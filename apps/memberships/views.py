from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied

from .models import Membership
from .serializers import MembershipSerializer


class MembershipViewSet(viewsets.ModelViewSet):
    """
    Quién tiene acceso a qué invernadero. Es como un Owner invita a
    alguien más (ej. un Operator que puede controlar actuadores, o un
    Viewer que solo puede ver) — sin necesitar intervención de un
    admin. Un usuario que no es Owner del invernadero en cuestión (ni
    staff) no puede ver ni modificar sus membresías.
    """
    serializer_class = MembershipSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Membership.objects.select_related("user", "greenhouse")
        if user.is_staff:
            return qs
        owned_greenhouse_ids = Membership.objects.filter(
            user=user, role=Membership.Role.OWNER
        ).values_list("greenhouse_id", flat=True)
        return qs.filter(greenhouse_id__in=owned_greenhouse_ids)

    def _require_owner_of(self, greenhouse):
        user = self.request.user
        if user.is_staff:
            return
        is_owner = Membership.objects.filter(
            user=user, greenhouse=greenhouse, role=Membership.Role.OWNER
        ).exists()
        if not is_owner:
            raise PermissionDenied("Solo el propietario puede administrar los miembros.")

    def perform_create(self, serializer):
        greenhouse = serializer.validated_data["greenhouse"]
        self._require_owner_of(greenhouse)
        serializer.save()

    def perform_destroy(self, instance):
        self._require_owner_of(instance.greenhouse)
        instance.delete()