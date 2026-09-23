from rest_framework.permissions import BasePermission


class CanControlActuators(BasePermission):
    """
    Marcador de posición hasta la Etapa 12 (roles y permisos por
    invernadero). Por ahora, solo el staff puede cambiar el estado
    de un actuador; cualquier usuario autenticado puede solo LEER.
    Aislar esta regla en su propia clase significa que cuando
    construyamos roles reales, solo se edita este archivo — ninguna
    vista cambia.
    """

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_staff)