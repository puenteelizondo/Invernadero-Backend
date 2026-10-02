import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.alerts.engine import check_no_signal
from apps.common.realtime import publish_events


class Command(BaseCommand):
    help = (
        "Monitor de alertas de «sin señal»: cada N segundos revisa qué sensores llevan demasiado "
        "tiempo sin mandar lecturas y abre las alertas correspondientes. Debe estar corriendo "
        "(en Docker lo hace el servicio `alerts-monitor`); si no, esas alertas no se generan."
    )

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=15.0, help="Segundos entre revisiones (por defecto 15).")
        parser.add_argument("--once", action="store_true", help="Revisa una sola vez y termina (para pruebas o cron).")

    def handle(self, *args, **opts):
        while True:
            try:
                events = check_no_signal()
                publish_events(events)
                if events:
                    self.stdout.write(f"{len(events)} alerta(s) de sin señal abiertas")
            except Exception as exc:  # un fallo puntual (BD o Redis reiniciándose) no debe tumbar el monitor
                self.stderr.write(f"Error en la revisión: {exc!r}")
            if opts["once"]:
                return
            close_old_connections()  # evita conexiones a la BD caducadas en un proceso de larga vida
            time.sleep(opts["interval"])
