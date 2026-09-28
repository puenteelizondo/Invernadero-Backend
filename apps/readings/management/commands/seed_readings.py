import random
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.readings.models import Reading
from apps.sensors.models import Sensor


class Command(BaseCommand):
    """
    Etapa 10: genera lecturas sintéticas, espaciadas hacia atrás en el
    tiempo, para un sensor existente. No es código de producción —es
    una herramienta desechable para poder medir con EXPLAIN ANALYZE
    cómo se comporta la tabla Reading a un volumen realista, en vez de
    adivinar índices sin evidencia.
    """

    help = (
        "Genera lecturas sintéticas para un sensor existente, útil para "
        "medir el plan de ejecución de Postgres a escala (Etapa 10)."
    )

    def add_arguments(self, parser):
        parser.add_argument("sensor_id", type=int)
        parser.add_argument(
            "--count", type=int, default=200_000,
            help="Cuántas lecturas generar (default: 200,000).",
        )
        parser.add_argument(
            "--interval-seconds", type=int, default=10,
            help="Separación entre lecturas sintéticas consecutivas, "
                 "contando hacia atrás desde ahora (default: 10s).",
        )
        parser.add_argument(
            "--batch-size", type=int, default=5000,
            help="Filas por cada bulk_create (default: 5000).",
        )

    def handle(self, *args, **options):
        sensor_id = options["sensor_id"]
        count = options["count"]
        interval = options["interval_seconds"]
        batch_size = options["batch_size"]

        try:
            sensor = Sensor.objects.select_related("sensor_type").get(pk=sensor_id)
        except Sensor.DoesNotExist:
            raise CommandError(f"No existe un sensor con id={sensor_id}.")

        valid_min = sensor.sensor_type.valid_min
        valid_max = sensor.sensor_type.valid_max
        low = valid_min if valid_min is not None else 0.0
        high = valid_max if valid_max is not None else 100.0

        now = timezone.now()
        self.stdout.write(
            f"Generando {count:,} lecturas sintéticas para "
            f"'{sensor.name}' (id={sensor.id})..."
        )

        batch = []
        created = 0
        for i in range(count):
            ts = now - timedelta(seconds=interval * i)
            value = round(random.uniform(low, high), 2)
            batch.append(Reading(sensor=sensor, timestamp=ts, value=value))
            if len(batch) >= batch_size:
                Reading.objects.bulk_create(batch, ignore_conflicts=True)
                created += len(batch)
                batch = []
                self.stdout.write(f"  ...{created:,} insertadas", ending="\r")

        if batch:
            Reading.objects.bulk_create(batch, ignore_conflicts=True)
            created += len(batch)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nListo: {created:,} lecturas generadas "
                f"(o ignoradas por timestamp duplicado)."
            )
        )