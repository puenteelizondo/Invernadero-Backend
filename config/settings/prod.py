"""
Configuración de producción -- el archivo "hermano" de dev.py que el
propio comentario de dev.py ya anunciaba desde la Etapa 5 ("En la
Etapa 14 crearemos prod.py").

Solo lo que es DISTINTO de base.py para correr detrás de HTTPS de
verdad. Nada de esto se activa a menos que actives este módulo:

    DJANGO_SETTINGS_MODULE=config.settings.prod

(ver docker-compose.prod.yml, que ya lo hace por ti).
"""
from .base import *  # noqa: F401,F403

# DEBUG ya es False por defecto en base.py (env.bool con default=False)
# -- lo forzamos aquí explícitamente para no depender de que nadie
# olvide poner DEBUG=False en el .env de producción.
DEBUG = False

# --- HTTPS ---------------------------------------------------------------
# Todo esto asume que Django corre detrás de un proxy/balanceador que
# termina TLS (Nginx, un load balancer del proveedor de hosting, etc.)
# -- si expusieras Daphne directo a internet sin nada delante (no
# recomendado), quita SECURE_PROXY_SSL_HEADER o Django nunca vería la
# conexión como "segura" y quedarías en un loop de redirects.
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HSTS: le dice al navegador "de ahora en adelante, entra siempre por
# HTTPS a este dominio, ni siquiera intentes HTTP". Empieza en 7 días
# (no en el año que recomienda Django para producción madura) para que
# un error de configuración no te deje un dominio inaccesible por HTTP
# durante demasiado tiempo mientras todavía estás afinando esto.
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 7)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Sin esto, Django 4+ rechaza cualquier POST/PUT/PATCH/DELETE
# autenticado por sesión (CsrfViewMiddleware) que llegue desde tu
# dominio real -- CSRF_TRUSTED_ORIGINS vacío significa "no confíes en
# ningún origen externo". Debe incluir el esquema (https://) y, si tu
# frontend vive en un dominio aparte, ese dominio también.
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# --- Estáticos -----------------------------------------------------------
# collectstatic (que docker-compose.prod.yml corre antes de levantar
# Daphne) necesita un STATIC_ROOT fijo para saber dónde juntar todo
# (CSS/JS del admin y de la browsable API de DRF). CÓMO servir esa
# carpeta (Nginx, un bucket, whitenoise, etc.) queda fuera de este
# archivo a propósito -- no se agrega una dependencia nueva al
# proyecto (ej. whitenoise) sin que lo decidas tú explícitamente.
STATIC_ROOT = BASE_DIR / "staticfiles"
