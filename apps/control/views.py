from django.db import transaction
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, SAFE_METHODS, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.memberships.mixins import GreenhouseScopedMixin
from apps.memberships.models import Membership
from apps.memberships.permissions import role_of
from apps.sensors.authentication import DeviceKeyAuthentication
from apps.sensors.permissions import IsDeviceAuthenticated
from apps.sensors.throttling import DeviceRateThrottle

from . import services
from .models import ControlLoop
from .serializers import ControlLoopChangeSerializer, ControlLoopSerializer
from .tokens import DEVICE_WS_TOKEN_TTL, issue_device_ws_token

EDITOR_ROLES = (Membership.Role.OWNER, Membership.Role.OPERATOR)


class CanEditControlLoops(BasePermission):
    """
    Lectura: cualquier rol del invernadero. Escritura: Owner u Operator
    (staff cuenta como Owner). La restricción se valida aquí, en el servidor,
    no solo ocultando botones en la web.
    """

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        role = role_of(request.user, obj.greenhouse)
        if role is None:
            return False
        if request.method in SAFE_METHODS:
            return True
        return role in EDITOR_ROLES


class ControlLoopViewSet(GreenhouseScopedMixin, viewsets.ModelViewSet):
    """
    /api/v1/control-loops/   (filtro ?greenhouse=<id>)

    Guarda y entrega la configuración de los lazos; el cálculo (PID/PI/P/On-Off)
    lo hace el controlador. Cada cambio sube `version`, queda en el historial y
    se empuja al dispositivo por WebSocket.
    """

    serializer_class = ControlLoopSerializer
    permission_classes = [CanEditControlLoops]
    filterset_fields = ["greenhouse", "device", "enabled", "mode"]
    queryset = ControlLoop.objects.select_related(
        "greenhouse", "sensor", "sensor__sensor_type", "actuator", "device", "updated_by"
    ).all()

    def get_throttles(self):
        throttles = super().get_throttles()
        if self.request.method not in SAFE_METHODS:
            self.throttle_scope = "control_write"
            throttles.append(ScopedRateThrottle())
        return throttles

    def perform_create(self, serializer):
        greenhouse = serializer.validated_data["greenhouse"]
        role = role_of(self.request.user, greenhouse)
        if role not in EDITOR_ROLES:
            raise PermissionDenied("Necesitas ser propietario u operador de este invernadero.")
        serializer.save()

    def perform_destroy(self, instance):
        gid, did, lid = instance.greenhouse_id, instance.device_id, instance.pk
        with transaction.atomic():
            instance.delete()
            transaction.on_commit(lambda: services.publish_loop_deleted(gid, did, lid))

    @action(detail=True, methods=["get"], url_path="history")
    def history(self, request, pk=None):
        loop = self.get_object()
        qs = loop.changes.select_related("changed_by")[:100]
        return Response(ControlLoopChangeSerializer(qs, many=True).data)


# ---- endpoints que usa el DISPOSITIVO (X-Device-Key) ------------------------
class _DeviceView(APIView):
    authentication_classes = [DeviceKeyAuthentication]
    permission_classes = [IsDeviceAuthenticated]
    throttle_classes = [DeviceRateThrottle]


class DeviceWsTokenView(_DeviceView):
    """POST /api/v1/devices/ws-token/ -> token de un solo uso para /ws/device/?token=…"""

    def post(self, request):
        return Response({"token": issue_device_ws_token(request.auth), "expires_in": DEVICE_WS_TOKEN_TTL})


class DeviceControlConfigView(_DeviceView):
    """
    GET /api/v1/devices/control-config/

    Respaldo para leer la configuración al arrancar si el WebSocket aún no
    conecta. Solo devuelve los lazos de ESTE dispositivo.
    """

    def get(self, request):
        loops = ControlLoop.objects.filter(device=request.auth)
        return Response({"loops": [l.device_payload() for l in loops]})
