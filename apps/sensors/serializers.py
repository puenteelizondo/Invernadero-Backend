from rest_framework import serializers

from apps.common.serializers import FixedGreenhouseMixin

from apps.common.types import TypeCatalogSerializerMixin

from .models import Device, Sensor, SensorType


class SensorTypeSerializer(TypeCatalogSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = SensorType
        fields = [
            "id", "greenhouse", "code", "name", "default_unit",
            "valid_min", "valid_max", "description", "can_edit",
        ]
        read_only_fields = ["id"]


class DeviceSerializer(FixedGreenhouseMixin, serializers.ModelSerializer):
    # La API key NUNCA se expone leyendo el recurso. Solo se genera
    # (y se muestra una vez) mediante la acción dedicada más abajo.
    class Meta:
        model = Device
        fields = [
            "id", "name", "greenhouse", "key_prefix",
            "is_active", "last_seen_at", "created_at",
        ]
        read_only_fields = ["id", "key_prefix", "last_seen_at", "created_at"]


class SensorSerializer(FixedGreenhouseMixin, serializers.ModelSerializer):
    sensor_type_name = serializers.CharField(source="sensor_type.name", read_only=True)
    effective_unit = serializers.CharField(source="get_unit", read_only=True)

    class Meta:
        model = Sensor
        fields = [
            "id", "name", "sensor_type", "sensor_type_name", "device",
            "greenhouse", "zone", "unit", "effective_unit", "description",
            "is_active", "reading_interval_seconds",
            "persist_interval_seconds", "persist_deadband", "config",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        # Replica Sensor.clean(): la zona debe pertenecer al mismo
        # invernadero que el sensor. Ver la nota sobre clean() en
        # GreenhouseSerializer — aquí el chequeo es entre dos campos,
        # así que va en validate() (a nivel de objeto) y no en un
        # validate_<campo> individual.
        zone = attrs.get("zone", getattr(self.instance, "zone", None))
        greenhouse = attrs.get("greenhouse", getattr(self.instance, "greenhouse", None))
        # El dispositivo también debe ser de este invernadero: si no, alguien podía
        # asignarle a su sensor el ESP32 de OTRO invernadero (y mandarle lazos u órdenes).
        device = attrs.get("device", getattr(self.instance, "device", None))
        if device and greenhouse and device.greenhouse_id != greenhouse.id:
            raise serializers.ValidationError(
                {"device": "El dispositivo debe pertenecer al mismo invernadero que el sensor."}
            )
        # Si lo usa un lazo, el dispositivo se cambia desde el lazo (si no, el lazo
        # quedaría en un ESP32 que ya no tiene este sensor).
        if self.instance is not None and "device" in attrs and attrs["device"] != self.instance.device:
            loop = self.instance.control_loops.first()
            if loop is not None:
                raise serializers.ValidationError(
                    {"device": f"Lo usa el lazo «{loop.name}». Cambia o borra el lazo primero."}
                )
        if zone and greenhouse and zone.greenhouse_id != greenhouse.id:
            raise serializers.ValidationError(
                {"zone": "La zona debe pertenecer al mismo invernadero que el sensor."}
            )
        # Un tipo propio de otro invernadero no se puede usar aquí (los globales sí).
        sensor_type = attrs.get("sensor_type", getattr(self.instance, "sensor_type", None))
        if sensor_type and greenhouse and sensor_type.greenhouse_id not in (None, greenhouse.id):
            raise serializers.ValidationError(
                {"sensor_type": "Ese tipo de sensor pertenece a otro invernadero."}
            )
        return attrs