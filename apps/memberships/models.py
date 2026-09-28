from django.db import models


class Membership(models.Model):
    """
    Etapa 12: quién tiene acceso a qué invernadero, y con qué rol.

    Es la pieza central del modelo multi-tenant: en vez de que un
    admin vea/controle todo, cada usuario solo ve los invernaderos
    donde tiene una fila aquí. Cuando construyas el frontend y un
    cliente registre su invernadero, se crea automáticamente una
    Membership con role=OWNER para ese usuario (ver
    GreenhouseViewSet.perform_create) — así es como "cada cliente
    administra el suyo" sin que tengas que asignarlo a mano.
    """

    class Role(models.TextChoices):
        OWNER = "owner", "Propietario"
        OPERATOR = "operator", "Operador"
        VIEWER = "viewer", "Solo lectura"

    user = models.ForeignKey(
        "users.User", on_delete=models.CASCADE, related_name="memberships"
    )
    greenhouse = models.ForeignKey(
        "greenhouses.Greenhouse", on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "greenhouse"], name="unique_membership_user_greenhouse"
            )
        ]
        ordering = ["greenhouse_id", "role"]

    def __str__(self):
        return f"{self.user} → {self.greenhouse} ({self.role})"