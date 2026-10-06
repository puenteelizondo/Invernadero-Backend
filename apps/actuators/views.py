from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.types import TypeCatalogMixin
from apps.memberships.mixins import GreenhouseScopedMixin
from apps.memberships.permissions import IsGreenhouseMember, IsGreenhouseOperatorOrAbove

from .models import Actuator, ActuatorType
from .serializers import (
    ActuatorSerializer,
    ActuatorStateChangeSerializer,
    ActuatorStateHistorySerializer,
    ActuatorTypeSerializer,
)


class ActuatorTypeViewSet(TypeCatalogMixin, viewsets.ModelViewSet):
    """
    Catálogo de tipos de actuador, con los mismos dos ámbitos que
    SensorType: globales (staff) y propios de un invernadero (su Owner).
    """
    queryset = ActuatorType.objects.select_related("greenhouse").all()
    serializer_class = ActuatorTypeSerializer
    filterset_fields = ["greenhouse"]


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

        # Si un lazo de control ACTIVO maneja este actuador, el ESP32 decide la
        # salida: un encendido manual no llegaría al hardware y solo dejaría un
        # estado falso en la web. Se rechaza con 409 y se dice qué lazo lo tiene.
        from apps.control.models import ControlLoop

        loop = (
            ControlLoop.objects.filter(actuator=actuator, enabled=True)
            .exclude(mode=ControlLoop.Mode.OFF)
            .first()
        )
        if loop is not None:
            return Response(
                {
                    "detail": (
                        f"Este actuador lo controla automáticamente el lazo “{loop.name}”. "
                        "Para manejarlo a mano, pon ese lazo en Apagado en la página Control."
                    ),
                    "control_loop": loop.pk,
                },
                status=409,
            )

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

    @action(detail=True, methods=["post"], url_path="purge")
    def purge(self, request, pk=None):
        """
        POST /api/v1/actuators/{id}/purge/   body: {"confirm_name": "<nombre exacto>"}

        Borrado EXPLÍCITO e irreversible de un actuador junto con todo su
        historial de cambios de estado. El DELETE normal se niega si hay
        historial (ActuatorStateHistory.actuator es PROTECT); esta acción
        sirve para limpiar actuadores de prueba o creados por error.

        Solo el Owner del invernadero (get_object() aplica
        IsGreenhouseMember: un POST exige rol Owner) y hay que mandar el
        nombre exacto del actuador como confirmación.
        """
        actuator = self.get_object()
        confirm = request.data.get("confirm_name") if hasattr(request.data, "get") else None
        if not isinstance(confirm, str) or confirm.strip() != actuator.name:
            raise serializers.ValidationError(
                {"confirm_name": "Escribe el nombre exacto del actuador para confirmar."}
            )

        actuator_id, name = actuator.id, actuator.name
        with transaction.atomic():
            deleted, _ = actuator.state_history.all().delete()
            actuator.delete()

        return Response({"actuator_id": actuator_id, "name": name, "history_deleted": deleted})
