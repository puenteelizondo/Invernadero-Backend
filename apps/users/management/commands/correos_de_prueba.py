"""
Manda un ejemplo de CADA correo del sistema (con datos de muestra) para ver
el diseño en tu bandeja:

    docker compose exec web python manage.py correos_de_prueba tu-correo@gmail.com

No toca la base de datos ni crea invitaciones/alertas reales.
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils.html import format_html

from apps.common.emails import send_email


class Command(BaseCommand):
    help = "Manda un ejemplo de cada correo (recuperación, alerta, invitación, rol, acceso) al email indicado."

    def add_arguments(self, parser):
        parser.add_argument("email")

    def handle(self, email, **_):
        base = settings.FRONTEND_URL or "https://tu-invernadero.ejemplo.com"
        gh = "Invernadero de ejemplo"
        samples = [
            ("[Prueba] Recupera tu contraseña · Invernadero", dict(
                title="Recupera tu contraseña", eyebrow="Seguridad de tu cuenta",
                paragraphs=[format_html("Hola <strong>{}</strong>:", "usuario"),
                            "Alguien (con suerte tú) pidió restablecer la contraseña de tu cuenta del Invernadero. "
                            "Presiona el botón para elegir una nueva."],
                button=("Poner contraseña nueva", f"{base}/reset-password?uid=EJEMPLO&token=EJEMPLO"),
                note="Esto es una prueba: el enlace no funciona.")),
            (f"[Prueba] [{gh}] Alerta: Temperatura por encima del máximo (38.4 °C)", dict(
                title="Temperatura está por encima del máximo", eyebrow="Alerta crítica", tone="danger",
                paragraphs=[f"La lectura de «Temperatura» en {gh} salió del rango permitido."],
                highlight=("Valor medido", "38.4 °C", "Límite máximo: 32 °C"),
                details=[("Invernadero", gh), ("Sensor", "Temperatura"), ("Severidad", "Crítica")],
                button=("Ver alertas", f"{base}/greenhouses"),
                note="Solo se manda un correo cuando empieza la alerta, no por cada lectura.")),
            (f"[Prueba] [{gh}] Sin señal: Humedad no manda datos hace 12 min", dict(
                title="Humedad dejó de mandar datos", eyebrow="Alerta aviso", tone="info",
                paragraphs=["Revisa que el ESP32 tenga corriente y WiFi."],
                highlight=("Sin lecturas desde hace", "12 min"),
                details=[("Tolerancia", "5 min"), ("Sensor", "Humedad")])),
            (f"[Prueba] Te invitaron al invernadero {gh}", dict(
                title=f"Te invitaron a «{gh}»", eyebrow="Invitación",
                paragraphs=[format_html("<strong>{}</strong> te invitó a colaborar en el invernadero <strong>{}</strong>.", "jesus", gh),
                            "La invitación aparece hasta arriba de la página, con los botones Aceptar y Rechazar."],
                details=[("Tu rol", "Operador (puede manejar actuadores y lazos de control)"), ("Invitado por", "jesus")],
                button=("Ver la invitación", f"{base}/greenhouses"))),
            ("[Prueba] Cambió tu rol", dict(
                title="Cambió tu rol", eyebrow="Acceso actualizado", tone="info",
                paragraphs=[format_html("<strong>{}</strong> cambió tu rol en <strong>{}</strong>.", "jesus", gh)],
                details=[("Antes", "Solo lectura"), ("Ahora", "Operador")])),
            ("[Prueba] Ya no tienes acceso", dict(
                title=f"Ya no tienes acceso a «{gh}»", eyebrow="Acceso retirado", tone="warning",
                paragraphs=[format_html("<strong>{}</strong> te quitó el acceso al invernadero <strong>{}</strong>.", "jesus", gh)],
                note="Si crees que es un error, habla con el propietario del invernadero.")),
        ]
        for subject, content in samples:
            send_email([email], subject, **content)
            self.stdout.write(f"  enviado: {subject}")
        self.stdout.write(self.style.SUCCESS(f"Listo: {len(samples)} correos a {email}."))
