import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone


class GreenhouseConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.greenhouse_id = self.scope["url_route"]["kwargs"]["greenhouse_id"]
        self.group_name = f"greenhouse_{self.greenhouse_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        # Etapa 9: al conectar, se manda un "snapshot" con el último
        # valor conocido de cada sensor y el estado actual de cada
        # actuador del invernadero, para que el cliente no arranque en
        # blanco hasta que ocurra el primer evento nuevo. Usa el mismo
        # sobre {event, timestamp, payload} que los demás eventos, con
        # event="snapshot", para que el frontend no necesite un caso
        # especial: solo revisa el nombre del evento.
        snapshot = await self._build_snapshot()
        await self.send(text_data=json.dumps({
            "event": "snapshot",
            "timestamp": timezone.now().isoformat(),
            "payload": snapshot,
        }))

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def broadcast_event(self, event):
        await self.send(text_data=json.dumps({
            "event": event["event"],
            "timestamp": event["timestamp"],
            "payload": event["payload"],
        }))

    async def receive(self, text_data=None, bytes_data=None):
        pass

    @database_sync_to_async
    def _build_snapshot(self):
        """
        Arma el snapshot. Va decorado con @database_sync_to_async
        porque, a diferencia de connect()/disconnect()/broadcast_event()
        (que solo usan el channel_layer, que sí es async-nativo), este
        método hace consultas al ORM de Django y al cache de Django —
        ambos son código SÍNCRONO. database_sync_to_async ejecuta esta
        función en un hilo aparte del event loop, para no bloquear al
        resto de conexiones WebSocket mientras Postgres/Redis responden.
        Corre una sola vez, al conectar — no en cada mensaje.
        """
        from apps.actuators.models import Actuator
        from apps.readings.persistence import get_latest
        from apps.sensors.models import Sensor

        sensors = Sensor.objects.filter(
            greenhouse_id=self.greenhouse_id, is_active=True
        ).select_related("sensor_type")

        sensor_data = []
        for sensor in sensors:
            latest = get_latest(sensor.id)
            sensor_data.append({
                "sensor_id": sensor.id,
                "sensor_name": sensor.name,
                "sensor_type": sensor.sensor_type.code,
                "unit": sensor.get_unit(),
                "value": latest["value"] if latest else None,
                "timestamp": latest["timestamp"].isoformat() if latest else None,
            })

        actuators = Actuator.objects.filter(
            greenhouse_id=self.greenhouse_id, is_active=True
        )
        actuator_data = [
            {
                "actuator_id": actuator.id,
                "name": actuator.name,
                "state": actuator.state,
            }
            for actuator in actuators
        ]

        return {"sensors": sensor_data, "actuators": actuator_data}