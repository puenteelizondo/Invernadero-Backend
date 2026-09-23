from rest_framework.response import Response
from rest_framework.views import APIView

from apps.sensors.authentication import DeviceKeyAuthentication
from apps.sensors.permissions import IsDeviceAuthenticated

from .models import Reading
from .serializers import ReadingIngestItemSerializer

# ... (ReadingViewSet, ya existente de la Etapa 5, se mantiene igual)


class ReadingIngestView(APIView):
    """
    POST /api/v1/readings/ingest/

    Acepta una lectura individual o un lote:

        {"sensor_id": 1, "value": 24.5}
        {"readings": [{"sensor_id": 1, "value": 24.5}, {...}]}

    Requiere el header X-Device-Key. NO usa la autenticación de
    usuario del resto de la API (session/basic), por eso pisamos
    authentication_classes y permission_classes aquí explícitamente
    en vez de heredar los DEFAULT_* de settings.
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

        # Un solo INSERT para todo el lote. ignore_conflicts evita que
        # un duplicado (sensor + timestamp ya existente, ej. un reintento
        # de red) tumbe el resto del lote con un IntegrityError.
        if to_create:
            Reading.objects.bulk_create(to_create, ignore_conflicts=True)

        accepted = sum(1 for r in results if r["status"] == "accepted")
        rejected = len(results) - accepted

        return Response(
            {
                "accepted": accepted,
                "rejected": rejected,
                "results": results,
            },
            status=200,
        )