from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import serializers

from .models import Invitation, Membership

User = get_user_model()


class MembershipSerializer(serializers.ModelSerializer):
    """
    Para invitar a alguien a un invernadero necesitabas antes su `id`
    numérico de usuario (`user`), que casi nunca tienes a la mano.
    Ahora también puedes mandar `invite` con su `username` o su
    `email`, y el serializer resuelve el `id` por ti. `user` sigue
    aceptándose igual que antes (compatibilidad hacia atrás); si
    mandas ambos, `user` gana. Uno de los dos es obligatorio al crear.
    """
    username = serializers.CharField(source="user.username", read_only=True)
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)
    invite = serializers.CharField(
        write_only=True,
        required=False,
        help_text="username o email del usuario a invitar (alternativa a mandar 'user').",
    )

    class Meta:
        model = Membership
        fields = ["id", "user", "username", "invite", "greenhouse", "role", "created_at"]
        read_only_fields = ["created_at"]
        # Por defecto, ModelSerializer genera un UniqueTogetherValidator a
        # partir del UniqueConstraint(user, greenhouse) del modelo — y ESE
        # validador, al construirse, vuelve a poner required=True en 'user'
        # (y en 'greenhouse'), sin importar lo que hayamos declarado arriba.
        # Eso rompía por completo el flujo de invitar por 'invite': aunque
        # 'user' se resolvía bien en validate(), DRF exigía que también
        # viniera en el request. Lo desactivamos aquí y hacemos la misma
        # verificación de duplicados a mano, más abajo.
        validators = []

    def validate(self, attrs):
        invite = attrs.pop("invite", None)

        if attrs.get("user") is None and invite:
            resolved = User.objects.filter(
                Q(username__iexact=invite) | Q(email__iexact=invite)
            ).first()
            if resolved is None:
                raise serializers.ValidationError(
                    {"invite": f"No existe ningún usuario con username o email '{invite}'."}
                )
            attrs["user"] = resolved

        # Solo exigimos 'user' resuelto al CREAR (self.instance is None).
        # Al actualizar (ej. cambiar solo el 'role') no hace falta volver
        # a mandar a quién pertenece la membresía.
        if self.instance is None:
            if attrs.get("user") is None:
                raise serializers.ValidationError(
                    {"user": "Manda 'user' (id numérico) o 'invite' (username o email) para saber a quién invitar."}
                )
            # Mismo chequeo que hacía el UniqueTogetherValidator automático
            # (que desactivamos arriba), hecho a mano para no depender de
            # que 'user' sea obligatorio en el input.
            user = attrs["user"]
            greenhouse = attrs.get("greenhouse")
            if greenhouse and Membership.objects.filter(user=user, greenhouse=greenhouse).exists():
                raise serializers.ValidationError(
                    "Ya existe una membresía de este usuario en este invernadero."
                )

        return attrs


class InvitationSerializer(serializers.ModelSerializer):
    """
    Invitación a un invernadero. Al crear se manda `greenhouse`, `role` e
    `invite` (username o email de un usuario que ya tenga cuenta).
    """
    invite = serializers.CharField(write_only=True, help_text="username o email del usuario a invitar.")
    username = serializers.CharField(source="user.username", read_only=True)
    greenhouse_name = serializers.CharField(source="greenhouse.name", read_only=True)
    invited_by_username = serializers.CharField(source="invited_by.username", read_only=True, default=None)

    class Meta:
        model = Invitation
        fields = [
            "id", "greenhouse", "greenhouse_name", "user", "username", "invite", "role",
            "status", "invited_by_username", "created_at", "responded_at",
        ]
        read_only_fields = ["user", "status", "created_at", "responded_at"]
        validators = []   # el único "pendiente por persona" se valida abajo con un mensaje claro

    def validate(self, attrs):
        invite = attrs.pop("invite").strip()
        user = User.objects.filter(Q(username__iexact=invite) | Q(email__iexact=invite), is_active=True).first()
        if user is None:
            raise serializers.ValidationError(
                {"invite": f"No existe ninguna cuenta activa con username o email '{invite}'."}
            )
        greenhouse = attrs["greenhouse"]
        request = self.context.get("request")
        if request is not None and user.pk == request.user.pk:
            raise serializers.ValidationError({"invite": "No puedes invitarte a ti mismo."})
        if Membership.objects.filter(user=user, greenhouse=greenhouse).exists():
            raise serializers.ValidationError({"invite": f"{user.username} ya es miembro de este invernadero."})
        if Invitation.objects.filter(user=user, greenhouse=greenhouse, status=Invitation.Status.PENDING).exists():
            raise serializers.ValidationError(
                {"invite": f"{user.username} ya tiene una invitación pendiente a este invernadero."}
            )
        attrs["user"] = user
        return attrs
