from rest_framework import serializers

from .models import Device, Sensor, SensorType


class SensorTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = SensorType
        fields = [
            "id", "code", "name", "default_unit",
            "valid_min", "valid_max", "description",
        ]
        read_only_fields = ["id"]


class DeviceSerializer(serializers.ModelSerializer):
    # La API key NUNCA se expone leyendo el recurso. Solo se genera
    # (y se muestra una vez) mediante la acción dedicada más abajo.
    class Meta:
        model = Device
        fields = [
            "id", "name", "greenhouse", "key_prefix",
            "is_active", "last_seen_at", "created_at",
        ]
        read_only_fields = ["id", "key_prefix", "last_seen_at", "created_at"]


class SensorSerializer(serializers.ModelSerializer):
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
        if zone and greenhouse and zone.greenhouse_id != greenhouse.id:
            raise serializers.ValidationError(
                {"zone": "La zona debe pertenecer al mismo invernadero que el sensor."}
            )
        return attrs