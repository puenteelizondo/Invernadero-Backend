from django.db.models import Q
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.memberships.models import Membership
from apps.memberships.scoping import visible_greenhouse_ids


class TypeCatalogMixin:
    """
    Para los ViewSets de SensorType y ActuatorType.

    Un tipo puede ser GLOBAL (greenhouse vacío: lo administra el staff y
    lo ven todos) o PROPIO de un invernadero (greenhouse con valor: solo lo
    ven sus miembros y solo lo administra su Owner, o el staff).

    - Ver: cualquier autenticado ve los globales y los de sus invernaderos
      (el staff ve todos).
    - Crear / editar / borrar: un tipo global solo el staff; un tipo de
      invernadero, su Owner o el staff.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        ids = visible_greenhouse_ids(self.request.user)
        if ids is None:
            return qs
        return qs.filter(Q(greenhouse__isnull=True) | Q(greenhouse_id__in=ids))

    def _require_can_manage(self, greenhouse_id):
        user = self.request.user
        if user.is_staff:
            return
        if greenhouse_id is None:
            raise PermissionDenied("Solo el staff puede crear o modificar tipos globales.")
        is_owner = Membership.objects.filter(
            user=user, greenhouse_id=greenhouse_id, role=Membership.Role.OWNER
        ).exists()
        if not is_owner:
            raise PermissionDenied("Debes ser el propietario (owner) de este invernadero.")

    def perform_create(self, serializer):
        greenhouse = serializer.validated_data.get("greenhouse")
        self._require_can_manage(greenhouse.id if greenhouse else None)
        serializer.save()

    def perform_update(self, serializer):
        self._require_can_manage(serializer.instance.greenhouse_id)
        serializer.save()

    def perform_destroy(self, instance):
        self._require_can_manage(instance.greenhouse_id)
        instance.delete()


class TypeCatalogSerializerMixin(serializers.Serializer):
    """
    Campos y validaciones comunes de SensorTypeSerializer y
    ActuatorTypeSerializer: `can_edit` (¿puede el usuario actual
    modificar/borrar este tipo?) y las reglas sobre `greenhouse` y `code`.
    """
    can_edit = serializers.SerializerMethodField()

    def get_can_edit(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        if user.is_staff:
            return True
        if obj.greenhouse_id is None:
            return False
        # Se calcula una sola vez por petición, no una consulta por tipo.
        owner_ids = self.context.get("_owner_greenhouse_ids")
        if owner_ids is None:
            owner_ids = set(
                Membership.objects.filter(user=user, role=Membership.Role.OWNER)
                .values_list("greenhouse_id", flat=True)
            )
            self.context["_owner_greenhouse_ids"] = owner_ids
        return obj.greenhouse_id in owner_ids

    def validate_greenhouse(self, value):
        # El ámbito de un tipo no se cambia después de crearlo: moverlo
        # dejaría sensores/actuadores existentes con un tipo ajeno.
        if self.instance is not None and value != self.instance.greenhouse:
            raise serializers.ValidationError("No se puede cambiar el invernadero de un tipo ya creado.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        model = self.Meta.model
        code = attrs.get("code", getattr(self.instance, "code", None))
        greenhouse = attrs.get("greenhouse", getattr(self.instance, "greenhouse", None))
        if code and greenhouse is not None:
            # Un tipo propio no puede repetir el código de uno global: hay que usar el global.
            if model.objects.filter(code=code, greenhouse__isnull=True).exists():
                raise serializers.ValidationError(
                    {"code": "Ya existe un tipo global con ese código; úsalo en vez de crear otro."}
                )
        return attrs
