from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone


def publish_event(greenhouse_id, event: str, payload: dict) -> None:
    """
    Publica un evento al grupo de WebSocket del invernadero indicado.

    Único punto del backend que conoce el channel layer. Cualquier
    app (actuators, readings, y las que vengan: alerts, automation)
    llama a esto sin saber nada de grupos ni del formato interno de
    Channels.

    Sobre de evento consistente, definido al diseñar los actuadores:
        {"event": "...", "timestamp": "...", "payload": {...}}
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    async_to_sync(channel_layer.group_send)(
        f"greenhouse_{greenhouse_id}",
        {
            "type": "broadcast_event",
            "event": event,
            "timestamp": timezone.now().isoformat(),
            "payload": payload,
        },
    )