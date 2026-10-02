from django.utils import timezone
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.realtime import publish_events
from apps.memberships.mixins import GreenhouseScopedMixin
from apps.memberships.permissions import IsGreenhouseMember, IsGreenhouseOperatorOrAbove

from . import engine
from .models import Alert, AlertRule
from .serializers import AlertRuleSerializer, AlertSerializer


class AlertRuleViewSet(GreenhouseScopedMixin, viewsets.ModelViewSet):
    """Reglas de alerta por sensor. Leer: cualquier miembro. Crear/editar/borrar: Owner."""
    serializer_class = AlertRuleSerializer
    permission_classes = [IsGreenhouseMember]
    filterset_fields = ["greenhouse", "sensor", "is_active"]
    queryset = AlertRule.objects.select_related("sensor", "sensor__sensor_type", "active_alert").all()

    def perform_create(self, serializer):
        sensor = serializer.validated_data["sensor"]
        self._require_owner(sensor.greenhouse_id)
        serializer.save(greenhouse_id=sensor.greenhouse_id)

    def perform_update(self, serializer):
        rule = serializer.save()
        # Si se desactiva con una alerta abierta, se cierra (si no, quedaría activa para siempre).
        if not rule.is_active:
            event = engine.resolve_active(rule)
            if event:
                publish_events([event])

    def perform_destroy(self, instance):
        event = engine.resolve_active(instance)
        if event:
            publish_events([event])
        instance.delete()  # el historial de alertas se conserva (Alert.rule es SET_NULL)


class AlertViewSet(GreenhouseScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    Historial de alertas. Solo lectura; las crea y cierra la ingesta de lecturas.
    Filtros: ?greenhouse= ?status=active|resolved ?sensor= ?severity= ?kind=
    Reconocer una alerta ("ya la vi") la puede hacer Owner u Operator.
    """
    serializer_class = AlertSerializer
    filterset_fields = ["greenhouse", "status", "sensor", "severity", "kind"]
    queryset = Alert.objects.select_related("sensor", "sensor__sensor_type", "rule", "acknowledged_by").all()

    def get_permissions(self):
        if self.action == "acknowledge":
            return [IsGreenhouseOperatorOrAbove()]
        return [IsGreenhouseMember()]

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        alert = self.get_object()
        if alert.acknowledged_at is None:
            alert.acknowledged_at = timezone.now()
            alert.acknowledged_by = request.user
            alert.save(update_fields=["acknowledged_at", "acknowledged_by"])
            publish_events([(alert.greenhouse_id, "alert_acknowledged", {
                "alert_id": alert.id, "sensor_id": alert.sensor_id,
                "acknowledged_by": request.user.get_username(),
            })])
        return Response(self.get_serializer(alert).data)

    @action(detail=False, methods=["post"])
    def purge(self, request):
        """
        POST /api/v1/alerts/purge/   body: {"greenhouse": <id>, "older_than_days": <int, opcional>}

        Borra del historial las alertas YA RESUELTAS de un invernadero (las
        activas nunca se tocan). Con `older_than_days` solo las resueltas hace
        más de esos días; sin él, todas las resueltas. Solo el Owner.
        """
        data = request.data if hasattr(request.data, "get") else {}
        gh = data.get("greenhouse")
        if not isinstance(gh, int) or isinstance(gh, bool):
            raise serializers.ValidationError({"greenhouse": "Indica el id del invernadero."})
        self._require_owner(gh)

        qs = Alert.objects.filter(greenhouse_id=gh, status=Alert.Status.RESOLVED)
        days = data.get("older_than_days")
        if days is not None:
            if not isinstance(days, int) or isinstance(days, bool) or days < 0:
                raise serializers.ValidationError({"older_than_days": "Debe ser un número entero de días (0 o más)."})
            qs = qs.filter(resolved_at__lt=timezone.now() - timezone.timedelta(days=days))
        deleted, _ = qs.delete()
        return Response({"deleted": deleted})
