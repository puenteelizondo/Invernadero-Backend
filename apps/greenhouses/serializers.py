from rest_framework import serializers

from apps.common.serializers import FixedGreenhouseMixin

from .models import Greenhouse, Zone


class ZoneSerializer(FixedGreenhouseMixin, serializers.ModelSerializer):
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
    # Rol del usuario actual en este invernadero (owner/operator/viewer; el staff
    # cuenta como owner). Permite a la web decidir qué mostrar editable sin tener
    # que leer las membresías, que solo ve el propietario.
    my_role = serializers.SerializerMethodField()

    def get_my_role(self, obj):
        from apps.memberships.permissions import role_of

        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return None
        return role_of(request.user, obj)

    class Meta:
        model = Greenhouse
        fields = [
            "id", "name", "description", "timezone", "is_active",
            "zones", "created_at", "updated_at", "my_role",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_timezone(self, value):
        import zoneinfo
        if value not in zoneinfo.available_timezones():
            raise serializers.ValidationError(
                f"'{value}' no es una zona horaria IANA válida."
            )
        return value