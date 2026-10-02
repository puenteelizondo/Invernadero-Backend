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

def publish_events(items) -> None:
    """
    Publica varios eventos con UN solo viaje al event loop.

    `items` es una lista de (greenhouse_id, event, payload). Es lo mismo que
    llamar a `publish_event` en bucle, pero con un solo `async_to_sync`: con lotes grandes, abrir un event loop por
    cada lectura era de lo más caro de la ingesta.
    """
    items = list(items)
    channel_layer = get_channel_layer()
    if channel_layer is None or not items:
        return

    async def _send_all():
        # En serie, NO con gather: channels_redis tiene un pool de conexiones
        # acotado y lanzar decenas de group_send a la vez lo desborda
        # ("Too many connections") cuando hay varios dispositivos a la vez.
        for gid, event, payload in items:
            await channel_layer.group_send(
                f"greenhouse_{gid}",
                {
                    "type": "broadcast_event",
                    "event": event,
                    "timestamp": timezone.now().isoformat(),
                    "payload": payload,
                },
            )

    async_to_sync(_send_all)()
