from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import ReadingIngestView, ReadingViewSet

router = DefaultRouter()
router.register("readings", ReadingViewSet)

urlpatterns = [
    path(
        "readings/ingest/",
        ReadingIngestView.as_view(),
        name="reading-ingest",
    ),
] + router.urls