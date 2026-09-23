from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Actuator, ActuatorType
from .permissions import CanControlActuators
from .serializers import (
    ActuatorSerializer,
    ActuatorStateChangeSerializer,
    ActuatorStateHistorySerializer,
    ActuatorTypeSerializer,
)


class ActuatorTypeViewSet(viewsets.ModelViewSet):
    queryset = ActuatorType.objects.all()
    serializer_class = ActuatorTypeSerializer


class ActuatorViewSet(viewsets.ModelViewSet):
    queryset = Actuator.objects.select_related(
        "actuator_type", "greenhouse", "zone", "device"
    ).all()
    serializer_class = ActuatorSerializer
    filterset_fields = ["greenhouse", "zone", "actuator_type", "is_active", "state"]
    search_fields = ["name"]

    def get_permissions(self):
        # Todo lo demás en este ViewSet hereda el permiso por defecto
        # (IsAuthenticated, ver settings). Solo la acción de control
        # exige además ser staff.
        if self.action == "state":
            return [CanControlActuators()]
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

        # TODO (Etapa 7): si changed es True, publicar el evento
        # actuator_state_changed al grupo de WebSocket del invernadero.
        # Por ahora solo respondemos por HTTP; el WebSocket se conecta
        # aquí mismo cuando implementemos Channels.

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