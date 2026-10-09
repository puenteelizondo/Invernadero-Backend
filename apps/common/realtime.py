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

    # Un solo mensaje por invernadero con todos sus eventos (antes, uno por
    # evento): el navegador los sigue recibiendo uno por uno, igual que antes
    # (ver GreenhouseConsumer.broadcast_events), pero Redis hace 1 envío en vez de N.
    now = timezone.now().isoformat()
    by_gh = {}
    for gid, event, payload in items:
        by_gh.setdefault(gid, []).append({"event": event, "timestamp": now, "payload": payload})

    async def _send_all():
        # En serie, NO con gather: channels_redis tiene un pool de conexiones
        # acotado y lanzar decenas de group_send a la vez lo desborda
        # ("Too many connections") cuando hay varios dispositivos a la vez.
        for gid, evs in by_gh.items():
            if len(evs) == 1:
                msg = {"type": "broadcast_event", **evs[0]}
            else:
                msg = {"type": "broadcast_events", "events": evs}
            await channel_layer.group_send(f"greenhouse_{gid}", msg)

    async_to_sync(_send_all)()
