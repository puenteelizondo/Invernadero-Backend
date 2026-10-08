from django.http import FileResponse
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.memberships.mixins import GreenhouseScopedMixin
from apps.memberships.permissions import IsGreenhouseMember
from apps.memberships.scoping import visible_greenhouse_ids
from apps.sensors.authentication import DeviceKeyAuthentication
from apps.sensors.permissions import IsDeviceAuthenticated
from apps.sensors.throttling import DeviceRateThrottle

from .exports import build_readings_xlsx
from .filters import ReadingFilter
from .models import Reading
from .pagination import ReadingCursorPagination
from .ingest import ingest_readings
from .serializers import (
    ReadingExportQuerySerializer,
    ReadingSerializer,
)


class ReadingViewSet(GreenhouseScopedMixin, viewsets.ReadOnlyModelViewSet):
    # Reading no tiene un campo .greenhouse propio (llega a él vía
    # sensor), por eso greenhouse_field usa la doble-guía de Django
    # ORM para cruzar la relación.
    greenhouse_field = "sensor__greenhouse_id"
    serializer_class = ReadingSerializer
    permission_classes = [IsGreenhouseMember]
    filterset_class = ReadingFilter
    pagination_class = ReadingCursorPagination
    queryset = Reading.objects.select_related(
        "sensor", "sensor__sensor_type", "sensor__greenhouse"
    ).all()


class ReadingIngestView(APIView):
    authentication_classes = [DeviceKeyAuthentication]
    permission_classes = [IsDeviceAuthenticated]
    # Throttle propio por dispositivo, no el UserRateThrottle/AnonRateThrottle
    # global (ver apps/sensors/throttling.py) -- request.user aquí siempre es
    # AnonymousUser, así que esos throttles agruparían por IP, no por
    # dispositivo.
    throttle_classes = [DeviceRateThrottle]

    def post(self, request):
        device = request.auth
        raw_items = request.data.get("readings") \
            if isinstance(request.data, dict) and "readings" in request.data \
            else [request.data]

        if not isinstance(raw_items, list) or not raw_items:
            return Response(
                {"detail": "Se esperaba una lectura o una lista bajo 'readings'."},
                status=400,
            )

        return Response(ingest_readings(device, raw_items), status=200)


class ReadingExportView(APIView):
    """
    GET /api/v1/readings/export/?date_from=...&date_to=...&sensor=<id opcional>

    Etapa 12: se agrega el filtrado multi-tenant. Antes cualquier
    usuario autenticado podía exportar lecturas de CUALQUIER sensor
    (un hueco real ahora que hay varios clientes). Ahora:
    - Si mandas 'sensor', se exige que seas miembro (cualquier rol)
      del invernadero de ese sensor.
    - Si no mandas 'sensor' (exportar todo un rango), el queryset se
      limita de entrada a los sensores de TUS invernaderos — nunca ves
      datos ajenos aunque no hayas filtrado por sensor.
    """

    def get(self, request):
        query = ReadingExportQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data

        queryset = Reading.objects.select_related(
            "sensor", "sensor__sensor_type", "sensor__greenhouse"
        ).filter(
            timestamp__gte=data["date_from"],
            timestamp__lte=data["date_to"],
        )

        ids = visible_greenhouse_ids(request.user)
        if ids is not None:
            queryset = queryset.filter(sensor__greenhouse_id__in=ids)

        if "sensor" in data:
            queryset = queryset.filter(sensor_id=data["sensor"])

        queryset = queryset.order_by("sensor_id", "timestamp")

        xlsx_file, row_count = build_readings_xlsx(queryset)

        filename = (
            f"lecturas_{data['date_from']:%Y%m%d}_{data['date_to']:%Y%m%d}.xlsx"
        )
        response = FileResponse(
            xlsx_file,
            as_attachment=True,
            filename=filename,
            content_type=(
                "application/vnd.openxmlformats-officedocument"
                ".spreadsheetml.sheet"
            ),
        )
        response["X-Row-Count"] = str(row_count)
        return response