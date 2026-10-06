"""
Piezas compartidas del control: presencia del dispositivo, última telemetría
(solo en caché/Redis) y envío de mensajes al dispositivo por su grupo de Channels.
"""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache
from django.utils import timezone

from apps.common.realtime import publish_event

ONLINE_TTL = 90          # s sin recibir nada del dispositivo => se considera caído
TELEMETRY_TTL = 120      # s que se conserva la última telemetría de un lazo


def device_group(device_id) -> str:
    return f"device_{device_id}"


# ---- presencia ------------------------------------------------------------
def mark_online(device_id, channel_name: str) -> None:
    cache.set(f"ctrl:conn:{device_id}", channel_name, ONLINE_TTL)


def touch_online(device_id) -> None:
    key = f"ctrl:conn:{device_id}"
    current = cache.get(key)
    if current is not None:
        cache.set(key, current, ONLINE_TTL)


def mark_offline(device_id, channel_name: str) -> bool:
    """Solo marca caído si esta conexión sigue siendo la vigente (reconexiones)."""
    key = f"ctrl:conn:{device_id}"
    if cache.get(key) == channel_name:
        cache.delete(key)
        return True
    return False


def is_online(device_id) -> bool:
    return cache.get(f"ctrl:conn:{device_id}") is not None


# ---- telemetría (nunca se guarda como Reading) -----------------------------
def set_last_telemetry(loop_id, data: dict) -> None:
    cache.set(f"ctrl:tm:{loop_id}", data, TELEMETRY_TTL)


def get_last_telemetry(loop_id):
    return cache.get(f"ctrl:tm:{loop_id}")


# ---- envío al dispositivo --------------------------------------------------
def push_to_device(device_id, event: str, **data) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        device_group(device_id),
        {"type": "device_message", "event": event, "data": data},
    )


def loop_event_payload(loop) -> dict:
    """Resumen público de un lazo para el grupo WebSocket del invernadero."""
    return {
        "loop_id": loop.pk,
        "greenhouse_id": loop.greenhouse_id,
        "device_id": loop.device_id,
        "name": loop.name,
        "sensor_id": loop.sensor_id,
        "actuator_id": loop.actuator_id,
        "mode": loop.mode,
        "direction": loop.direction,
        "setpoint": loop.setpoint,
        "hysteresis": loop.hysteresis,
        "kp": loop.kp,
        "ki": loop.ki,
        "kd": loop.kd,
        "output_min": loop.output_min,
        "output_max": loop.output_max,
        "integral_limit": loop.integral_limit,
        "sample_time_ms": loop.sample_time_ms,
        "enabled": loop.enabled,
        "version": loop.version,
        "applied_version": loop.applied_version,
        "applied_at": loop.applied_at.isoformat() if loop.applied_at else None,
        "updated_at": loop.updated_at.isoformat() if loop.updated_at else timezone.now().isoformat(),
        "device_online": is_online(loop.device_id),
    }


def publish_loop_changed(loop, old_device_id=None) -> None:
    """Un lazo se creó o cambió: avisa al dispositivo y a la web."""
    if old_device_id and old_device_id != loop.device_id:
        push_to_device(old_device_id, "config_remove", loop_id=loop.pk)
    push_to_device(loop.device_id, "config_update", loop=loop.device_payload())
    publish_event(loop.greenhouse_id, "control_loop_updated", loop_event_payload(loop))


def publish_loop_deleted(greenhouse_id, device_id, loop_id) -> None:
    push_to_device(device_id, "config_remove", loop_id=loop_id)
    publish_event(greenhouse_id, "control_loop_deleted", {"loop_id": loop_id, "greenhouse_id": greenhouse_id})
