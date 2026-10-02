import hashlib
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication

from .models import Device


# Verificar la clave con el hash de contraseñas de Django (PBKDF2) cuesta
# cientos de milisegundos de CPU y se hacía en CADA petición de ingesta, lo
# que limitaba todo el backend a unas pocas peticiones por segundo. Como la
# clave es un token aleatorio de 256 bits, basta con verificarla a fondo una
# vez y recordar el resultado un rato.
AUTH_CACHE_SECONDS = 300
# `last_seen_at` solo se actualiza si lleva más de esto sin tocarse
# (en vez de una escritura a la base por cada petición).
LAST_SEEN_REFRESH = timedelta(seconds=30)


def _auth_cache_key(raw_key: str) -> str:
    # Solo se guarda el SHA-256 de la clave, nunca la clave.
    return "devauth:" + hashlib.sha256(raw_key.encode()).hexdigest()


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

        # El valor en caché es el hash guardado del dispositivo: si se rota
        # la clave, el hash cambia y la entrada vieja deja de coincidir sola.
        ck = _auth_cache_key(raw_key)
        if cache.get(ck) != device.api_key_hash:
            if not device.check_api_key(raw_key):
                raise exceptions.AuthenticationFailed("API key inválida.")
            cache.set(ck, device.api_key_hash, AUTH_CACHE_SECONDS)

        now = timezone.now()
        if device.last_seen_at is None or now - device.last_seen_at > LAST_SEEN_REFRESH:
            device.last_seen_at = now
            device.save(update_fields=["last_seen_at"])

        # DRF exige devolver (user, auth). No hay un User real aquí,
        # así que el "auth" es lo que importa: el propio Device. La
        # vista y el permiso lo leen desde request.auth.
        from django.contrib.auth.models import AnonymousUser
        return (AnonymousUser(), device)