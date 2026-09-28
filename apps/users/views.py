from rest_framework import generics, permissions

from .serializers import RegisterSerializer


class RegisterView(generics.CreateAPIView):
    """
    POST /api/v1/auth/register/

    Antes, crear un usuario nuevo dependía de `createsuperuser` o del
    admin -- no había forma de que alguien se registrara por sí mismo.
    Este endpoint es público a propósito (AllowAny, sin
    authentication_classes): es la puerta de entrada para que un
    cliente nuevo cree su propia cuenta.

    No crea ninguna Membership ni invernadero -- eso pasa después, ya
    sea porque el usuario registra su propio invernadero (ver
    GreenhouseViewSet.perform_create, que sí crea una Membership OWNER
    automáticamente) o porque alguien más lo invita a uno existente
    (ver apps/memberships).

    Comparte el AnonRateThrottle global (60/minute por IP, ver
    config/settings/base.py) igual que cualquier otro endpoint público
    -- no tiene un límite propio porque no hay ninguna razón para que
    el registro necesite ser más permisivo o más estricto que el resto.
    """
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
