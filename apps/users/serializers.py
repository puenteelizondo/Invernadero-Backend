from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
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
