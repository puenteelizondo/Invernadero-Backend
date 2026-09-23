from rest_framework import viewsets

from .models import Greenhouse, Zone
from .serializers import GreenhouseSerializer, ZoneSerializer


class GreenhouseViewSet(viewsets.ModelViewSet):
    queryset = Greenhouse.objects.all()
    serializer_class = GreenhouseSerializer
    filterset_fields = ["is_active"]
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]


class ZoneViewSet(viewsets.ModelViewSet):
    queryset = Zone.objects.select_related("greenhouse").all()
    serializer_class = ZoneSerializer
    filterset_fields = ["greenhouse"]