from rest_framework.permissions import BasePermission

from .models import Device


class IsDeviceAuthenticated(BasePermission):
    """
    A diferencia de IsAuthenticated (que mira request.user), esto
    verifica que request.auth sea un Device válido — el objeto que
    DeviceKeyAuthentication deja ahí tras validar la API key.
    """

    def has_permission(self, request, view):
        return isinstance(request.auth, Device)