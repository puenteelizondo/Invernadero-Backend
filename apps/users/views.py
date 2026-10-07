import logging

from django.contrib.auth import get_user_model, login, logout
from django.conf import settings
from django.utils.html import format_html
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, permissions, viewsets
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.emails import send_email
from apps.common.frontend import frontend_base

from django.contrib.auth.password_validation import validate_password

from .serializers import (
    AdminUserCreateSerializer,
    AdminUserSerializer,
    generate_temporary_password,
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    UserSerializer,
    build_uid_and_token,
)

User = get_user_model()
logger = logging.getLogger(__name__)


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
            base = frontend_base(request)
            days = max(1, round(settings.PASSWORD_RESET_TIMEOUT / 86400))
            vigencia = f"Sirve una sola vez y caduca en {days} día{'s' if days != 1 else ''}."
            if base:
                content = dict(
                    paragraphs=[
                        format_html("Hola <strong>{}</strong>:", user.get_username()),
                        "Alguien (con suerte tú) pidió restablecer la contraseña de tu cuenta del Invernadero. "
                        "Presiona el botón para elegir una nueva.",
                    ],
                    button=("Poner contraseña nueva", f"{base}/reset-password?uid={uid}&token={token}"),
                    note=f"{vigencia} Si tú no lo pediste, ignora este correo: tu contraseña no cambia.",
                )
            else:
                content = dict(
                    paragraphs=[
                        format_html("Hola <strong>{}</strong>:", user.get_username()),
                        "Alguien (con suerte tú) pidió restablecer la contraseña de tu cuenta del Invernadero. "
                        "Abre la página «Recuperar contraseña» y usa estos datos:",
                    ],
                    details=[("uid", uid), ("token", token)],
                    note=f"{vigencia} Si tú no lo pediste, ignora este correo: tu contraseña no cambia.",
                )
            # Con el backend de consola (por defecto) el correo solo se imprime
            # en `docker compose logs web`. Para mandarlo de verdad hay que
            # configurar SMTP en el .env (ver README, sección "Correo").
            try:
                send_email(
                    [user.email],
                    "Recupera tu contraseña · Invernadero",
                    title="Recupera tu contraseña",
                    eyebrow="Seguridad de tu cuenta",
                    preheader="Elige una contraseña nueva para tu cuenta del Invernadero.",
                    footer="Recibes este correo porque se pidió recuperar la contraseña de esta cuenta.",
                    **content,
                )
            except Exception:
                # La respuesta debe ser la misma exista o no la cuenta, así que
                # el error no se le muestra a quien lo pidió: queda en el log.
                logger.exception("No se pudo mandar el correo de recuperación a %s", user.email)

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


class AdminUserViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    /api/v1/admin/users/  -- gestión de cuentas, SOLO para staff.

    Es la única vía para crear usuarios: el registro público fue cerrado.
    No hay DELETE a propósito: una cuenta con historial (cambios de
    actuadores, auditoría de lazos) no se borra, se DESACTIVA
    (`is_active=false`), lo que además impide iniciar sesión.

    Acciones:
      GET    /admin/users/?search=            lista (con buscador)
      POST   /admin/users/                    crea (devuelve la contraseña
                                              temporal UNA sola vez si se generó)
      PATCH  /admin/users/{id}/               email, is_staff, is_active
      POST   /admin/users/{id}/reset-password/  genera otra contraseña temporal
    """

    permission_classes = [permissions.IsAdminUser]
    queryset = User.objects.all().order_by("username")
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    search_fields = ["username", "email", "first_name", "last_name"]
    filterset_fields = ["is_staff", "is_active"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return AdminUserCreateSerializer
        return AdminUserSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        data = AdminUserSerializer(user).data
        # La temporal sale en la respuesta una sola vez; no se guarda en claro.
        data["temporary_password"] = getattr(user, "_temporary_password", None)
        return Response(data, status=201)

    def perform_update(self, serializer):
        target = serializer.instance
        if target.pk == self.request.user.pk:
            incoming = serializer.validated_data
            if incoming.get("is_active") is False or incoming.get("is_staff") is False:
                from rest_framework.exceptions import ValidationError

                raise ValidationError(
                    "No puedes desactivarte ni quitarte el rol de administrador a ti mismo."
                )
        serializer.save()

    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        user = self.get_object()
        raw = request.data.get("password") if hasattr(request.data, "get") else None
        generated = not raw
        raw = raw or generate_temporary_password()
        if not generated:
            validate_password(raw, user)
        user.set_password(raw)
        user.save(update_fields=["password"])
        return Response(
            {"id": user.pk, "username": user.username, "temporary_password": raw if generated else None}
        )
