from django.conf import settings
from django.db import models


class AlertRule(models.Model):
    """
    Regla de alerta de UN sensor: avisa cuando el valor sale de
    [min_value, max_value]. Cualquiera de los dos límites puede faltar
    (pero no ambos).

    `duration_seconds` evita falsas alarmas: la condición debe mantenerse
    de forma continua ese tiempo antes de abrir la alerta (0 = al primer
    valor fuera de límites).

    El estado de evaluación vive aquí mismo (no en caché), así sobrevive
    a reinicios y la ingesta lo recibe junto con la regla, sin consultas
    extra:
    - `breach_since`: desde cuándo está fuera de límites (None si está bien).
    - `active_alert`: la alerta abierta ahora mismo, si la hay.
    """

    class Severity(models.TextChoices):
        WARNING = "warning", "Aviso"
        CRITICAL = "critical", "Crítica"

    class RuleType(models.TextChoices):
        THRESHOLD = "threshold", "Fuera de límites"
        NO_SIGNAL = "no_signal", "Sin señal"

    sensor = models.ForeignKey("sensors.Sensor", on_delete=models.CASCADE, related_name="alert_rules")
    # THRESHOLD: min/max (+ duration_seconds sostenida). NO_SIGNAL: avisa si el
    # sensor lleva más de `duration_seconds` sin mandar lecturas (sin límites).
    rule_type = models.CharField(max_length=10, choices=RuleType.choices, default=RuleType.THRESHOLD)
    # Copia del invernadero del sensor: permite filtrar por invernadero
    # (GreenhouseScopedMixin) sin hacer un JOIN en cada consulta.
    greenhouse = models.ForeignKey(
        "greenhouses.Greenhouse", on_delete=models.CASCADE, related_name="alert_rules", editable=False
    )
    name = models.CharField(max_length=100, blank=True, help_text="Opcional; si se deja vacío se genera uno.")
    min_value = models.FloatField(null=True, blank=True, help_text="Alerta si el valor baja de este número.")
    max_value = models.FloatField(null=True, blank=True, help_text="Alerta si el valor supera este número.")
    duration_seconds = models.PositiveIntegerField(default=0)
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.WARNING)
    is_active = models.BooleanField(default=True)
    notify_email = models.BooleanField(default=True)

    breach_since = models.DateTimeField(null=True, blank=True, editable=False)
    active_alert = models.ForeignKey(
        "alerts.Alert", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sensor_id", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rule_type="no_signal")
                | models.Q(min_value__isnull=False)
                | models.Q(max_value__isnull=False),
                name="alertrule_has_a_limit",
            ),
        ]

    def __str__(self):
        return self.name or f"Regla #{self.pk} ({self.sensor_id})"


class Alert(models.Model):
    """Un episodio de alerta: se abre al incumplirse una regla y se cierra solo al volver a la normalidad."""

    class Kind(models.TextChoices):
        HIGH = "high", "Por encima del máximo"
        LOW = "low", "Por debajo del mínimo"
        # Para STALE: threshold = segundos de silencio tolerados; trigger_value y
        # peak_value = segundos sin lecturas (peak se va actualizando).
        STALE = "stale", "Sin señal"

    class Status(models.TextChoices):
        ACTIVE = "active", "Activa"
        RESOLVED = "resolved", "Resuelta"

    # SET_NULL: borrar la regla no borra el historial de lo que pasó.
    rule = models.ForeignKey(AlertRule, null=True, on_delete=models.SET_NULL, related_name="alerts")
    sensor = models.ForeignKey("sensors.Sensor", on_delete=models.CASCADE, related_name="alerts")
    greenhouse = models.ForeignKey("greenhouses.Greenhouse", on_delete=models.CASCADE, related_name="alerts")
    kind = models.CharField(max_length=5, choices=Kind.choices)
    severity = models.CharField(max_length=10, choices=AlertRule.Severity.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    threshold = models.FloatField(help_text="El límite que se cruzó.")
    trigger_value = models.FloatField(help_text="Valor que abrió la alerta.")
    peak_value = models.FloatField(help_text="Valor más extremo mientras estuvo abierta.")
    opened_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-opened_at", "-id"]
        indexes = [
            models.Index(fields=["greenhouse", "status", "-opened_at"], name="alert_gh_status_idx"),
            models.Index(fields=["sensor", "-opened_at"], name="alert_sensor_idx"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} · sensor {self.sensor_id} · {self.status}"
