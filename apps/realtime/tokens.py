import secrets

from django.core.cache import cache

WS_TOKEN_TTL_SECONDS = 30
_PREFIX = "ws_token:"


def issue_ws_token(user) -> str:
    """
    Genera un token corto, de un solo uso, que autoriza UNA conexión de
    WebSocket para el usuario dado.

    Vive en Redis (el mismo backend de caché que ya usa el proyecto para
    la política de persistencia de lecturas), no en Postgres: es un dato
    puramente efímero que se consume en segundos y nunca se vuelve a
    consultar después de la conexión.

    Por qué un token aparte en vez de reusar la sesión o Basic Auth
    directamente: un WebSocket del navegador no puede mandar el header
    Authorization como un fetch normal, y aunque pudiera, no queremos
    que la contraseña (o el hash de sesión) viaje en la URL de conexión.
    Este token es de un solo uso y expira solo, así que aunque quede en
    algún log de acceso no sirve para nada pasados unos segundos.
    """
    token = secrets.token_urlsafe(32)
    cache.set(f"{_PREFIX}{token}", user.id, timeout=WS_TOKEN_TTL_SECONDS)
    return token


def consume_ws_token(token: str):
    """
    Valida y CONSUME un token: si existe, lo borra de inmediato y
    devuelve el id del usuario asociado; si no existe (nunca se emitió,
    ya expiró, o ya se usó una vez), devuelve None.

    Se borra aquí mismo, antes de devolver el resultado, para que dos
    intentos de conexión simultáneos con el mismo token no puedan
    "colarse" ambos mientras los dos lo validan a la vez.
    """
    key = f"{_PREFIX}{token}"
    user_id = cache.get(key)
    if user_id is not None:
        cache.delete(key)
    return user_id
