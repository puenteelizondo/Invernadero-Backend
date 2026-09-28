from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import serializers

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    """
    Para POST /api/v1/auth/register/ (ver views.py::RegisterView).

    `username` ya es único por su cuenta (AbstractUser lo declara con
    unique=True) -- ModelSerializer le agrega un UniqueValidator solo.
    `email` NO es único en el modelo de Django por defecto, así que lo
    validamos aquí a mano para no permitir dos cuentas con el mismo
    correo (sin tener que tocar el modelo ni agregar una migración).
    """
    password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
        help_text="Se valida con AUTH_PASSWORD_VALIDATORS (misma política que usa el admin de Django).",
    )

    class Meta:
        model = User
        fields = ["id", "username", "email", "password"]
        extra_kwargs = {"email": {"required": False}}

    def validate_password(self, value):
        # Corre las mismas 4 validaciones que ya declara
        # AUTH_PASSWORD_VALIDATORS en settings (similitud con el
        # usuario, longitud mínima, contraseñas comunes, no-solo-
        # numérica) -- las mismas que aplican al crear un usuario por
        # el admin, no una política nueva inventada para este endpoint.
        validate_password(value)
        return value

    def validate_email(self, value):
        if value and User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Ya existe una cuenta con este email.")
        return value

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
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
