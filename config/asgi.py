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

from apps.realtime.routing import websocket_urlpatterns  # noqa: E402

websocket_router = URLRouter(websocket_urlpatterns)

# AllowedHostsOriginValidator exige el header Origin y lo compara con
# ALLOWED_HOSTS. Todo navegador lo manda solo; herramientas de prueba
# como Postman o wscat no, porque Origin es un concepto exclusivo de
# navegadores. En desarrollo (DEBUG=True) no hay riesgo real de un
# origen malicioso probando contra tu propia máquina, así que se omite
# la validación para poder probar con estas herramientas. En
# producción (DEBUG=False, Etapa 14) se aplica siempre.
if not settings.DEBUG:
    websocket_router = AllowedHostsOriginValidator(websocket_router)

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": websocket_router,
})