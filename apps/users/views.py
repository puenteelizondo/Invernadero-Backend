from django.contrib.auth import get_user_model, login, logout
from django.core.mail import send_mail
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import (
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    UserSerializer,
    build_uid_and_token,
)

User = get_user_model()


class RegisterView(generics.CreateAPIView):
    """
    POST /api/v1/auth/register/

    Antes, crear un usuario nuevo dependía de `createsuperuser` o del
    admin -- no había forma de que alguien se registrara por sí mismo.
    Este endpoint es público a propósito (AllowAny, sin
    authentication_classes): es la puerta de entrada para que un
    cliente nuevo cree su propia cuenta.

    No crea ninguna Membership ni invernadero -- eso pasa después, ya
    sea porque el usuario registra su propio invernadero (ver
    GreenhouseViewSet.perform_create, que sí crea una Membership OWNER
    automáticamente) o porque alguien más lo invita a uno existente
    (ver apps/memberships).

    Comparte el AnonRateThrottle global (60/minute por IP, ver
    config/settings/base.py) igual que cualquier otro endpoint público
    -- no tiene un límite propio porque no hay ninguna razón para que
    el registro necesite ser más permisivo o más estricto que el resto.
    """
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []


class PasswordResetRequestView(APIView):
    """
    POST /api/v1/auth/password-reset/

    Pide un email; si existe una cuenta con ese email, le manda un
    correo con un enlace de un solo uso para poner contraseña nueva
    (ver PasswordResetConfirmView). Público, sin autenticación previa
    (obviamente -- si ya pudieras loguearte no necesitarías esto).

    Responde EXACTAMENTE IGUAL exista o no el email, con el mismo
    código y el mismo mensaje -- si la respuesta variara según si el
    email está registrado, cualquiera podría usar este endpoint para
    averiguar qué correos tienen cuenta en el sistema (enumeración de
    usuarios). El correo en sí solo se manda si el usuario existe.
    """
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is not None:
            uid, token = build_uid_and_token(user)
            # EMAIL_BACKEND por defecto en desarrollo es la consola
            # (config/settings/dev.py) -- el correo se imprime en los
            # logs de `docker compose logs web` en vez de enviarse de
            # verdad. En producción, config/settings/prod.py exige un
            # EMAIL_BACKEND real (SMTP) por variables de entorno.
            send_mail(
                subject="Recuperar tu contraseña -- Invernadero",
                message=(
                    "Alguien (con suerte tú) pidió restablecer la contraseña "
                    "de esta cuenta.\n\n"
                    "Para poner una contraseña nueva, manda un POST a "
                    "/api/v1/auth/password-reset/confirm/ con este uid y "
                    "token (válidos por un tiempo limitado y de un solo uso):\n\n"
                    f"uid: {uid}\n"
                    f"token: {token}\n\n"
                    "Si tú no pediste esto, puedes ignorar este correo."
                ),
                from_email=None,  # usa DEFAULT_FROM_EMAIL
                recipient_list=[email],
                fail_silently=False,
            )

        return Response(
            {"detail": "Si el email está registrado, se mandó un correo con instrucciones."}
        )


class PasswordResetConfirmView(APIView):
    """
    POST /api/v1/auth/password-reset/confirm/

    Recibe el uid+token que llegaron por correo (ver
    PasswordResetRequestView) más la contraseña nueva, y la aplica.
    El token es de un solo uso: default_token_generator lo invalida
    en cuanto cambia algo del usuario (incluida la propia contraseña),
    así que no hace falta guardar/borrar nada aparte.
    """
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"detail": "Contraseña actualizada."})


@method_decorator(ensure_csrf_cookie, name="get")
class CsrfCookieView(APIView):
    """
    GET /api/v1/auth/csrf/

    No hace nada más que forzar que Django mande la cookie `csrftoken`
    en la respuesta (`ensure_csrf_cookie`). El frontend llama esto UNA
    vez al arrancar, antes de intentar login -- sin la cookie, no hay
    token que mandar de vuelta en el header `X-CSRFToken`, y Django
    rechazaría el POST de login con 403 (CsrfViewMiddleware ya está
    activo para todo el proyecto, ver MIDDLEWARE en settings).
    """
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        # get_token() además marca la cookie como "usada", lo que
        # Django necesita para decidir mandarla en la respuesta.
        return Response({"detail": "Cookie CSRF puesta.", "csrfToken": get_token(request)})


class SessionLoginView(APIView):
    """
    POST /api/v1/auth/login/

    Login por SESIÓN (cookie), pensado para el frontend -- requiere
    haber llamado antes a GET /api/v1/auth/csrf/ y mandar esa cookie
    de vuelta como header X-CSRFToken (ver CsrfCookieView). No
    reemplaza BasicAuthentication, que sigue funcionando igual para
    Postman/scripts.
    """
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        login(request, user)
        return Response(UserSerializer(user).data)


class LogoutView(APIView):
    """POST /api/v1/auth/logout/ -- cierra la sesión actual."""

    def post(self, request):
        logout(request)
        return Response({"detail": "Sesión cerrada."})


class MeView(APIView):
    """
    GET /api/v1/auth/me/

    El frontend llama esto al arrancar para saber si ya hay una sesión
    activa (200 con el usuario) o no (401, gracias a IsAuthenticated
    por defecto) -- así decide si mostrar la pantalla de login o la
    app, sin tener que "adivinar" leyendo cookies desde JS.
    """

    def get(self, request):
        return Response(UserSerializer(request.user).data)
