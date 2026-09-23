from rest_framework import serializers

from .models import Greenhouse, Zone


class ZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = Zone
        fields = ["id", "greenhouse", "name", "description", "created_at"]
        read_only_fields = ["id", "created_at"]


class GreenhouseSerializer(serializers.ModelSerializer):
    # Zonas anidadas de solo lectura: útil para ver de un vistazo la
    # estructura del invernadero sin una petición aparte. La CREACIÓN de
    # zonas sigue siendo por /zones/, no aquí (evita ambigüedad sobre
    # dónde se procesa cada cosa).
    zones = ZoneSerializer(many=True, read_only=True)

    class Meta:
        model = Greenhouse
        fields = [
            "id", "name", "description", "timezone", "is_active",
            "zones", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_timezone(self, value):
        import zoneinfo
        if value not in zoneinfo.available_timezones():
            raise serializers.ValidationError(
                f"'{value}' no es una zona horaria IANA válida."
            )
        return value