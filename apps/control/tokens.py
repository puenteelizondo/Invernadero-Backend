import secrets

from django.core.cache import cache

DEVICE_WS_TOKEN_TTL = 30
_PREFIX = "dev_ws_token:"


def issue_device_ws_token(device) -> str:
    """
    Token corto y de un solo uso para abrir UN WebSocket de dispositivo.

    Guarda también la huella de la clave con la que se pidió: si la clave se
    regenera antes de usar el token, el token ya no sirve (si no, quien tuviera
    la clave vieja podría pedir un token justo antes y conectarse después).
    """
    token = secrets.token_urlsafe(32)
    cache.set(f"{_PREFIX}{token}", {"id": device.pk, "key": device.api_key_hash}, timeout=DEVICE_WS_TOKEN_TTL)
    return token


def consume_device_ws_token(token: str):
    """Devuelve {"id", "key"} o None. De un solo uso aunque lleguen dos conexiones a la vez."""
    key = f"{_PREFIX}{token}"
    data = cache.get(key)
    if data is None or not cache.delete(key):   # solo quien logra borrarlo lo usa
        return None
    if isinstance(data, int):                    # token emitido antes de este cambio
        return {"id": data, "key": None}
    return data
