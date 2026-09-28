from django.urls import path

from .views import WebSocketTokenView

urlpatterns = [
    path("realtime/ws-token/", WebSocketTokenView.as_view(), name="ws-token"),
]
