from rest_framework import serializers

from apps.common.serializers import FixedGreenhouseMixin

from apps.common.types import TypeCatalogSerializerMixin

from .models import Actuator, ActuatorStateHistory, ActuatorType


class ActuatorTypeSerializer(TypeCatalogSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = ActuatorType
        fields = ["id", "greenhouse", "code", "name", "description", "can_edit"]
        read_only_fields = ["id"]


class ActuatorSerializer(FixedGreenhouseMixin, serializers.ModelSerializer):
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
        # El dispositivo también debe ser de este invernadero: si no, alguien podía
        # asignarle a su actuador el ESP32 de OTRO invernadero (y mandarle lazos u órdenes).
        device = attrs.get("device", getattr(self.instance, "device", None))
        if device and greenhouse and device.greenhouse_id != greenhouse.id:
            raise serializers.ValidationError(
                {"device": "El dispositivo debe pertenecer al mismo invernadero que el actuador."}
            )
        # Si lo usa un lazo, el dispositivo se cambia desde el lazo (si no, el lazo
        # quedaría en un ESP32 que ya no tiene este actuador).
        if self.instance is not None and "device" in attrs and attrs["device"] != self.instance.device:
            loop = self.instance.control_loops.first()
            if loop is not None:
                raise serializers.ValidationError(
                    {"device": f"Lo usa el lazo «{loop.name}». Cambia o borra el lazo primero."}
                )
        if zone and greenhouse and zone.greenhouse_id != greenhouse.id:
            raise serializers.ValidationError(
                {"zone": "La zona debe pertenecer al mismo invernadero que el actuador."}
            )
        actuator_type = attrs.get("actuator_type", getattr(self.instance, "actuator_type", None))
        if actuator_type and greenhouse and actuator_type.greenhouse_id not in (None, greenhouse.id):
            raise serializers.ValidationError(
                {"actuator_type": "Ese tipo de actuador pertenece a otro invernadero."}
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