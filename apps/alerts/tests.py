from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.greenhouses.models import Greenhouse
from apps.memberships.models import Membership
from apps.sensors.models import Device, Sensor, SensorType

from . import engine
from .models import Alert, AlertRule

User = get_user_model()


@override_settings(EMAIL_ASYNC=False, FRONTEND_URL="https://inv.example.com")
class CorreoDeAlertaTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.gh = Greenhouse.objects.create(name="UdeC")
        st, _ = SensorType.objects.get_or_create(
            code="temperature", defaults=dict(name="Temperatura", default_unit="°C", valid_min=-40, valid_max=200)
        )
        self.dev = Device.objects.create(name="esp", greenhouse=self.gh)
        self.key = self.dev.set_api_key()
        self.dev.save()
        self.sensor = Sensor.objects.create(name="Temperatura", sensor_type=st, greenhouse=self.gh, device=self.dev)
        owner = User.objects.create_user("cantero", email="cantero@example.com", password="Clave-segura-123")
        sin_email = User.objects.create_user("otro", password="Clave-segura-123")
        oper = User.objects.create_user("oper", email="oper@example.com", password="Clave-segura-123")
        Membership.objects.create(user=owner, greenhouse=self.gh, role="owner")
        Membership.objects.create(user=sin_email, greenhouse=self.gh, role="owner")
        Membership.objects.create(user=oper, greenhouse=self.gh, role="operator")

    def ingest(self, value):
        return self.client.post(
            "/api/v1/readings/ingest/", {"sensor_id": self.sensor.pk, "value": value},
            format="json", HTTP_X_DEVICE_KEY=self.key,
        )

    def test_lectura_fuera_de_rango_manda_correo_a_los_duenos(self):
        for severity, tone_word in (("critical", "crítica"), ("warning", "aviso")):
            mail.outbox.clear()
            AlertRule.objects.all().delete()
            Alert.objects.all().delete()
            AlertRule.objects.create(sensor=self.sensor, greenhouse=self.gh, max_value=30, severity=severity)
            self.assertEqual(self.ingest(20).status_code, 200)
            r = self.ingest(66.7)
            self.assertEqual(r.status_code, 200, r.content)
            self.assertEqual(len(mail.outbox), 1, severity)
            m = mail.outbox[0]
            self.assertEqual(m.to, ["cantero@example.com"])          # solo dueños con email
            self.assertIn("66.7", m.subject)
            html = m.alternatives[0][0]
            self.assertIn("66.7", html)
            self.assertIn(f"/greenhouses/{self.gh.pk}/alerts", html)
            self.assertIn(tone_word, html.lower())
            # la misma alerta abierta no vuelve a mandar correo
            self.ingest(70)
            self.assertEqual(len(mail.outbox), 1)

    def test_por_debajo_del_minimo(self):
        AlertRule.objects.create(sensor=self.sensor, greenhouse=self.gh, min_value=10, severity="warning")
        self.ingest(4)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("por debajo del mínimo", mail.outbox[0].subject)

    def test_sin_avisar_por_correo_no_manda(self):
        AlertRule.objects.create(sensor=self.sensor, greenhouse=self.gh, max_value=30, notify_email=False)
        self.ingest(50)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Alert.objects.count(), 1)

    def test_sin_senal_manda_correo(self):
        AlertRule.objects.create(sensor=self.sensor, greenhouse=self.gh, rule_type="no_signal", duration_seconds=60, severity="critical")
        engine.check_no_signal(now=timezone.now() + timedelta(minutes=10))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Sin señal", mail.outbox[0].subject)
        self.assertIn("dejó de mandar datos", mail.outbox[0].alternatives[0][0])

    def test_error_de_smtp_no_rompe_la_ingesta(self):
        from unittest import mock

        AlertRule.objects.create(sensor=self.sensor, greenhouse=self.gh, max_value=30)
        with mock.patch("apps.alerts.engine.send_email", side_effect=OSError("smtp caído")), \
                self.assertLogs("apps.alerts.engine", level="ERROR"):
            r = self.ingest(50)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Alert.objects.count(), 1)
