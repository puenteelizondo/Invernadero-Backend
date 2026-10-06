import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

# django_asgi_app debe crearse ANTES de importar routing/consumers:
# get_asgi_application() es lo que inicializa el registro de apps de
# Django, y los consumers hacen consultas al ORM (Sensor, Actuator).
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402
from django.conf import settings  # noqa: E402

from apps.control.routing import websocket_urlpatterns as control_ws  # noqa: E402
from apps.realtime.routing import websocket_urlpatterns  # noqa: E402

# Navegadores (/ws/greenhouses/<id>/) y dispositivos (/ws/device/) van por
# routers separados.
#
# AllowedHostsOriginValidator exige el header Origin y lo compara con
# ALLOWED_HOSTS. Todo navegador lo manda solo; herramientas de prueba como
# Postman o wscat no, porque Origin es un concepto exclusivo de navegadores.
# En desarrollo (DEBUG=True) se omite para poder probar con esas herramientas;
# en producción (DEBUG=False) se aplica siempre a las rutas de navegador.
#
# Al WebSocket de DISPOSITIVOS no se le aplica nunca: un ESP32 no manda Origin
# (no es un navegador) y su conexión no usa cookies de sesión, sino un token de
# un solo uso que solo se obtiene con la X-Device-Key. Ahí el chequeo de Origin
# no protege nada y solo dejaría fuera al hardware en producción.
browser_ws = URLRouter(websocket_urlpatterns)
if not settings.DEBUG:
    browser_ws = AllowedHostsOriginValidator(browser_ws)
device_ws = URLRouter(control_ws)


async def websocket_router(scope, receive, send):
    if scope["path"].startswith("/ws/device/"):
        return await device_ws(scope, receive, send)
    return await browser_ws(scope, receive, send)


application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": websocket_router,
})