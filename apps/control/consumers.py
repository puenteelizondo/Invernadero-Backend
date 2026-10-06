import asyncio
import json
import math
import time
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone

from apps.common.realtime import publish_event

from . import services
from .tokens import consume_device_ws_token

PING_EVERY = 25          # s entre ping y ping
IDLE_LIMIT = 75          # s sin recibir NADA => se cierra la conexión
TELEMETRY_MIN_GAP = 0.5  # s: el servidor retransmite como máximo 2 mensajes/s por lazo
MAX_MESSAGE_BYTES = 4096


def _num(value):
    """float finito o None (descarta NaN/inf/strings)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


class DeviceConsumer(AsyncWebsocketConsumer):
    """
    WebSocket del DISPOSITIVO: /ws/device/?token=<token de POST /api/v1/devices/ws-token/>

    servidor -> dispositivo:  config | config_update | config_remove | ping
    dispositivo -> servidor:  ack | telemetry | pong | hello

    El dispositivo solo recibe SUS lazos y solo puede confirmar/reportar sobre
    ellos. La telemetría se guarda únicamente en caché (Redis) y se retransmite
    al grupo del invernadero; nunca se guarda como Reading.
    """

    async def connect(self):
        query = parse_qs(self.scope["query_string"].decode())
        token = query.get("token", [None])[0]
        device = await self._authenticate(token)
        if device is None:
            await self.close(code=4401)
            return

        self.device_id, self.greenhouse_id = device
        self.group = services.device_group(self.device_id)
        self.loops = {}            # loop_id -> {"actuator_id": ...}
        self._last_fwd = {}        # loop_id -> monotonic del último reenvío
        self._last_on = {}         # loop_id -> último estado ON/OFF derivado de la salida
        self._last_rx = time.monotonic()
        self._last_seen_write = 0.0
        self._joined = False
        self._ping_task = None

        await self.channel_layer.group_add(self.group, self.channel_name)
        self._joined = True
        await self.accept()
        services.mark_online(self.device_id, self.channel_name)
        await self._announce(True)

        payloads = await self._load_loops()
        await self.send_json({"event": "config", "loops": payloads})
        self._ping_task = asyncio.ensure_future(self._keepalive())

    async def disconnect(self, code):
        if self._ping_task is not None:
            self._ping_task.cancel()
        if getattr(self, "_joined", False):
            await self.channel_layer.group_discard(self.group, self.channel_name)
            if services.mark_offline(self.device_id, self.channel_name):
                await self._announce(False)

    # ---- servidor -> dispositivo (llega por el grupo) ------------------------
    async def device_message(self, message):
        event, data = message["event"], message["data"]
        if event == "config_update":
            loop = data["loop"]
            self.loops[loop["id"]] = {"actuator_id": loop["actuator_id"]}
        elif event == "config_remove":
            self.loops.pop(data["loop_id"], None)
        await self.send_json({"event": event, **data})

    async def send_json(self, obj):
        await self.send(text_data=json.dumps(obj))

    # ---- dispositivo -> servidor ----------------------------------------------
    async def receive(self, text_data=None, bytes_data=None):
        if not text_data or len(text_data) > MAX_MESSAGE_BYTES:
            return
        try:
            msg = json.loads(text_data)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return

        self._last_rx = time.monotonic()
        services.touch_online(self.device_id)
        await self._touch_last_seen()

        event = msg.get("event")
        if event == "ack":
            await self._on_ack(msg)
        elif event == "telemetry":
            await self._on_telemetry(msg)
        # "pong" y "hello" solo sirven para mantener viva la conexión.

    async def _on_ack(self, msg):
        loop_id, version = msg.get("loop_id"), msg.get("version")
        if not isinstance(loop_id, int) or not isinstance(version, int) or loop_id not in self.loops:
            return
        payload = await self._save_ack(loop_id, version)
        if payload is not None:
            await database_sync_to_async(publish_event)(
                self.greenhouse_id, "control_loop_applied", payload
            )

    async def _on_telemetry(self, msg):
        loop_id = msg.get("loop_id")
        if not isinstance(loop_id, int) or loop_id not in self.loops:
            return
        now = time.monotonic()
        if now - self._last_fwd.get(loop_id, 0.0) < TELEMETRY_MIN_GAP:
            return
        self._last_fwd[loop_id] = now

        data = {
            "loop_id": loop_id,
            "greenhouse_id": self.greenhouse_id,
            "device_id": self.device_id,
            "pv": _num(msg.get("pv")),
            "setpoint": _num(msg.get("setpoint")),
            "output": _num(msg.get("output")),
            "error": _num(msg.get("error")),
            "p": _num(msg.get("p")),
            "i": _num(msg.get("i")),
            "d": _num(msg.get("d")),
            "mode": str(msg.get("mode", ""))[:10],
            "version": msg.get("version") if isinstance(msg.get("version"), int) else None,
            "ts": timezone.now().isoformat(),
        }
        services.set_last_telemetry(loop_id, data)
        await database_sync_to_async(publish_event)(self.greenhouse_id, "control_telemetry", data)

        # El estado ON/OFF del actuador en la plataforma sigue a la salida del lazo
        # (solo cuando cruza el umbral, no en cada muestra).
        out = data["output"]
        if out is not None:
            on = out > 1.0
            if self._last_on.get(loop_id) != on:
                self._last_on[loop_id] = on
                await self._sync_actuator(self.loops[loop_id]["actuator_id"], on)

    # ---- tareas ------------------------------------------------------------------
    async def _keepalive(self):
        try:
            while True:
                await asyncio.sleep(PING_EVERY)
                if time.monotonic() - self._last_rx > IDLE_LIMIT:
                    await self.close(code=4408)
                    return
                await self.send_json({"event": "ping"})
        except asyncio.CancelledError:
            pass

    # ---- base de datos ------------------------------------------------------------
    @database_sync_to_async
    def _authenticate(self, token):
        from apps.sensors.models import Device

        if not token:
            return None
        device_id = consume_device_ws_token(token)
        if device_id is None:
            return None
        row = Device.objects.filter(pk=device_id, is_active=True).values_list("pk", "greenhouse_id").first()
        return row

    @database_sync_to_async
    def _load_loops(self):
        from .models import ControlLoop

        loops = list(ControlLoop.objects.filter(device_id=self.device_id))
        self.loops = {l.pk: {"actuator_id": l.actuator_id} for l in loops}
        return [l.device_payload() for l in loops]

    @database_sync_to_async
    def _save_ack(self, loop_id, version):
        from .models import ControlLoop

        updated = (
            ControlLoop.objects.filter(pk=loop_id, device_id=self.device_id, version__gte=version)
            .exclude(applied_version__gte=version)
            .update(applied_version=version, applied_at=timezone.now())
        )
        if not updated:
            return None
        loop = ControlLoop.objects.get(pk=loop_id)
        return {
            "loop_id": loop.pk,
            "greenhouse_id": loop.greenhouse_id,
            "version": loop.version,
            "applied_version": loop.applied_version,
            "applied_at": loop.applied_at.isoformat(),
        }

    @database_sync_to_async
    def _touch_last_seen(self):
        from apps.sensors.models import Device

        now = time.monotonic()
        if now - self._last_seen_write < 30:
            return
        self._last_seen_write = now
        Device.objects.filter(pk=self.device_id).update(last_seen_at=timezone.now())

    @database_sync_to_async
    def _sync_actuator(self, actuator_id, on):
        from apps.actuators.models import Actuator

        actuator = Actuator.objects.filter(pk=actuator_id, greenhouse_id=self.greenhouse_id).first()
        if actuator is not None:
            actuator.set_state(on, source="automation")

    @database_sync_to_async
    def _announce(self, online):
        publish_event(
            self.greenhouse_id, "device_connection", {"device_id": self.device_id, "online": online}
        )
