from rest_framework.throttling import SimpleRateThrottle


class DeviceRateThrottle(SimpleRateThrottle):
    """
    Throttling para la ingesta de lecturas (`ReadingIngestView`).

    Esa vista se autentica por API key de DISPOSITIVO
    (`DeviceKeyAuthentication`), no por usuario — ahí `request.user`
    siempre es `AnonymousUser`. Si usáramos los throttles estándar de
    DRF (`UserRateThrottle`/`AnonRateThrottle`), terminarían agrupando
    a TODOS los dispositivos que ingesten desde la misma red por su IP,
    en vez de darle a cada dispositivo su propio límite. Por eso usamos
    el id del `Device` (`request.auth`, puesto ahí por
    `DeviceKeyAuthentication`) como clave.
    """
    scope = "device"

    def get_cache_key(self, request, view):
        device = getattr(request, "auth", None)
        if device is None:
            # Sin dispositivo autenticado (la petición ya habrá sido
            # rechazada por IsDeviceAuthenticated antes de llegar aquí
            # en la práctica) — no hay nada que limitar por este lado.
            return None
        return self.cache_format % {"scope": self.scope, "ident": device.pk}
