import json
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone


class GreenhouseConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.greenhouse_id = self.scope["url_route"]["kwargs"]["greenhouse_id"]
        self.group_name = f"greenhouse_{self.greenhouse_id}"

        # Autenticación/autorización del WebSocket: se exige un token
        # corto de un solo uso (POST /api/v1/realtime/ws-token/, ya
        # autenticado por HTTP) mandado como ?token=... en la URL de
        # conexión. Sin este paso, cualquiera que supiera o adivinara un
        # greenhouse_id podía conectarse y recibir sus lecturas/eventos
        # en tiempo real sin ninguna credencial.
        query = parse_qs(self.scope["query_string"].decode())
        token = query.get("token", [None])[0]

        user = await self._authenticate(token)
        if user is None:
            # Token ausente, inválido, expirado, o ya usado una vez.
            await self.close(code=4401)
            return

        if not await self._user_can_view(user):
            # Token válido (sabemos quién eres), pero no tienes
            # Membership en ESTE invernadero, ni eres staff.
            await self.close(code=4403)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        # Marca que sí llegamos a unirnos al grupo — disconnect() la usa
        # para no intentar salir de un grupo al que nunca entramos (ej.
        # cuando la conexión se rechazó por token inválido, arriba).
        self._joined_group = True

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
        if getattr(self, "_joined_group", False):
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

    @database_sync_to_async
    def _authenticate(self, token):
        """
        Cambia el token de un solo uso (emitido por
        POST /api/v1/realtime/ws-token/) por el User al que pertenece.
        Devuelve None si no vino token, o si consume_ws_token() no lo
        reconoce (nunca existió, ya expiró, o ya se usó).
        """
        from apps.users.models import User

        from .tokens import consume_ws_token

        if not token:
            return None

        user_id = consume_ws_token(token)
        if user_id is None:
            return None

        try:
            return User.objects.get(pk=user_id, is_active=True)
        except User.DoesNotExist:
            return None

    @database_sync_to_async
    def _user_can_view(self, user):
        """
        Mismo criterio que IsGreenhouseMember para lectura: cualquier
        rol de Membership en este invernadero alcanza (Owner, Operator
        o Viewer), y is_staff se trata como acceso a todo — igual que
        en el resto de la API (ver apps.memberships.scoping).
        """
        from apps.memberships.models import Membership

        if user.is_staff:
            return True
        return Membership.objects.filter(
            user=user, greenhouse_id=self.greenhouse_id
        ).exists()