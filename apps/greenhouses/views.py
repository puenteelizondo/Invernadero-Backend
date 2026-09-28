from rest_framework import viewsets

from apps.memberships.mixins import GreenhouseScopedMixin
from apps.memberships.models import Membership
from apps.memberships.permissions import IsGreenhouseMember
from apps.memberships.scoping import visible_greenhouse_ids

from .models import Greenhouse, Zone
from .serializers import GreenhouseSerializer, ZoneSerializer


class GreenhouseViewSet(viewsets.ModelViewSet):
    # El router de DRF necesita este atributo de clase para inferir el
    # basename de la ruta automáticamente (router.register sin
    # basename explícito) — get_queryset() abajo es el que de verdad
    # se usa en cada petición; esto es solo metadata estática.
    queryset = Greenhouse.objects.all()
    serializer_class = GreenhouseSerializer
    permission_classes = [IsGreenhouseMember]
    filterset_fields = ["is_active"]
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]

    def get_queryset(self):
        # No usa GreenhouseScopedMixin porque aquí el invernadero ES
        # el objeto (no tiene un campo .greenhouse propio) — se filtra
        # por su propio id, no por un FK.
        ids = visible_greenhouse_ids(self.request.user)
        qs = Greenhouse.objects.all()
        if ids is None:
            return qs
        return qs.filter(id__in=ids)

    def perform_create(self, serializer):
        # Etapa 12: quien registra un invernadero se vuelve
        # automáticamente su Owner. Es la pieza que permite que cada
        # cliente registre y administre el suyo desde el futuro
        # frontend, sin que un admin tenga que asignarlo a mano.
        greenhouse = serializer.save()
        Membership.objects.create(
            user=self.request.user, greenhouse=greenhouse, role=Membership.Role.OWNER
        )


class ZoneViewSet(GreenhouseScopedMixin, viewsets.ModelViewSet):
    serializer_class = ZoneSerializer
    permission_classes = [IsGreenhouseMember]
    filterset_fields = ["greenhouse"]
    queryset = Zone.objects.select_related("greenhouse").all()