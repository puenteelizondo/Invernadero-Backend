from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import ReadingExportView, ReadingIngestView, ReadingViewSet

router = DefaultRouter()
router.register("readings", ReadingViewSet)

# Importante: las rutas literales (ingest/, export/) van ANTES que
# router.urls. El router genera una ruta genérica
# "readings/<pk>/" para el detalle (GET /readings/<id>/) que acepta
# cualquier texto como <pk> — si router.urls fuera primero, Django
# probaría esa ruta antes que las nuestras, "export" calzaría como si
# fuera un id de lectura, y nunca llegaríamos a ReadingExportView (así
# fue como se manifestó el bug: 404 "No encontrado", el mensaje que da
# DRF cuando busca una lectura con pk="export" y no la encuentra).
urlpatterns = [
    path("readings/ingest/", ReadingIngestView.as_view(), name="reading-ingest"),
    path("readings/export/", ReadingExportView.as_view(), name="reading-export"),
] + router.urls