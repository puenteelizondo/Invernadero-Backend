import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models


class SensorType(models.Model):
    """
    Catálogo de tipos de sensor (temperatura, humedad, CO2, pH...).

    Es la pieza clave de la escalabilidad pedida: agregar un tipo
    de sensor nuevo es INSERTAR UNA FILA aquí (por API o admin),
    nunca escribir código ni migrar la base de datos.
    """
    code = models.SlugField(max_length=50, unique=True, help_text="Ej: 'temperature'")
    name = models.CharField(max_length=100, help_text="Ej: 'Temperatura'")
    default_unit = models.CharField(max_length=20, help_text="Ej: '°C', '%', 'ppm'")

    # Rango físico plausible. Se usa para rechazar lecturas absurdas
    # (ej. una 'temperatura' de 9999°C es casi seguro un sensor roto
    # o un error de transmisión). Ambos son opcionales porque no
    # todos los tipos tienen un rango físico obvio.
    valid_min = models.FloatField(null=True, blank=True)
    valid_max = models.FloatField(null=True, blank=True)

    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Device(models.Model):
    """
    Un dispositivo físico (ej. un ESP32) que agrupa uno o más
    sensores y se autentica ante el backend para enviar lecturas.

    La API key se guarda hasheada (igual que una contraseña):
    nunca se almacena en texto plano, ni siquiera en la base de
    datos. Solo se muestra una vez, al crearla.
    """
    name = models.CharField(max_length=100)
    greenhouse = models.ForeignKey(
        "greenhouses.Greenhouse", on_delete=models.PROTECT, related_name="devices"
    )

    key_prefix = models.CharField(max_length=8, unique=True, editable=False)
    api_key_hash = models.CharField(max_length=128, editable=False)

    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.key_prefix})"

    def set_api_key(self):
        """
        Genera una API key nueva, la hashea para guardarla, y
        devuelve la clave EN TEXTO PLANO para mostrarla una sola vez.
        A partir de aquí, nadie (ni el propio backend) puede
        recuperarla; solo se puede verificar o rotar.
        """
        raw_key = secrets.token_urlsafe(32)
        self.key_prefix = raw_key[:8]
        self.api_key_hash = make_password(raw_key)
        return raw_key

    def check_api_key(self, raw_key: str) -> bool:
        return check_password(raw_key, self.api_key_hash)


class Sensor(models.Model):
    """
    Un sensor individual. Es el modelo genérico que reemplaza a
    tener una tabla distinta por cada tipo de sensor.
    """
    name = models.CharField(max_length=100)
    sensor_type = models.ForeignKey(
        SensorType, on_delete=models.PROTECT, related_name="sensors"
    )
    device = models.ForeignKey(
        Device, on_delete=models.SET_NULL, null=True, blank=True, related_name="sensors"
    )
    greenhouse = models.ForeignKey(
        "greenhouses.Greenhouse", on_delete=models.PROTECT, related_name="sensors"
    )
    zone = models.ForeignKey(
        "greenhouses.Zone", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="sensors",
    )

    unit = models.CharField(
        max_length=20, blank=True,
        help_text="Si se deja vacío, se usa la unidad por defecto del tipo de sensor.",
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    reading_interval_seconds = models.PositiveIntegerField(
        default=60, help_text="Cada cuántos segundos se espera una lectura de este sensor.",
    )

    # --- Política de guardado (ver Etapa 1, sección de almacenamiento) ---
    persist_interval_seconds = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Guardar como máximo 1 lectura cada N segundos. "
                   "Vacío = nunca guardar (solo tiempo real). 0 = guardar todo.",
    )
    persist_deadband = models.FloatField(
        null=True, blank=True,
        help_text="Guardar también si el valor cambia al menos esto desde el último guardado.",
    )

    # Configuración específica del tipo de sensor que NO necesitamos
    # filtrar ni consultar (calibración, pin, ganancia, offset...).
    # Ver Etapa 1: JSONField se justifica aquí porque su forma varía
    # por tipo de sensor y no es un dato por el que se busque.
    config = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["greenhouse", "name"]

    def __str__(self):
        return f"{self.name} ({self.sensor_type.name})"

    def clean(self):
        if self.zone_id and self.zone.greenhouse_id != self.greenhouse_id:
            raise ValidationError(
                {"zone": "La zona debe pertenecer al mismo invernadero que el sensor."}
            )

    def get_unit(self) -> str:
        return self.unit or self.sensor_type.default_unit