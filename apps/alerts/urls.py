from rest_framework.routers import DefaultRouter

from .views import AlertRuleViewSet, AlertViewSet

router = DefaultRouter()
router.register("alert-rules", AlertRuleViewSet)
router.register("alerts", AlertViewSet)

urlpatterns = router.urls
