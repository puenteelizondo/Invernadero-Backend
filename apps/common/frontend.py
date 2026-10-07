"""Dirección pública del frontend, para los enlaces que van en los correos."""
from fnmatch import fnmatch

from django.conf import settings


def _trusted(origin: str) -> bool:
    origin = origin.rstrip("/").lower()
    return any(fnmatch(origin, o.rstrip("/").lower()) for o in settings.CSRF_TRUSTED_ORIGINS)


def frontend_base(request=None) -> str:
    """
    URL base del frontend, sin "/" final, o "" si no se sabe.

    1) FRONTEND_URL del .env, si está.
    2) Si no, el Origin (o Referer) de la petición, pero SOLO si es uno de
       CSRF_TRUSTED_ORIGINS. Nunca se usa el Host tal cual: cualquiera
       podría mandar un Host falso y hacer que el correo apunte a su sitio.
    """
    if settings.FRONTEND_URL:
        return settings.FRONTEND_URL
    if request is None:
        return ""
    origin = request.headers.get("Origin", "")
    if not origin:
        referer = request.headers.get("Referer", "")
        parts = referer.split("/")
        origin = "/".join(parts[:3]) if len(parts) >= 3 and parts[0] in ("http:", "https:") else ""
    return origin.rstrip("/") if origin and _trusted(origin) else ""
