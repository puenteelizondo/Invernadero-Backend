from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .tokens import WS_TOKEN_TTL_SECONDS, issue_ws_token


class WebSocketTokenView(APIView):
    """
    POST /api/v1/realtime/ws-token/

    El frontend llama esto ya autenticado por HTTP (Basic Auth o sesión,
    lo que ya existe) para obtener un token de un solo uso con el que
    abrir el WebSocket. El WebSocket en sí no vuelve a pasar por
    SessionAuthentication/BasicAuthentication: el token es lo único que
    GreenhouseConsumer.connect() acepta como prueba de identidad.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = issue_ws_token(request.user)
        return Response({"token": token, "expires_in": WS_TOKEN_TTL_SECONDS})
