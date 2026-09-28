# Imagen base: Python 3.13 sobre Debian, variante "slim" (sin extras innecesarios)
FROM python:3.13-slim

# PYTHONDONTWRITEBYTECODE: no generar archivos .pyc dentro del contenedor
# PYTHONUNBUFFERED: mostrar los logs al instante en `docker compose logs`
# PIP_*: instalar sin caché ni avisos, para una imagen más pequeña y limpia
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Carpeta de trabajo dentro del contenedor
WORKDIR /app

# 1) Primero solo requirements.txt: mientras no cambie, Docker reutiliza esta
#    capa y no reinstala todo cada vez que editas código.
COPY requirements.txt .
RUN pip install -r requirements.txt

# 2) Después el código del proyecto
COPY . .

# Ejecutar como usuario normal, no como root. chown es necesario: COPY
# de arriba deja todo /app como root:root, y sin esto un comando que
# necesite escribir ahí (ej. collectstatic en producción, ver
# docker-compose.prod.yml) fallaría por permisos al correr como appuser.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Comando por defecto -- el que usa docker-compose.yml (desarrollo).
# El propio `runserver` delega en Daphne automáticamente al detectar
# channels/ASGI_APPLICATION instalado (lo ves en los logs al arrancar:
# "Starting ASGI/Daphne..."), así que esto YA sirve WebSockets en
# desarrollo -- pero sigue siendo el servidor de desarrollo de Django,
# no apto para producción (Django lo advierte al arrancar). Para
# producción, docker-compose.prod.yml sobreescribe este CMD para
# invocar Daphne directamente (sin el autoreloader) y correr
# migrate/collectstatic antes de levantar el servidor.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]