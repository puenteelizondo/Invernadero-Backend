import math

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from apps.sensors.models import Sensor

from .models import Reading


class ReadingIngestItemSerializer(serializers.Serializer):
    """
    Valida UNA lectura dentro de un lote de ingesta. No es un
    ModelSerializer porque no crea el objeto directamente: la vista
    junta los resultados válidos de todo el lote y hace un solo
    bulk_create al final (ver decisión 6 de esta etapa).
    """
    sensor_id = serializers.IntegerField()
    value = serializers.FloatField()
    timestamp = serializers.DateTimeField(required=False)

    def validate_value(self, value):
        if not math.isfinite(value):
            # JSON técnicamente permite NaN/Infinity en algunos parsers
            # (incluido el que usa DRF por defecto); una lectura NaN no
            # tiene sentido físico y rompería agregaciones futuras.
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
            raise serializers.ValidationError(
                "El timestamp está demasiado atrasado."
            )
        return value

    def validate(self, attrs):
        # El dispositivo autenticado viaja en el contexto (lo pone la
        # vista). Aquí se cierra la regla de seguridad clave de esta
        # etapa: el sensor debe pertenecer a ESTE dispositivo.
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
            raise serializers.ValidationError(
                {"sensor_id": "El sensor está inactivo."}
            )

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