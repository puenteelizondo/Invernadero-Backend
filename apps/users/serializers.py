from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import serializers

User = get_user_model()


class LoginSerializer(serializers.Serializer):
    """
    Para POST /api/v1/auth/login/ (ver views.py::SessionLoginView).

    Es un login por SESIÓN (cookie), pensado para el frontend -- no
    reemplaza a BasicAuthentication (que sigue funcionando igual, para
    Postman/scripts). `authenticate()` es la misma función que usa
    Django internamente para /admin/ y para BasicAuthentication: no
    reinventa cómo se valida usuario+contraseña.
    """
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate(self, attrs):
        user = authenticate(
            self.context["request"], username=attrs["username"], password=attrs["password"]
        )
        if user is None:
            raise serializers.ValidationError("Usuario o contraseña incorrectos.")
        if not user.is_active:
            raise serializers.ValidationError("Esta cuenta está desactivada.")
        attrs["user"] = user
        return attrs


class UserSerializer(serializers.ModelSerializer):
    """Para GET /api/v1/auth/me/ -- lo mínimo que el frontend necesita saber de sí mismo."""

    class Meta:
        model = User
        fields = ["id", "username", "email", "is_staff"]


class AdminUserSerializer(serializers.ModelSerializer):
    """Lectura/edición de cuentas por un administrador."""

    class Meta:
        model = User
        fields = [
            "id", "username", "email", "first_name", "last_name",
            "is_staff", "is_active", "last_login", "date_joined",
        ]
        read_only_fields = ["id", "username", "last_login", "date_joined"]

    def validate_email(self, value):
        qs = User.objects.filter(email__iexact=value) if value else User.objects.none()
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Ya existe una cuenta con este email.")
        return value


def generate_temporary_password() -> str:
    """Contraseña temporal legible (sin caracteres ambiguos) que cumple los validadores."""
    import secrets

    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(14))


class AdminUserCreateSerializer(AdminUserSerializer):
    """
    Alta de usuario por un administrador (POST /api/v1/admin/users/).

    `password` es opcional: si no se manda, se genera una temporal que la
    respuesta muestra una sola vez. Se valida con AUTH_PASSWORD_VALIDATORS.
    """

    password = serializers.CharField(
        write_only=True, required=False, allow_blank=True, style={"input_type": "password"}
    )

    class Meta(AdminUserSerializer.Meta):
        fields = AdminUserSerializer.Meta.fields + ["password"]
        read_only_fields = ["id", "last_login", "date_joined"]
        extra_kwargs = {"email": {"required": False}}

    def validate(self, attrs):
        raw = attrs.get("password")
        if raw:
            probe = User(username=attrs.get("username", ""), email=attrs.get("email", ""))
            try:
                validate_password(raw, probe)
            except Exception as exc:  # DjangoValidationError -> 400 de DRF
                raise serializers.ValidationError({"password": list(getattr(exc, "messages", [str(exc)]))})
        return attrs

    def create(self, validated_data):
        raw = validated_data.pop("password", "") or ""
        generated = not raw
        if generated:
            raw = generate_temporary_password()
        user = User(**validated_data)
        user.set_password(raw)
        user.save()
        user._temporary_password = raw if generated else None
        return user


class PasswordResetRequestSerializer(serializers.Serializer):
    """
    Para POST /api/v1/auth/password-reset/ (ver
    views.py::PasswordResetRequestView).

    A propósito NO valida que el email exista -- decirle a quien
    manda la petición "ese email no está registrado" es una fuga de
    información (permite enumerar qué correos tienen cuenta). La
    vista siempre responde igual, exista o no el usuario.
    """
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    """
    Para POST /api/v1/auth/password-reset/confirm/ (ver
    views.py::PasswordResetConfirmView).

    `uid` + `token` son exactamente lo que Django genera para sus
    propias vistas de reseteo de contraseña (uid64 del id del usuario,
    token de un solo uso con `default_token_generator`) -- no es un
    mecanismo inventado para este proyecto, es el mismo que trae
    Django desde hace años, solo que aquí lo exponemos como API en vez
    de como vistas HTML con formularios.
    """
    uid = serializers.CharField()
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate(self, attrs):
        try:
            user_id = force_str(urlsafe_base64_decode(attrs["uid"]))
            user = User.objects.get(pk=user_id)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            raise serializers.ValidationError({"uid": "Enlace de recuperación inválido."})

        if not default_token_generator.check_token(user, attrs["token"]):
            raise serializers.ValidationError(
                {"token": "El enlace de recuperación es inválido o ya expiró. Pide uno nuevo."}
            )

        attrs["user"] = user
        return attrs

    def save(self):
        user = self.validated_data["user"]
        user.set_password(self.validated_data["password"])
        user.save(update_fields=["password"])
        return user


def build_uid_and_token(user):
    """
    Compartido por PasswordResetRequestView: arma el par (uid, token)
    que el cliente tendrá que mandar de vuelta a
    PasswordResetConfirmSerializer. Vive aquí (no en views.py) para
    quedar junto al código que después lo consume/valida.
    """
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return uid, token
