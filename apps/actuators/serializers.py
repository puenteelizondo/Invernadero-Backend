from rest_framework import serializers

from .models import Actuator, ActuatorStateHistory, ActuatorType


class ActuatorTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActuatorType
        fields = ["id", "code", "name", "description"]
        read_only_fields = ["id"]


class ActuatorSerializer(serializers.ModelSerializer):
    actuator_type_name = serializers.CharField(source="actuator_type.name", read_only=True)

    class Meta:
        model = Actuator
        fields = [
            "id", "name", "actuator_type", "actuator_type_name", "device",
            "greenhouse", "zone", "description", "is_active", "state",
            "config", "created_at", "updated_at",
        ]
        # "state" es de solo lectura AQUÍ: cambiar el estado real solo
        # ocurre a través de la acción dedicada (ver ActuatorViewSet),
        # nunca por un PATCH genérico. Esto blinda en código la decisión
        # de diseño que tomamos: "un cambio de estado es una acción, no
        # una edición de campo".
        read_only_fields = ["id", "state", "created_at", "updated_at"]

    def validate(self, attrs):
        zone = attrs.get("zone", getattr(self.instance, "zone", None))
        greenhouse = attrs.get("greenhouse", getattr(self.instance, "greenhouse", None))
        if zone and greenhouse and zone.greenhouse_id != greenhouse.id:
            raise serializers.ValidationError(
                {"zone": "La zona debe pertenecer al mismo invernadero que el actuador."}
            )
        return attrs


class ActuatorStateChangeSerializer(serializers.Serializer):
    """
    No es un ModelSerializer: no representa el recurso completo,
    solo valida la entrada de la acción de control.
    """
    state = serializers.BooleanField()


class ActuatorStateHistorySerializer(serializers.ModelSerializer):
    changed_by_username = serializers.CharField(
        source="changed_by.username", read_only=True, default=None
    )

    class Meta:
        model = ActuatorStateHistory
        fields = ["id", "state", "changed_by_username", "source", "changed_at"]