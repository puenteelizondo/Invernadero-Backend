from rest_framework.exceptions import PermissionDenied

from .models import Membership
from .scoping import visible_greenhouse_ids


class GreenhouseScopedMixin:
    """
    Mixin para ViewSets cuyos objetos pertenecen a un invernadero.
    Dos responsabilidades:

    1. get_queryset(): filtra a lo que el usuario puede VER (cualquier
       rol). Se combina con greenhouse_field, que dice cómo llegar del
       modelo al invernadero en sintaxis de filtro de Django
       ("greenhouse_id" para Sensor/Actuator/Device/Zone,
       "sensor__greenhouse_id" para Reading).

    2. perform_create() / _require_owner(): antes de CREAR algo nuevo
       dentro de un invernadero, exige que el usuario sea su Owner —
       si no, un Operator o Viewer de un invernadero podría, por
       ejemplo, intentar registrar un sensor nuevo ahí sin tener
       permiso real de administrarlo.
    """
    greenhouse_field = "greenhouse_id"

    def get_queryset(self):
        ids = visible_greenhouse_ids(self.request.user)
        qs = super().get_queryset()
        if ids is None:
            return qs
        return qs.filter(**{f"{self.greenhouse_field}__in": ids})

    def _require_owner(self, greenhouse_id):
        user = self.request.user
        if user.is_staff:
            return
        is_owner = Membership.objects.filter(
            user=user, greenhouse_id=greenhouse_id, role=Membership.Role.OWNER
        ).exists()
        if not is_owner:
            raise PermissionDenied(
                "Debes ser el propietario (owner) de este invernadero."
            )

    def perform_create(self, serializer):
        greenhouse = serializer.validated_data["greenhouse"]
        self._require_owner(greenhouse.id)
        serializer.save()