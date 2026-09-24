from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Device, Sensor, SensorType
from .serializers import DeviceSerializer, SensorSerializer, SensorTypeSerializer


class SensorTypeViewSet(viewsets.ModelViewSet):
    queryset = SensorType.objects.all()
    serializer_class = SensorTypeSerializer


class DeviceViewSet(viewsets.ModelViewSet):
    queryset = Device.objects.select_related("greenhouse").all()
    serializer_class = DeviceSerializer
    filterset_fields = ["greenhouse", "is_active"]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
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
        device = self.get_object()
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


class SensorViewSet(viewsets.ModelViewSet):
    queryset = Sensor.objects.select_related(
        "sensor_type", "greenhouse", "zone", "device"
    ).all()
    serializer_class = SensorSerializer
    filterset_fields = ["greenhouse", "zone", "sensor_type", "device", "is_active"]
    search_fields = ["name"]