import asyncio
import logging
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

logger = logging.getLogger(__name__)

PING_EVERY = 25          # s entre ping y ping
IDLE_LIMIT = 75          # s sin recibir NADA => se cierra la conexión
TELEMETRY_MIN_GAP = 0.5  # s: el servidor retransmite como máximo 2 mensajes/s por lazo
MAX_MESSAGE_BYTES = 4096
# Lecturas: el límite es POR DISPOSITIVO y compartido con la ingesta HTTP
# (apps/readings/ingest.py::within_device_limit): 30 cada 3 s.
READINGS_PER_SEC = 10
MAX_READINGS_PER_MESSAGE = 20
RESULT_NOTICE_GAP = 5.0  # s entre avisos de "lecturas rechazadas" al dispositivo


MAX_ID = 2**63 - 1


def _num(value):
    """float finito o None (descarta NaN/inf/strings y números gigantes)."""
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _id(value):
    """Entero positivo que cabe en la base de datos, o None."""
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= MAX_ID else None


class DeviceConsumer(AsyncWebsocketConsumer):
    """
    WebSocket del DISPOSITIVO: /ws/device/?token=<token de POST /api/v1/devices/ws-token/>

    servidor -> dispositivo:  config | config_update | config_remove | ping
                              actuators (al conectar) | actuator_state | readings_result
    dispositivo -> servidor:  ack | telemetry | readings | pong | hello

    El dispositivo solo recibe SUS lazos y SUS actuadores, y solo puede
    confirmar/reportar sobre ellos. La telemetría se guarda únicamente en
    caché (Redis) y se retransmite al grupo del invernadero; nunca se guarda
    como Reading.

    "readings" pasa por la MISMA ingesta que POST /api/v1/readings/ingest/
    (apps/readings/ingest.py): mismas validaciones (solo sensores de este
    dispositivo, rango válido), alertas y regla de "guardar cada N".
    """

    async def connect(self):
        query = parse_qs(self.scope["query_string"].decode())
        token = query.get("token", [None])[0]
        device = await self._authenticate(token)
        if device is None:
            await self.close(code=4401)
            return

        self.device_id, self.greenhouse_id, self._key_hash = device
        self.group = services.device_group(self.device_id)
        self.loops = {}            # loop_id -> {"actuator_id": ...}
        self._last_fwd = {}        # loop_id -> monotonic del último reenvío
        self._last_on = {}         # loop_id -> último estado ON/OFF derivado de la salida
        self._last_rx = time.monotonic()
        self._last_seen_write = 0.0
        self._joined = False
        self._ping_task = None
        self._last_notice = 0.0
        self._last_online_touch = time.monotonic()

        await self.channel_layer.group_add(self.group, self.channel_name)
        self._joined = True
        # Segunda revisión YA dentro del grupo: si regeneraron la clave o
        # desactivaron el dispositivo entre la primera y aquí, el aviso de
        # cierre pudo perderse; así no se cuela.
        if await self._authenticate_again(device) is None:
            await self.close(code=4401)
            return
        await self.accept()
        services.mark_online(self.device_id, self.channel_name)
        await self._announce(True)

        payloads = await self._load_loops()
        await self.send_json({"event": "config", "loops": payloads})
        # Estado actual de los actuadores de este dispositivo: el que maneja un
        # actuador "a mano" (sin lazo) se pone al día en cuanto se conecta.
        await self.send_json({"event": "actuators", "actuators": await self._load_actuators()})
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

    async def device_close(self, message):
        """El servidor pidió cerrar (clave regenerada, o dispositivo desactivado o borrado)."""
        if message.get("except_channel") == self.channel_name:
            return
        await self.close(code=4403)

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

        now = time.monotonic()
        self._last_rx = now
        # "En línea" dura 90 s en la caché: basta renovarlo cada 15 s (antes, en
        # cada mensaje, con 2 viajes a Redis que además frenaban a todos).
        if now - self._last_online_touch > 15:
            self._last_online_touch = now
            await database_sync_to_async(services.touch_online, thread_sensitive=False)(self.device_id)
        if now - self._last_seen_write >= 30:
            await self._touch_last_seen()

        event = msg.get("event")
        try:
            if event == "ack":
                await self._on_ack(msg)
            elif event == "telemetry":
                await self._on_telemetry(msg)
            elif event == "readings":
                await self._on_readings(msg)
        except Exception:  # un mensaje raro nunca debe tumbar la conexión ni dejar basura
            logger.exception("Mensaje del dispositivo %s ignorado", self.device_id)
        # "pong" y "hello" solo sirven para mantener viva la conexión.

    async def _on_readings(self, msg):
        items = msg.get("readings")
        if not isinstance(items, list) or not items:
            return
        items = items[:MAX_READINGS_PER_MESSAGE]

        # Solo objetos con un sensor_id razonable; lo demás ni siquiera llega a la base.
        items = [
            i if isinstance(i, dict) and _id(i.get("sensor_id"))
            else {"sensor_id": None, "value": i.get("value") if isinstance(i, dict) else None}
            for i in items
        ]
        result = await self._ingest(items)
        if result is None:
            await self._notice({"error": "rate_limited",
                                "detail": f"Demasiadas lecturas: máximo {READINGS_PER_SEC} por segundo por dispositivo."})
            return
        rejected = [r for r in result["results"] if r["status"] != "accepted"]
        if rejected:
            await self._notice({"rejected": [
                {"sensor_id": items[r["index"]].get("sensor_id") if isinstance(items[r["index"]], dict) else None,
                 "errors": r.get("errors")}
                for r in rejected[:5]
            ]})

    async def _notice(self, data):
        """Avisa al dispositivo (para el monitor serie), sin inundarlo: 1 aviso cada pocos segundos."""
        now = time.monotonic()
        if now - self._last_notice < RESULT_NOTICE_GAP:
            return
        self._last_notice = now
        await self.send_json({"event": "readings_result", **data})

    async def _on_ack(self, msg):
        loop_id, version = _id(msg.get("loop_id")), _id(msg.get("version"))
        if loop_id is None or version is None or loop_id not in self.loops:
            return
        payload = await self._save_ack(loop_id, version)
        if payload is not None:
            await database_sync_to_async(publish_event)(
                self.greenhouse_id, "control_loop_applied", payload
            )

    async def _on_telemetry(self, msg):
        from django.core.cache import cache

        loop_id = _id(msg.get("loop_id"))
        if loop_id is None or loop_id not in self.loops:
            return
        now = time.monotonic()
        if now - self._last_fwd.get(loop_id, 0.0) < TELEMETRY_MIN_GAP:
            return
        self._last_fwd[loop_id] = now
        # El mismo tope, pero compartido entre todas las conexiones del dispositivo.
        if not await cache.aadd(f"ctrl:tmgap:{loop_id}:{int(time.time() / TELEMETRY_MIN_GAP)}", 1, 5):
            return

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
            "version": _id(msg.get("version")),
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
        data = consume_device_ws_token(token)
        if data is None:
            return None
        row = (Device.objects.filter(pk=data["id"], is_active=True)
               .values_list("pk", "greenhouse_id", "api_key_hash").first())
        if row is None or (data["key"] is not None and data["key"] != row[2]):
            return None          # la clave cambió después de pedir el token
        return row

    @database_sync_to_async
    def _authenticate_again(self, device):
        from apps.sensors.models import Device

        return Device.objects.filter(pk=device[0], is_active=True, api_key_hash=device[2]).values_list("pk").first()

    @database_sync_to_async
    def _load_loops(self):
        from .models import ControlLoop

        loops = list(ControlLoop.objects.filter(device_id=self.device_id))
        self.loops = {l.pk: {"actuator_id": l.actuator_id} for l in loops}
        return [l.device_payload() for l in loops]

    @database_sync_to_async
    def _load_actuators(self):
        from apps.actuators.models import Actuator

        return [
            {"id": pk, "state": state}
            for pk, state in Actuator.objects.filter(device_id=self.device_id, is_active=True)
            .order_by("pk").values_list("pk", "state")
        ]

    async def _ingest(self, items):
        # thread_sensitive=False: las lecturas de distintos ESP32 se procesan en
        # paralelo (mientras uno espera a Postgres/Redis, otro avanza). Antes todas
        # iban en fila en un solo hilo y con ~50 ESP32 la página se atrasaba segundos.
        return await database_sync_to_async(self._ingest_sync, thread_sensitive=False)(items)

    def _ingest_sync(self, items):
        from apps.readings.ingest import ingest_readings, within_device_limit
        from apps.sensors.models import Device

        if not within_device_limit(self.device_id, len(items)):
            return None
        device = Device.objects.filter(pk=self.device_id, is_active=True).first()
        if device is None:      # lo desactivaron mientras estaba conectado
            return {"accepted": 0, "persisted": 0, "rejected": len(items),
                    "results": [{"index": i, "status": "rejected", "errors": "Dispositivo inactivo."}
                                for i in range(len(items))]}
        return ingest_readings(device, items)

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
