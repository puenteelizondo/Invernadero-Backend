from rest_framework.routers import DefaultRouter

from .views import ActuatorTypeViewSet, ActuatorViewSet

router = DefaultRouter()
router.register("actuator-types", ActuatorTypeViewSet)
router.register("actuators", ActuatorViewSet)

urlpatterns = router.urls