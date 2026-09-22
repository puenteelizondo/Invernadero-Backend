from django.db import models


class Greenhouse(models.Model):
    """
    Un invernadero físico. Es el nivel más alto de organización:
    todo (zonas, dispositivos, sensores) cuelga de un invernadero.
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    # Zona horaria del invernadero. Las lecturas se guardan siempre
    # en UTC (ver settings.TIME_ZONE); este campo solo se usa para
    # MOSTRAR fechas al usuario y para la exportación a Excel.
    # Es un CharField libre en vez de un paquete como django-timezone-field
    # porque agregar una dependencia solo para validar un string
    # no se justifica todavía; el valor se valida en clean().
    timezone = models.CharField(max_length=64, default="UTC")

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        import zoneinfo
        from django.core.exceptions import ValidationError

        if self.timezone not in zoneinfo.available_timezones():
            raise ValidationError(
                {"timezone": f"'{self.timezone}' no es una zona horaria IANA válida."}
            )


class Zone(models.Model):
    """
    Subdivisión de un invernadero (ej. 'Mesa Norte', 'Túnel 2').
    Opcional: un sensor puede no pertenecer a ninguna zona.
    """
    greenhouse = models.ForeignKey(
        Greenhouse, on_delete=models.PROTECT, related_name="zones"
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["greenhouse", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["greenhouse", "name"], name="unique_zone_name_per_greenhouse"
            )
        ]

    def __str__(self):
        return f"{self.greenhouse.name} / {self.name}"