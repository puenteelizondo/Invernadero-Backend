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

# --- Seguridad básica -------------------------------------------------
# Sin valor por defecto: si falta en el entorno, Django se niega a arrancar.
SECRET_KEY = env("DJANGO_SECRET_KEY")
# Por defecto apagado: si alguien olvida la variable, falla hacia el lado seguro.
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

# --- Aplicaciones -----------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # --- terceros ---
    "rest_framework",
    "django_filters",
    # --- apps propias ---
    "apps.users",
    "apps.greenhouses",
    "apps.sensors",
    "apps.readings",
    "apps.actuators",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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
}

# --- Ingesta de lecturas -------------------------------------------
# Tolerancia de reloj para el timestamp que reporta un dispositivo.
# No son secretos ni cambian por entorno, así que van como constantes
# simples en vez de variables de .env.
READING_TIMESTAMP_MAX_FUTURE_SECONDS = 300        # 5 minutos de adelanto
READING_TIMESTAMP_MAX_PAST_SECONDS = 7 * 24 * 3600  # 7 días de atraso