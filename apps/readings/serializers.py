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
        # La vista precarga todos los sensores del lote en una sola consulta
        # (context["sensors"]); si no viene, se consulta de a uno.
        preloaded = self.context.get("sensors")
        if preloaded is not None:
            sensor = preloaded.get(attrs["sensor_id"])
        else:
            sensor = Sensor.objects.select_related("sensor_type", "greenhouse").filter(
                pk=attrs["sensor_id"]
            ).first()
        # Mismo mensaje si no existe o es de otro dispositivo: así un dispositivo
        # no puede averiguar qué ids de sensores existen en otros invernaderos.
        if sensor is None or sensor.device_id != device.id:
            raise serializers.ValidationError(
                {"sensor_id": "El sensor no existe o no está asignado a este dispositivo."}
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


class ReadingExportQuerySerializer(serializers.Serializer):
    """
    Valida los parámetros de /readings/export/. Es un serializer
    "suelto" (no ligado a un modelo) porque lo único que necesita es
    validar query params de un GET, no serializar filas.
    """
    date_from = serializers.DateTimeField()
    date_to = serializers.DateTimeField()
    sensor = serializers.IntegerField(required=False)

    def validate(self, attrs):
        if attrs["date_to"] <= attrs["date_from"]:
            raise serializers.ValidationError(
                {"date_to": "Debe ser posterior a date_from."}
            )

        max_days = settings.READING_EXPORT_MAX_DAYS
        span_days = (attrs["date_to"] - attrs["date_from"]).days
        if span_days > max_days:
            raise serializers.ValidationError(
                {"date_to": f"El rango no puede superar {max_days} días."}
            )

        if "sensor" in attrs and not Sensor.objects.filter(pk=attrs["sensor"]).exists():
            raise serializers.ValidationError({"sensor": "El sensor no existe."})

        return attrs