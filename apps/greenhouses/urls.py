from rest_framework.routers import DefaultRouter

from .views import GreenhouseViewSet, ZoneViewSet

router = DefaultRouter()
router.register("greenhouses", GreenhouseViewSet)
router.register("zones", ZoneViewSet)

urlpatterns = router.urls