from rest_framework.routers import DefaultRouter

from .views import InvitationViewSet, MembershipViewSet

router = DefaultRouter()
router.register("memberships", MembershipViewSet, basename="membership")
router.register("invitations", InvitationViewSet, basename="invitation")

urlpatterns = router.urls
