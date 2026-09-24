from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.sensors.authentication import DeviceKeyAuthentication
from apps.sensors.permissions import IsDeviceAuthenticated

from .filters import ReadingFilter
from .models import Reading
from .pagination import ReadingCursorPagination
from .serializers import ReadingIngestItemSerializer, ReadingSerializer


class ReadingViewSet(viewsets.ReadOnlyModelViewSet):
    # ReadOnlyModelViewSet expone solo list y retrieve: no permite
    # crear, editar ni borrar lecturas por esta vía (Etapa 5).
    queryset = Reading.objects.select_related(
        "sensor", "sensor__sensor_type", "sensor__greenhouse"
    ).all()
    serializer_class = ReadingSerializer
    filterset_class = ReadingFilter
    pagination_class = ReadingCursorPagination


class ReadingIngestView(APIView):
    """
    POST /api/v1/readings/ingest/

    Acepta una lectura individual o un lote:

        {"sensor_id": 1, "value": 24.5}
        {"readings": [{"sensor_id": 1, "value": 24.5}, {...}]}

    Requiere el header X-Device-Key. No usa la autenticación de
    usuario del resto de la API (session/basic).
    """
    authentication_classes = [DeviceKeyAuthentication]
    permission_classes = [IsDeviceAuthenticated]

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

        results = []
        to_create = []

        for index, item in enumerate(raw_items):
            serializer = ReadingIngestItemSerializer(
                data=item, context={"device": device}
            )
            if serializer.is_valid():
                data = serializer.validated_data
                to_create.append(
                    Reading(
                        sensor=data["sensor"],
                        value=data["value"],
                        timestamp=data["timestamp"],
                    )
                )
                results.append({"index": index, "status": "accepted"})
            else:
                results.append(
                    {"index": index, "status": "rejected", "errors": serializer.errors}
                )

        if to_create:
            Reading.objects.bulk_create(to_create, ignore_conflicts=True)

        accepted = sum(1 for r in results if r["status"] == "accepted")
        rejected = len(results) - accepted

        return Response(
            {"accepted": accepted, "rejected": rejected, "results": results},
            status=200,
        )