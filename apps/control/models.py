from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class ControlLoop(models.Model):
    """
    Un lazo de control (On/Off, P, PI o PID) que EJECUTA el controlador
    (ESP32/Arduino), no el servidor.

    El backend solo guarda, valida, versiona y entrega esta configuración al
    dispositivo, y retransmite la telemetría que el dispositivo reporta. La
    salida del lazo jamás se calcula aquí.
    """

    class Mode(models.TextChoices):
        OFF = "off", "Apagado"
        ON_OFF = "on_off", "On/Off"
        P = "p", "P"
        PI = "pi", "PI"
        PID = "pid", "PID"

    class Direction(models.TextChoices):
        # Subir la salida SUBE la variable (calefactor, humidificador, luz).
        DIRECT = "direct", "Directa (la salida sube la variable)"
        # Subir la salida BAJA la variable (ventilador/enfriador, deshumidificador).
        REVERSE = "reverse", "Inversa (la salida baja la variable)"

    greenhouse = models.ForeignKey(
        "greenhouses.Greenhouse", on_delete=models.CASCADE, related_name="control_loops"
    )
    name = models.CharField(max_length=100)
    # PROTECT (no CASCADE): borrar el sensor, el actuador o el dispositivo de un
    # lazo se niega con 409 ("tiene 1 lazo de control"), igual que el resto de
    # la app. Con CASCADE los lazos desaparecían en silencio al borrar o
    # purgar uno de ellos.
    sensor = models.ForeignKey(
        "sensors.Sensor", on_delete=models.PROTECT, related_name="control_loops",
        help_text="Variable de proceso (PV) que el controlador lee.",
    )
    actuator = models.ForeignKey(
        "actuators.Actuator", on_delete=models.PROTECT, related_name="control_loops",
        help_text="Salida que maneja el controlador.",
    )
    device = models.ForeignKey(
        "sensors.Device", on_delete=models.PROTECT, related_name="control_loops",
        help_text="Dispositivo que ejecuta el lazo.",
    )

    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.OFF)
    direction = models.CharField(max_length=10, choices=Direction.choices, default=Direction.DIRECT)
    setpoint = models.FloatField()
    hysteresis = models.FloatField(default=0.5, help_text="Solo On/Off.")
    kp = models.FloatField(default=1.0)
    ki = models.FloatField(default=0.0)
    kd = models.FloatField(default=0.0)
    output_min = models.FloatField(default=0.0)
    output_max = models.FloatField(default=100.0)
    integral_limit = models.FloatField(
        default=100.0, help_text="Límite del término integral (anti-windup), en unidades de salida."
    )
    sample_time_ms = models.PositiveIntegerField(default=1000)
    enabled = models.BooleanField(default=False)

    # Sube en CADA cambio de configuración; el dispositivo la confirma con `ack`.
    version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    applied_version = models.PositiveIntegerField(default=0)
    applied_at = models.DateTimeField(null=True, blank=True)

    # Campos que forman parte de la configuración que viaja al dispositivo.
    CONFIG_FIELDS = (
        "name", "sensor_id", "actuator_id", "device_id", "mode", "direction",
        "setpoint", "hysteresis", "kp", "ki", "kd", "output_min", "output_max",
        "integral_limit", "sample_time_ms", "enabled",
    )

    class Meta:
        ordering = ["greenhouse", "name"]
        constraints = [
            # Un actuador lo maneja a lo sumo UN lazo: dos lazos sobre la misma
            # salida se pelearían entre sí.
            models.UniqueConstraint(fields=["actuator"], name="controlloop_one_per_actuator"),
            models.CheckConstraint(
                condition=Q(output_min__lt=models.F("output_max")),
                name="controlloop_output_range",
            ),
        ]

    def __str__(self):
        return f"{self.name} [{self.mode}] sp={self.setpoint}"

    def clean(self):
        errors = {}
        if self.sensor_id and self.sensor.greenhouse_id != self.greenhouse_id:
            errors["sensor"] = "El sensor debe pertenecer al mismo invernadero."
        if self.actuator_id and self.actuator.greenhouse_id != self.greenhouse_id:
            errors["actuator"] = "El actuador debe pertenecer al mismo invernadero."
        if self.device_id and self.device.greenhouse_id != self.greenhouse_id:
            errors["device"] = "El dispositivo debe pertenecer al mismo invernadero."
        if errors:
            raise ValidationError(errors)

    def device_payload(self) -> dict:
        """Lo que recibe el dispositivo: configuración completa + versión."""
        return {
            "id": self.pk,
            "version": self.version,
            "name": self.name,
            "sensor_id": self.sensor_id,
            "actuator_id": self.actuator_id,
            "mode": self.mode,
            "direction": self.direction,
            "setpoint": self.setpoint,
            "hysteresis": self.hysteresis,
            "kp": self.kp,
            "ki": self.ki,
            "kd": self.kd,
            "output_min": self.output_min,
            "output_max": self.output_max,
            "integral_limit": self.integral_limit,
            "sample_time_ms": self.sample_time_ms,
            "enabled": self.enabled,
        }


class ControlLoopChange(models.Model):
    """Historial: quién cambió qué en un lazo, con valores antes y después."""

    loop = models.ForeignKey(ControlLoop, on_delete=models.CASCADE, related_name="changes")
    version = models.PositiveIntegerField()
    changed_by = models.ForeignKey(
        "users.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # {"setpoint": {"before": 24, "after": 26}, ...}
    changes = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["loop", "-created_at"], name="ctrlchange_loop_idx")]

    def __str__(self):
        return f"{self.loop_id} v{self.version}"
