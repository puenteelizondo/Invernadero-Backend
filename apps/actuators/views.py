from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from apps.memberships.mixins import GreenhouseScopedMixin
from apps.memberships.permissions import IsGreenhouseMember, IsGreenhouseOperatorOrAbove

from .models import Actuator, ActuatorType
from .serializers import (
    ActuatorSerializer,
    ActuatorStateChangeSerializer,
    ActuatorStateHistorySerializer,
    ActuatorTypeSerializer,
)


class ActuatorTypeViewSet(viewsets.ModelViewSet):
    """
    Catálogo GLOBAL (igual que SensorType): lectura abierta a
    cualquier autenticado, escritura solo para staff.
    """
    queryset = ActuatorType.objects.all()
    serializer_class = ActuatorTypeSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAdminUser()]
        return super().get_permissions()


class ActuatorViewSet(GreenhouseScopedMixin, viewsets.ModelViewSet):
    serializer_class = ActuatorSerializer
    permission_classes = [IsGreenhouseMember]
    filterset_fields = ["greenhouse", "zone", "actuator_type", "is_active", "state"]
    search_fields = ["name"]
    queryset = Actuator.objects.select_related(
        "actuator_type", "greenhouse", "zone", "device"
    ).all()

    def get_permissions(self):
        # Etapa 12: reemplaza el placeholder CanControlActuators
        # (is_staff) por el rol real dentro del invernadero — Owner u
        # Operator pueden controlar; Viewer solo puede ver.
        if self.action == "state":
            return [IsGreenhouseOperatorOrAbove()]
        return super().get_permissions()

    @action(detail=True, methods=["post"])
    def state(self, request, pk=None):
        actuator = self.get_object()
        serializer = ActuatorStateChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        changed = actuator.set_state(
            serializer.validated_data["state"],
            user=request.user,
            source="manual",
        )

        return Response(
            {
                "actuator_id": actuator.id,
                "name": actuator.name,
                "state": actuator.state,
                "changed": changed,
                "updated_at": timezone.now(),
            }
        )

    @action(detail=True, methods=["get"], url_path="history")
    def history(self, request, pk=None):
        actuator = self.get_object()
        qs = actuator.state_history.select_related("changed_by")[:100]
        serializer = ActuatorStateHistorySerializer(qs, many=True)
        return Response(serializer.data)