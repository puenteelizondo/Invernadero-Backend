import secrets

from django.core.cache import cache

DEVICE_WS_TOKEN_TTL = 30
_PREFIX = "dev_ws_token:"


def issue_device_ws_token(device) -> str:
    """Token corto y de un solo uso para abrir UN WebSocket de dispositivo."""
    token = secrets.token_urlsafe(32)
    cache.set(f"{_PREFIX}{token}", device.pk, timeout=DEVICE_WS_TOKEN_TTL)
    return token


def consume_device_ws_token(token: str):
    key = f"{_PREFIX}{token}"
    device_id = cache.get(key)
    if device_id is not None:
        cache.delete(key)
    return device_id
