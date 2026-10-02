from rest_framework import serializers

from apps.memberships.scoping import visible_greenhouse_ids
from apps.sensors.models import Sensor

from .models import Alert, AlertRule


class AlertRuleSerializer(serializers.ModelSerializer):
    sensor_name = serializers.CharField(source="sensor.name", read_only=True)
    unit = serializers.CharField(source="sensor.get_unit", read_only=True)
    has_active_alert = serializers.SerializerMethodField()

    class Meta:
        model = AlertRule
        fields = [
            "id", "sensor", "sensor_name", "unit", "greenhouse", "name", "rule_type",
            "min_value", "max_value", "duration_seconds", "severity",
            "is_active", "notify_email", "has_active_alert", "created_at",
        ]
        read_only_fields = ["greenhouse", "created_at"]

    def get_has_active_alert(self, obj):
        return obj.active_alert_id is not None

    def validate_sensor(self, sensor):
        # Un usuario no puede referenciar sensores de invernaderos ajenos.
        user = self.context["request"].user
        ids = visible_greenhouse_ids(user)
        if ids is not None and sensor.greenhouse_id not in ids:
            raise serializers.ValidationError("El sensor no existe.")
        if self.instance is not None and sensor.pk != self.instance.sensor_id:
            raise serializers.ValidationError("No se puede cambiar el sensor de una regla; crea otra.")
        return sensor

    # Mínimo de silencio tolerable en una regla "sin señal" (menos sería ruido: los sensores reportan cada pocos segundos).
    MIN_NO_SIGNAL_SECONDS = 30

    def validate(self, attrs):
        # En un PATCH parcial, se mezclan con los valores ya guardados.
        get = lambda k: attrs[k] if k in attrs else getattr(self.instance, k, None)  # noqa: E731
        if (get("rule_type") or AlertRule.RuleType.THRESHOLD) == AlertRule.RuleType.NO_SIGNAL:
            if (get("duration_seconds") or 0) < self.MIN_NO_SIGNAL_SECONDS:
                raise serializers.ValidationError(
                    {"duration_seconds": f"Indica cuántos segundos sin datos se toleran (mínimo {self.MIN_NO_SIGNAL_SECONDS})."}
                )
            attrs["min_value"] = attrs["max_value"] = None  # no aplican límites a esta clase de regla
            return attrs
        lo, hi = get("min_value"), get("max_value")
        if lo is None and hi is None:
            raise serializers.ValidationError("Define al menos un límite: mínimo o máximo.")
        if lo is not None and hi is not None and lo >= hi:
            raise serializers.ValidationError({"min_value": "El mínimo debe ser menor que el máximo."})
        return attrs


class AlertSerializer(serializers.ModelSerializer):
    sensor_name = serializers.CharField(source="sensor.name", read_only=True)
    unit = serializers.CharField(source="sensor.get_unit", read_only=True)
    rule_name = serializers.SerializerMethodField()
    acknowledged_by_name = serializers.CharField(source="acknowledged_by.username", read_only=True, default=None)

    class Meta:
        model = Alert
        fields = [
            "id", "rule", "rule_name", "sensor", "sensor_name", "unit", "greenhouse",
            "kind", "severity", "status", "threshold", "trigger_value", "peak_value",
            "opened_at", "resolved_at", "acknowledged_at", "acknowledged_by_name",
        ]
        read_only_fields = fields

    def get_rule_name(self, obj):
        return str(obj.rule) if obj.rule_id else ""
