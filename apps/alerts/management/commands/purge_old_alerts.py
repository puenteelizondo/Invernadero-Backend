from django.core.management.base import BaseCommand

from apps.alerts.engine import purge_old_resolved


class Command(BaseCommand):
    help = "Borra las alertas resueltas con más de N días (por defecto ALERT_RETENTION_DAYS)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None, help="Antigüedad en días; si se omite, ALERT_RETENTION_DAYS.")

    def handle(self, *args, **opts):
        n = purge_old_resolved(opts["days"])
        self.stdout.write(self.style.SUCCESS(f"Alertas borradas: {n}"))
