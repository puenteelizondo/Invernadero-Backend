from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import ControlLoopViewSet, DeviceControlConfigView, DeviceWsTokenView

router = SimpleRouter()
router.register("control-loops", ControlLoopViewSet, basename="control-loop")

# IMPORTANTE: este include va ANTES que apps.sensors.urls en config/urls.py;
# si no, el router de `devices/<pk>/` se tragaría "ws-token" y "control-config".
urlpatterns = [
    path("devices/ws-token/", DeviceWsTokenView.as_view(), name="device-ws-token"),
    path("devices/control-config/", DeviceControlConfigView.as_view(), name="device-control-config"),
] + router.urls
