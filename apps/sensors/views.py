from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from apps.memberships.mixins import GreenhouseScopedMixin
from apps.memberships.permissions import IsGreenhouseMember

from .models import Device, Sensor, SensorType
from .serializers import DeviceSerializer, SensorSerializer, SensorTypeSerializer


class SensorTypeViewSet(viewsets.ModelViewSet):
    """
    Catálogo GLOBAL, compartido por todos los invernaderos/clientes —
    no pertenece a uno en particular, así que no usa
    GreenhouseScopedMixin. Cualquier usuario autenticado puede leerlo
    (lo necesita para elegir un tipo al crear un sensor), pero solo el
    staff puede crear/editar/borrar tipos nuevos: un cliente no debe
    poder alterar el catálogo que comparten todos los demás.
    """
    queryset = SensorType.objects.all()
    serializer_class = SensorTypeSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAdminUser()]
        return super().get_permissions()


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