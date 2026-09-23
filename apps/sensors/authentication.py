from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication

from .models import Device


class DeviceKeyAuthentication(BaseAuthentication):
    """
    Autentica un DISPOSITIVO (no un usuario) mediante el header:

        X-Device-Key: <api_key completa>

    La api_key generada por Device.set_api_key() empieza con los
    mismos 8 caracteres que quedan guardados en key_prefix. Por eso
    podemos usar ese prefijo para encontrar el registro correcto en
    O(1) por índice, sin tener que probar check_password() contra
    todos los dispositivos activos uno por uno.
    """

    def authenticate(self, request):
        raw_key = request.headers.get("X-Device-Key")
        if not raw_key:
            # None (no ValidationError) le dice a DRF "esta clase no
            # aplica aquí"; permite combinarse con otras si hiciera falta.
            return None

        prefix = raw_key[:8]
        try:
            device = Device.objects.select_related("greenhouse").get(
                key_prefix=prefix, is_active=True
            )
        except Device.DoesNotExist:
            raise exceptions.AuthenticationFailed("Dispositivo no reconocido o inactivo.")

        if not device.check_api_key(raw_key):
            raise exceptions.AuthenticationFailed("API key inválida.")

        device.last_seen_at = timezone.now()
        device.save(update_fields=["last_seen_at"])

        # DRF exige devolver (user, auth). No hay un User real aquí,
        # así que el "auth" es lo que importa: el propio Device. La
        # vista y el permiso lo leen desde request.auth.
        from django.contrib.auth.models import AnonymousUser
        return (AnonymousUser(), device)