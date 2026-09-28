"""
Configuración común a todos los entornos.

Todo lo que cambia entre máquinas (secretos, hosts, base de datos)
se lee del entorno; nada sensible está escrito aquí.
"""
from pathlib import Path

import environ

# config/settings/base.py -> subimos 3 niveles hasta la raíz del proyecto
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
READING_EXPORT_MAX_DAYS = 366
# --- Seguridad básica -------------------------------------------------
# Sin valor por defecto: si falta en el entorno, Django se niega a arrancar.
SECRET_KEY = env("DJANGO_SECRET_KEY")
# Por defecto apagado: si alguien olvida la variable, falla hacia el lado seguro.
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

# --- CORS ---------------------------------------------------------------
# Sin esto, un frontend en otro origen (ej. http://localhost:5173 en
# desarrollo, o tu dominio real en producción) no puede llamar a esta API
# desde el navegador: el navegador bloquea la respuesta por la política de
# mismo origen si no trae los headers Access-Control-Allow-*.
#
# Lista blanca explícita por entorno (nunca "abrir a todos" por defecto):
# en vez de CORS_ALLOW_ALL_ORIGINS=True, cada entorno declara en su .env
# exactamente qué orígenes puede llamarlo. Por defecto, lista vacía — sin
# configurar nada, NINGÚN origen externo puede usar la API (falla hacia el
# lado seguro, igual que SECRET_KEY/DEBUG arriba).
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
# True porque el frontend puede autenticarse con SessionAuthentication
# (cookie de sesión) además de Basic Auth. Con CORS_ALLOWED_ORIGINS como
# lista explícita (nunca "*"), django-cors-headers responde con el origen
# exacto que hizo la petición — combinación que sí permite credenciales,
# a diferencia de un comodín.
CORS_ALLOW_CREDENTIALS = True

# --- Aplicaciones -----------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "daphne",                      # NUEVO — debe ir antes de staticfiles
    "django.contrib.staticfiles",
    "corsheaders",                 # NUEVO
    "rest_framework",
    "django_filters",
    "channels",                    # NUEVO
    "apps.users",
    "apps.greenhouses",
    "apps.sensors",
    "apps.readings",
    "apps.actuators",
    "apps.memberships",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # CorsMiddleware va lo más arriba posible, y SIEMPRE antes de
    # CommonMiddleware: CommonMiddleware puede generar una respuesta
    # propia (ej. redirect por falta de slash final) antes de que el
    # request llegue más abajo, y esa respuesta también necesita los
    # headers de CORS para que el navegador no la bloquee.
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Base de datos (PostgreSQL) ---------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST"),
        "PORT": env("POSTGRES_PORT", default="5432"),
    }
}

# --- Usuario personalizado ---------------------------------------------
# Le dice a Django que use apps.users.User en vez de django.contrib.auth.User.
# Debe existir ANTES de la primera migración (ver Etapa 3): cambiarlo
# después obliga a reconstruir la base de datos desde cero.
AUTH_USER_MODEL = "users.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Internacionalización y zona horaria ------------------------------
LANGUAGE_CODE = "es-mx"
# Todo se guarda y procesa en UTC. La zona horaria de cada invernadero
# (campo del modelo Greenhouse) se aplicará solo al MOSTRAR datos.
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"



# --- Django REST Framework ---------------------------------------------
REST_FRAMEWORK = {
    # Autenticación mínima para esta etapa. SessionAuthentication permite
    # navegar la API en el navegador tras iniciar sesión en /admin/;
    # BasicAuthentication permite probar en Postman con usuario/contraseña
    # sin construir nada más. En la Etapa 12 esto se reemplaza por JWT
    # y roles diferenciados por invernadero.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    # Por defecto, ningún endpoint es público. Quien quiera exponer algo
    # sin login (como la ingesta de lecturas, Etapa 6) lo declara explícito
    # en su propia vista.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    # Rate limiting básico: nada limitaba cuántas peticiones por minuto
    # podía mandar un cliente (ni la ingesta de lecturas, ni un login a
    # fuerza bruta). AnonRateThrottle/UserRateThrottle cubren la API
    # normal (agrupan por IP o por usuario autenticado); la ingesta de
    # dispositivos usa su propio throttle por separado porque no se
    # autentica como usuario (ver ReadingIngestView y
    # apps/sensors/throttling.py::DeviceRateThrottle).
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/minute",
        "user": "300/minute",
        "device": "120/minute",
    },
}

# --- Ingesta de lecturas -------------------------------------------
# Tolerancia de reloj para el timestamp que reporta un dispositivo.
# No son secretos ni cambian por entorno, así que van como constantes
# simples en vez de variables de .env.
READING_TIMESTAMP_MAX_FUTURE_SECONDS = 300        # 5 minutos de adelanto
READING_TIMESTAMP_MAX_PAST_SECONDS = 7 * 24 * 3600  # 7 días de atraso


# --- Channels / WebSockets ---------------------------------------------
ASGI_APPLICATION = "config.asgi.application"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [
                {
                    "address": env("REDIS_URL", default="redis://redis:6379/0"),
                    "socket_timeout": None,
                }
            ],
        },
    },
}
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_CACHE_URL", default="redis://redis:6379/1"),
    }
}