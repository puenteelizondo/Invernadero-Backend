from rest_framework.routers import DefaultRouter

from .views import DeviceViewSet, SensorTypeViewSet, SensorViewSet

router = DefaultRouter()
router.register("sensor-types", SensorTypeViewSet)
router.register("devices", DeviceViewSet)
router.register("sensors", SensorViewSet)

urlpatterns = router.urls