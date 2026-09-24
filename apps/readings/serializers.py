import math

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from apps.sensors.models import Sensor

from .models import Reading


class ReadingSerializer(serializers.ModelSerializer):
    sensor_name = serializers.CharField(source="sensor.name", read_only=True)
    sensor_type = serializers.CharField(source="sensor.sensor_type.code", read_only=True)
    unit = serializers.CharField(source="sensor.get_unit", read_only=True)
    greenhouse = serializers.IntegerField(source="sensor.greenhouse_id", read_only=True)

    class Meta:
        model = Reading
        fields = [
            "id", "sensor", "sensor_name", "sensor_type", "unit",
            "greenhouse", "timestamp", "value",
        ]


class ReadingIngestItemSerializer(serializers.Serializer):
    """
    Valida UNA lectura dentro de un lote de ingesta. No crea el
    objeto directamente: la vista junta los resultados válidos de
    todo el lote y hace un solo bulk_create al final.
    """
    sensor_id = serializers.IntegerField()
    value = serializers.FloatField()
    timestamp = serializers.DateTimeField(required=False)

    def validate_value(self, value):
        if not math.isfinite(value):
            raise serializers.ValidationError("El valor debe ser un número finito.")
        return value

    def validate_timestamp(self, value):
        now = timezone.now()
        max_future = settings.READING_TIMESTAMP_MAX_FUTURE_SECONDS
        max_past = settings.READING_TIMESTAMP_MAX_PAST_SECONDS

        if value > now + timezone.timedelta(seconds=max_future):
            raise serializers.ValidationError(
                "El timestamp está demasiado adelantado respecto al servidor."
            )
        if value < now - timezone.timedelta(seconds=max_past):
            raise serializers.ValidationError("El timestamp está demasiado atrasado.")
        return value

    def validate(self, attrs):
        device = self.context["device"]
        try:
            sensor = Sensor.objects.select_related("sensor_type").get(
                pk=attrs["sensor_id"]
            )
        except Sensor.DoesNotExist:
            raise serializers.ValidationError({"sensor_id": "El sensor no existe."})

        if sensor.device_id != device.id:
            raise serializers.ValidationError(
                {"sensor_id": "Este sensor no está asignado a tu dispositivo."}
            )
        if not sensor.is_active:
            raise serializers.ValidationError({"sensor_id": "El sensor está inactivo."})

        stype = sensor.sensor_type
        value = attrs["value"]
        if stype.valid_min is not None and value < stype.valid_min:
            raise serializers.ValidationError(
                {"value": f"Valor fuera de rango (mínimo {stype.valid_min})."}
            )
        if stype.valid_max is not None and value > stype.valid_max:
            raise serializers.ValidationError(
                {"value": f"Valor fuera de rango (máximo {stype.valid_max})."}
            )

        attrs["sensor"] = sensor
        attrs.setdefault("timestamp", timezone.now())
        return attrs