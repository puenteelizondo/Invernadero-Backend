# apps/realtime/routing.py
from django.urls import re_path

from .consumers import GreenhouseConsumer

websocket_urlpatterns = [
    re_path(r"^ws/greenhouses/(?P<greenhouse_id>\d+)/$", GreenhouseConsumer.as_asgi()),
]