from django.db import models


class Reading(models.Model):
    """
    Una lectura histórica persistida de un sensor.

    Es intencionalmente ANGOSTA (pocas columnas): entre más estrecha
    sea una tabla, más filas caben por página de disco y más rápidas
    son las consultas por rango. No repetimos aquí el tipo de
    sensor, la unidad o la ubicación: eso se obtiene por JOIN con
    Sensor cuando se necesita, porque cambia poco y consultar por
    JOIN es barato comparado con el volumen de escritura de esta
    tabla.
    """
    sensor = models.ForeignKey(
        "sensors.Sensor", on_delete=models.PROTECT, related_name="readings"
    )
    timestamp = models.DateTimeField()
    value = models.FloatField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        constraints = [
            models.UniqueConstraint(
                fields=["sensor", "timestamp"], name="unique_reading_per_sensor_timestamp"
            )
        ]
        indexes = [
            # Cubre las consultas por sensor + rango de fechas, que
            # son el patrón de consulta principal del sistema
            # (histórico, exportación a Excel).
            models.Index(fields=["sensor", "-timestamp"], name="reading_sensor_ts_idx"),
        ]

    def __str__(self):
        return f"{self.sensor.name} @ {self.timestamp}: {self.value}"