from django.db import models


class ActuatorType(models.Model):
    """
    Catálogo de tipos de actuador (ventilador, bomba, válvula...).
    Mismo patrón que SensorType: agregar un tipo nuevo es una fila,
    no un despliegue de código.
    """
    code = models.SlugField(max_length=50, unique=True, help_text="Ej: 'fan'")
    name = models.CharField(max_length=100, help_text="Ej: 'Ventilador'")
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Actuator(models.Model):
    """
    Un actuador controlable. `state` es el estado ON/OFF actual,
    tratado como una caché de lectura rápida: la fuente de verdad
    histórica vive en ActuatorStateHistory, y ambas se actualizan
    siempre juntas a través de set_state(), nunca por separado.
    """
    name = models.CharField(max_length=100)
    actuator_type = models.ForeignKey(
        ActuatorType, on_delete=models.PROTECT, related_name="actuators"
    )
    device = models.ForeignKey(
        "sensors.Device", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="actuators",
        help_text="Dispositivo físico que controla este actuador (puede ser "
                   "el mismo que reporta sensores, ej. un relé en el mismo ESP32).",
    )
    greenhouse = models.ForeignKey(
        "greenhouses.Greenhouse", on_delete=models.PROTECT, related_name="actuators"
    )
    zone = models.ForeignKey(
        "greenhouses.Zone", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="actuators",
    )

    description = models.TextField(blank=True)

    # ¿Está registrado/disponible para operarse? (equivalente a Sensor.is_active)
    is_active = models.BooleanField(default=True)

    # ¿Está encendido ahora mismo? NO editar directamente: usar set_state().
    state = models.BooleanField(default=False)

    # Configuración específica del tipo (speed_levels, max_runtime_seconds,
    # normally_open...). Ver justificación de JSONField en el análisis de
    # esta etapa: mismo criterio que Sensor.config.
    config = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["greenhouse", "name"]

    def __str__(self):
        return f"{self.name} ({'ON' if self.state else 'OFF'})"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.zone_id and self.zone.greenhouse_id != self.greenhouse_id:
            raise ValidationError(
                {"zone": "La zona debe pertenecer al mismo invernadero que el actuador."}
            )

    def set_state(self, new_state: bool, *, user=None,
                  source: str = "manual") -> bool:
        """
        Único punto de entrada para cambiar el estado. Actualiza el
        campo `state` (para lecturas rápidas) y crea el registro de
        historial (para auditoría) en la misma operación, así nunca
        quedan desincronizados.

        Devuelve True si hubo un cambio real (y por lo tanto debe
        publicarse un evento de WebSocket); False si el estado
        pedido ya era el actual, para evitar ruido de eventos y
        entradas de historial sin cambios reales.
        """
        if new_state == self.state:
            return False

        self.state = new_state
        self.save(update_fields=["state", "updated_at"])

        ActuatorStateHistory.objects.create(
            actuator=self, state=new_state, changed_by=user, source=source,
        )
        return True


class ActuatorStateHistory(models.Model):
    """
    Bitácora de cada cambio de estado. A diferencia de Reading, no
    lleva restricción de unicidad por timestamp: varios cambios de
    estado legítimos pueden ocurrir en el mismo segundo (ej. un
    usuario corrigiendo un error inmediato), y cada uno es un evento
    real que vale la pena conservar.
    """

    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        AUTOMATION = "automation", "Automatización"

    actuator = models.ForeignKey(
        Actuator, on_delete=models.PROTECT, related_name="state_history"
    )
    state = models.BooleanField()
    changed_by = models.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="actuator_changes",
    )
    source = models.CharField(
        max_length=20, choices=Source.choices, default=Source.MANUAL,
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]
        indexes = [
            models.Index(fields=["actuator", "-changed_at"], name="actuator_hist_idx"),
        ]

    def __str__(self):
        return f"{self.actuator.name} → {'ON' if self.state else 'OFF'} @ {self.changed_at}"