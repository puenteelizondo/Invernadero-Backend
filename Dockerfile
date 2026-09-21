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

# Ejecutar como usuario normal, no como root
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

# Comando por defecto. Lo cambiaremos por Daphne (servidor ASGI) en la Etapa 7.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]