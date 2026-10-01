from django.core.cache import cache
from django.db import transaction
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.types import TypeCatalogMixin
from apps.memberships.mixins import GreenhouseScopedMixin
from apps.memberships.permissions import IsGreenhouseMember

from .models import Device, Sensor, SensorType
from .serializers import DeviceSerializer, SensorSerializer, SensorTypeSerializer


class SensorTypeViewSet(TypeCatalogMixin, viewsets.ModelViewSet):
    """
    Catálogo de tipos de sensor. Hay dos ámbitos (ver TypeCatalogMixin):
    - GLOBALES (greenhouse vacío): los ve todo el mundo y solo los
      administra el staff.
    - PROPIOS de un invernadero (greenhouse con valor): los ven sus
      miembros y los administra su Owner (o el staff). Así un cliente
      puede crear un tipo que necesite sin alterar el catálogo que
      comparten todos los demás.
    """
    queryset = SensorType.objects.select_related("greenhouse").all()
    serializer_class = SensorTypeSerializer
    filterset_fields = ["greenhouse"]


class DeviceViewSet(GreenhouseScopedMixin, viewsets.ModelViewSet):
    serializer_class = DeviceSerializer
    permission_classes = [IsGreenhouseMember]
    filterset_fields = ["greenhouse", "is_active"]
    queryset = Device.objects.select_related("greenhouse").all()

    def create(self, request, *args, **kwargs):
        # Sobrescribe create() en vez de perform_create() porque ya
        # tenía lógica propia (generar la api_key) desde la Etapa 6 —
        # aquí se agrega la validación de dueño ANTES de guardar nada.
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self._require_owner(serializer.validated_data["greenhouse"].id)

        device = serializer.save()
        raw_key = device.set_api_key()
        device.save()
        data = serializer.data
        data["api_key"] = raw_key
        data["warning"] = "Guarda esta clave ahora. No se volverá a mostrar."
        headers = self.get_success_headers(serializer.data)
        return Response(data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=["post"], url_path="rotate-key")
    def rotate_key(self, request, pk=None):
        device = self.get_object()  # aquí sí corre IsGreenhouseMember: solo el Owner
        raw_key = device.set_api_key()
        device.save()
        return Response(
            {
                "id": device.id,
                "key_prefix": device.key_prefix,
                "api_key": raw_key,
                "warning": "Guarda esta clave ahora. No se volverá a mostrar.",
            }
        )


class SensorViewSet(GreenhouseScopedMixin, viewsets.ModelViewSet):
    serializer_class = SensorSerializer
    permission_classes = [IsGreenhouseMember]
    filterset_fields = ["greenhouse", "zone", "sensor_type", "device", "is_active"]
    search_fields = ["name"]
    queryset = Sensor.objects.select_related(
        "sensor_type", "greenhouse", "zone", "device"
    ).all()

    @action(detail=True, methods=["post"], url_path="purge")
    def purge(self, request, pk=None):
        """
        POST /api/v1/sensors/{id}/purge/   body: {"confirm_name": "<nombre exacto>"}

        Borrado EXPLÍCITO de un sensor junto con TODAS sus lecturas
        guardadas. El DELETE normal se niega si el sensor tiene lecturas
        (Reading.sensor es PROTECT, para no perder historial por error);
        esta acción existe para limpiar sensores de prueba o creados por
        equivocación. Es irreversible.

        Solo el Owner del invernadero (get_object() aplica
        IsGreenhouseMember: un POST exige rol Owner) y hay que mandar el
        nombre exacto del sensor, como confirmación.
        """
        from apps.readings.models import Reading
        from apps.readings.persistence import _latest_cache_key, _persisted_cache_key

        sensor = self.get_object()
        confirm = request.data.get("confirm_name") if hasattr(request.data, "get") else None
        if not isinstance(confirm, str) or confirm.strip() != sensor.name:
            raise serializers.ValidationError(
                {"confirm_name": "Escribe el nombre exacto del sensor para confirmar."}
            )

        sensor_id, name = sensor.id, sensor.name
        with transaction.atomic():
            deleted_readings, _ = Reading.objects.filter(sensor_id=sensor_id).delete()
            sensor.delete()
        cache.delete_many([_persisted_cache_key(sensor_id), _latest_cache_key(sensor_id)])

        return Response(
            {"sensor_id": sensor_id, "name": name, "readings_deleted": deleted_readings},
            status=status.HTTP_200_OK,
        )
