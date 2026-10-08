from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APITestCase

from apps.actuators.models import Actuator, ActuatorType
from apps.greenhouses.models import Greenhouse
from apps.memberships.models import Membership
from apps.sensors.models import Device, Sensor, SensorType
from apps.users.models import User

from .models import ControlLoop, ControlLoopChange

PASS = "Cl4ve-segura-987"


def build_world():
    """Dos invernaderos, cada uno con su dispositivo, sensor y actuador."""
    w = {}
    stype = SensorType.objects.create(code="temperature", name="Temperatura", default_unit="°C", valid_min=-10, valid_max=60)
    atype = ActuatorType.objects.create(code="heater", name="Calefactor")
    for key in ("a", "b"):
        gh = Greenhouse.objects.create(name=f"inv-{key}")
        dev = Device.objects.create(name=f"esp-{key}", greenhouse=gh)
        raw = dev.set_api_key()
        dev.save()
        sensor = Sensor.objects.create(name=f"t-{key}", sensor_type=stype, greenhouse=gh, device=dev)
        act = Actuator.objects.create(name=f"cal-{key}", actuator_type=atype, greenhouse=gh, device=dev)
        w[key] = dict(gh=gh, dev=dev, raw=raw, sensor=sensor, act=act)
    return w


def make_users(w):
    u = {}
    u["staff"] = User.objects.create_user("staff", password=PASS, is_staff=True)
    for name, role in (("owner", "owner"), ("operator", "operator"), ("viewer", "viewer")):
        u[name] = User.objects.create_user(name, password=PASS)
        Membership.objects.create(user=u[name], greenhouse=w["a"]["gh"], role=role)
    u["outsider"] = User.objects.create_user("outsider", password=PASS)
    Membership.objects.create(user=u["outsider"], greenhouse=w["b"]["gh"], role="owner")
    return u


def body(w, **extra):
    d = dict(
        greenhouse=w["a"]["gh"].pk, name="Temperatura nave",
        sensor=w["a"]["sensor"].pk, actuator=w["a"]["act"].pk,
        mode="pid", direction="direct", setpoint=25, kp=2, ki=0.1, kd=0.5,
    )
    d.update(extra)
    return d


class ControlLoopApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.w = build_world()
        self.u = make_users(self.w)

    def _create(self, user="owner", **extra):
        self.client.force_authenticate(self.u[user])
        return self.client.post("/api/v1/control-loops/", body(self.w, **extra), format="json")

    # ---- borrar lo que usa un lazo ----
    def test_borrar_sensor_actuador_o_dispositivo_de_un_lazo_da_409_y_el_lazo_sigue(self):
        self.assertEqual(self._create("staff").status_code, 201)
        self.client.force_authenticate(self.u["staff"])
        a = self.w["a"]
        for url in (
            f"/api/v1/sensors/{a['sensor'].pk}/",
            f"/api/v1/actuators/{a['act'].pk}/",
            f"/api/v1/devices/{a['dev'].pk}/",
        ):
            r = self.client.delete(url)
            self.assertEqual(r.status_code, 409, url)
            self.assertIn("lazo de control", r.json()["detail"])
        r = self.client.post(f"/api/v1/sensors/{a['sensor'].pk}/purge/", {"confirm_name": a["sensor"].name}, format="json")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(ControlLoop.objects.count(), 1)

    def test_encender_a_mano_un_actuador_con_lazo_activo_da_409(self):
        self.assertEqual(self._create("owner", enabled=True).status_code, 201)   # PID activo
        self.client.force_authenticate(self.u["owner"])
        url = f"/api/v1/actuators/{self.w['a']['act'].pk}/state/"
        r = self.client.post(url, {"state": True}, format="json")
        self.assertEqual(r.status_code, 409)
        self.assertIn("Temperatura nave", r.json()["detail"])
        ControlLoop.objects.update(mode="off")                       # lazo apagado: vuelve el manual
        self.assertEqual(self.client.post(url, {"state": True}, format="json").status_code, 200)

    # ---- permisos ----
    def test_owner_y_operator_crean_viewer_no(self):
        self.assertEqual(self._create("owner").status_code, 201)
        ControlLoop.objects.all().delete()
        self.assertEqual(self._create("operator").status_code, 201)
        ControlLoop.objects.all().delete()
        self.assertEqual(self._create("viewer").status_code, 403)
        self.assertEqual(self._create("outsider").status_code, 403)

    def test_staff_puede_todo(self):
        self.assertEqual(self._create("staff").status_code, 201)

    def test_anonimo(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get("/api/v1/control-loops/").status_code, (401, 403))

    def test_viewer_lee_pero_no_edita(self):
        loop_id = self._create("owner").data["id"]
        self.client.force_authenticate(self.u["viewer"])
        self.assertEqual(self.client.get(f"/api/v1/control-loops/{loop_id}/").status_code, 200)
        r = self.client.patch(f"/api/v1/control-loops/{loop_id}/", {"setpoint": 30}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.delete(f"/api/v1/control-loops/{loop_id}/").status_code, 403)

    def test_aislamiento_entre_invernaderos(self):
        loop_id = self._create("owner").data["id"]
        self.client.force_authenticate(self.u["outsider"])
        self.assertEqual(self.client.get(f"/api/v1/control-loops/{loop_id}/").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/control-loops/").data["count"], 0)

    # ---- versión e historial ----
    def test_version_sube_solo_si_hay_cambio(self):
        loop_id = self._create().data["id"]
        url = f"/api/v1/control-loops/{loop_id}/"
        self.client.force_authenticate(self.u["operator"])
        r = self.client.patch(url, {"setpoint": 27.5}, format="json")
        self.assertEqual((r.status_code, r.data["version"]), (200, 2))
        r = self.client.patch(url, {"setpoint": 27.5}, format="json")
        self.assertEqual(r.data["version"], 2)            # sin cambios: no sube
        r = self.client.patch(url, {"kp": 3.0, "mode": "pi"}, format="json")
        self.assertEqual(r.data["version"], 3)
        hist = self.client.get(url + "history/").data
        self.assertEqual([h["version"] for h in hist], [3, 2, 1])
        self.assertEqual(hist[1]["changes"]["setpoint"], {"before": 25.0, "after": 27.5})
        self.assertEqual(hist[1]["changed_by_name"], "operator")
        self.assertEqual(ControlLoopChange.objects.filter(loop_id=loop_id).count(), 3)

    def test_my_role_en_invernadero(self):
        for user, role in (("owner", "owner"), ("operator", "operator"), ("viewer", "viewer"), ("staff", "owner")):
            self.client.force_authenticate(self.u[user])
            r = self.client.get(f"/api/v1/greenhouses/{self.w['a']['gh'].pk}/")
            self.assertEqual(r.data["my_role"], role, user)

    # ---- validaciones ----
    def test_validaciones(self):
        for extra in (
            {"setpoint": 999},           # fuera del rango válido del sensor
            {"setpoint": -50},
            {"kp": -1},
            {"output_min": 80, "output_max": 20},
            {"output_max": 150},
            {"sample_time_ms": 10},
            {"sample_time_ms": 999999},
            {"hysteresis": -1},
        ):
            r = self._create(**extra)
            self.assertEqual(r.status_code, 400, extra)

    def test_sensor_de_otro_invernadero(self):
        r = self._create(sensor=self.w["b"]["sensor"].pk)
        self.assertEqual(r.status_code, 400)

    def test_sensor_y_actuador_de_dispositivos_distintos(self):
        other = Device.objects.create(name="otro", greenhouse=self.w["a"]["gh"])
        other.set_api_key(); other.save()
        self.w["a"]["act"].device = other
        self.w["a"]["act"].save()
        r = self._create()
        self.assertEqual(r.status_code, 400)
        self.assertIn("dispositivos distintos", str(r.data))

    def test_sin_dispositivo(self):
        Sensor.objects.filter(pk=self.w["a"]["sensor"].pk).update(device=None)
        Actuator.objects.filter(pk=self.w["a"]["act"].pk).update(device=None)
        self.assertEqual(self._create().status_code, 400)

    def test_un_lazo_por_actuador(self):
        self.assertEqual(self._create().status_code, 201)
        self.assertEqual(self._create(name="otro").status_code, 400)

    def test_dispositivo_se_deduce(self):
        r = self._create()
        self.assertEqual(r.data["device"], self.w["a"]["dev"].pk)

    def test_no_mueve_de_invernadero(self):
        loop_id = self._create().data["id"]
        self.client.force_authenticate(self.u["owner"])
        r = self.client.patch(
            f"/api/v1/control-loops/{loop_id}/", {"greenhouse": self.w["b"]["gh"].pk}, format="json"
        )
        self.assertEqual(r.status_code, 400)

    def test_el_cliente_no_puede_falsear_ack(self):
        loop_id = self._create().data["id"]
        self.client.force_authenticate(self.u["owner"])
        r = self.client.patch(
            f"/api/v1/control-loops/{loop_id}/", {"applied_version": 99, "version": 99}, format="json"
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.data["version"], r.data["applied_version"]), (1, 0))

    def test_delete(self):
        loop_id = self._create().data["id"]
        self.client.force_authenticate(self.u["operator"])
        self.assertEqual(self.client.delete(f"/api/v1/control-loops/{loop_id}/").status_code, 204)
        self.assertFalse(ControlLoop.objects.exists())

    # ---- endpoints del dispositivo ----
    def test_control_config_solo_sus_lazos(self):
        self._create()
        self.client.force_authenticate(None)
        r = self.client.get("/api/v1/devices/control-config/", HTTP_X_DEVICE_KEY=self.w["a"]["raw"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["loops"]), 1)
        self.assertEqual(r.data["loops"][0]["version"], 1)
        r = self.client.get("/api/v1/devices/control-config/", HTTP_X_DEVICE_KEY=self.w["b"]["raw"])
        self.assertEqual(r.data["loops"], [])

    def test_control_config_exige_clave(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get("/api/v1/devices/control-config/").status_code, (401, 403))
        r = self.client.get("/api/v1/devices/control-config/", HTTP_X_DEVICE_KEY="x" * 40)
        self.assertIn(r.status_code, (401, 403))

    def test_ws_token_con_clave_de_dispositivo(self):
        self.client.force_authenticate(None)
        r = self.client.post("/api/v1/devices/ws-token/", HTTP_X_DEVICE_KEY=self.w["a"]["raw"])
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["token"])
        self.client.force_authenticate(self.u["owner"])
        self.assertIn(self.client.post("/api/v1/devices/ws-token/").status_code, (401, 403))


class DeviceWebSocketTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.w = build_world()
        self.u = make_users(self.w)
        self.loop = ControlLoop.objects.create(
            greenhouse=self.w["a"]["gh"], name="T", sensor=self.w["a"]["sensor"],
            actuator=self.w["a"]["act"], device=self.w["a"]["dev"], mode="pid", setpoint=25,
        )

    def _comm(self, token):
        from config.asgi import application

        return WebsocketCommunicator(application, f"/ws/device/?token={token}")

    def _token(self, key="a"):
        from apps.control.tokens import issue_device_ws_token

        return issue_device_ws_token(self.w[key]["dev"])

    async def _connect(self, key="a"):
        token = await database_sync_to_async(self._token)(key)
        comm = self._comm(token)
        ok, _ = await comm.connect()
        return comm, ok

    async def test_rechaza_sin_token_y_token_reusado(self):
        comm = self._comm("nada")
        ok, code = await comm.connect()
        self.assertFalse(ok)
        token = await database_sync_to_async(self._token)()
        c1 = self._comm(token)
        self.assertTrue((await c1.connect())[0])
        c2 = self._comm(token)
        self.assertFalse((await c2.connect())[0])      # de un solo uso
        await c1.disconnect()

    async def test_recibe_config_y_solo_sus_lazos(self):
        comm, ok = await self._connect("a")
        self.assertTrue(ok)
        msg = await comm.receive_json_from()
        self.assertEqual(msg["event"], "config")
        self.assertEqual([l["id"] for l in msg["loops"]], [self.loop.pk])
        self.assertEqual(msg["loops"][0]["setpoint"], 25)
        await comm.disconnect()
        comm_b, ok = await self._connect("b")
        msg = await comm_b.receive_json_from()
        self.assertEqual(msg["loops"], [])
        await comm_b.disconnect()

    async def test_config_update_llega_al_cambiar_por_api(self):
        comm, _ = await self._connect("a")
        await comm.receive_json_from()   # config
        await comm.receive_json_from()   # actuators

        def patch():
            from rest_framework.test import APIClient

            c = APIClient()
            c.force_authenticate(User.objects.get(username="operator"))
            return c.patch(f"/api/v1/control-loops/{self.loop.pk}/", {"setpoint": 28}, format="json")

        r = await database_sync_to_async(patch)()
        self.assertEqual(r.status_code, 200)
        msg = await comm.receive_json_from()
        self.assertEqual(msg["event"], "config_update")
        self.assertEqual((msg["loop"]["setpoint"], msg["loop"]["version"]), (28, 2))
        await comm.disconnect()

    async def test_ack_guarda_applied_version(self):
        comm, _ = await self._connect("a")
        await comm.receive_json_from()
        await comm.send_json_to({"event": "ack", "loop_id": self.loop.pk, "version": 1})
        await comm.send_json_to({"event": "ping_inexistente"})
        await comm.disconnect()
        await self._wait_for(lambda: ControlLoop.objects.get(pk=self.loop.pk).applied_version == 1)
        loop = await database_sync_to_async(ControlLoop.objects.get)(pk=self.loop.pk)
        self.assertEqual(loop.applied_version, 1)
        self.assertIsNotNone(loop.applied_at)

    async def test_ack_de_version_inexistente_o_lazo_ajeno_se_ignora(self):
        other = await database_sync_to_async(ControlLoop.objects.create)(
            greenhouse=self.w["b"]["gh"], name="B", sensor=self.w["b"]["sensor"],
            actuator=self.w["b"]["act"], device=self.w["b"]["dev"], setpoint=20,
        )
        comm, _ = await self._connect("a")
        await comm.receive_json_from()
        await comm.send_json_to({"event": "ack", "loop_id": other.pk, "version": 1})   # lazo ajeno
        await comm.send_json_to({"event": "ack", "loop_id": self.loop.pk, "version": 99})  # versión futura
        await comm.send_json_to({"event": "ack", "loop_id": "x", "version": "y"})
        await comm.disconnect()
        a = await database_sync_to_async(ControlLoop.objects.get)(pk=other.pk)
        b = await database_sync_to_async(ControlLoop.objects.get)(pk=self.loop.pk)
        self.assertEqual((a.applied_version, b.applied_version), (0, 0))

    async def test_telemetria_se_retransmite_sin_guardar_reading(self):
        from apps.readings.models import Reading

        layer = get_channel_layer()
        await layer.group_add("greenhouse_%d" % self.w["a"]["gh"].pk, "probe")
        comm, _ = await self._connect("a")
        await comm.receive_json_from()
        await comm.send_json_to({
            "event": "telemetry", "loop_id": self.loop.pk, "pv": 24.8, "setpoint": 25,
            "output": 42.5, "error": 0.2, "p": 1, "i": 2, "d": 3, "mode": "pid",
        })
        got = None
        for _ in range(20):
            m = await layer.receive("probe")
            if m.get("event") == "control_telemetry":
                got = m
                break
        self.assertIsNotNone(got)
        self.assertEqual(got["payload"]["pv"], 24.8)
        self.assertEqual(got["payload"]["output"], 42.5)
        self.assertEqual(await database_sync_to_async(Reading.objects.count)(), 0)
        last = cache.get(f"ctrl:tm:{self.loop.pk}")
        self.assertEqual(last["pv"], 24.8)
        # salida > 1 % => el actuador pasa a ON con origen "automation"
        act = await database_sync_to_async(Actuator.objects.get)(pk=self.w["a"]["act"].pk)
        self.assertTrue(act.state)
        hist = await database_sync_to_async(lambda: act.state_history.first().source)()
        self.assertEqual(hist, "automation")
        await comm.disconnect()

    async def test_telemetria_invalida_o_de_lazo_ajeno_se_ignora(self):
        layer = get_channel_layer()
        await layer.group_add("greenhouse_%d" % self.w["a"]["gh"].pk, "probe2")
        comm, _ = await self._connect("a")
        await comm.receive_json_from()
        await comm.send_json_to({"event": "telemetry", "loop_id": 9999, "pv": 1})
        await comm.send_json_to({"event": "telemetry", "loop_id": self.loop.pk, "pv": "NaN", "output": "abc"})
        await comm.send_json_to("no es objeto")
        await comm.disconnect()
        self.assertIsNone(cache.get("ctrl:tm:9999"))
        last = cache.get(f"ctrl:tm:{self.loop.pk}")
        self.assertIsNone(last["pv"])
        self.assertIsNone(last["output"])

    async def test_presencia_en_linea_y_desconexion(self):
        from apps.control import services

        comm, _ = await self._connect("a")
        await comm.receive_json_from()
        self.assertTrue(services.is_online(self.w["a"]["dev"].pk))
        await comm.disconnect()
        self.assertFalse(services.is_online(self.w["a"]["dev"].pk))

    async def test_reconexion_no_marca_caido(self):
        from apps.control import services

        c1, _ = await self._connect("a")
        await c1.receive_json_from()
        c2, _ = await self._connect("a")
        await c2.receive_json_from()
        await c1.disconnect()       # cae la vieja, la nueva sigue viva
        self.assertTrue(services.is_online(self.w["a"]["dev"].pk))
        await c2.disconnect()
        self.assertFalse(services.is_online(self.w["a"]["dev"].pk))

    async def _wait_for(self, cond, tries=40):
        import asyncio

        for _ in range(tries):
            if await database_sync_to_async(cond)():
                return
            await asyncio.sleep(0.05)


@override_settings(EMAIL_ASYNC=False)
class DeviceRealtimeTests(TransactionTestCase):
    """Lecturas que el ESP32 manda por su WebSocket, y órdenes a sus actuadores por el mismo canal."""

    def setUp(self):
        cache.clear()
        self.w = build_world()
        self.u = make_users(self.w)
        a = self.w["a"]
        self.manual = Actuator.objects.create(
            name="ventilador", actuator_type=a["act"].actuator_type, greenhouse=a["gh"], device=a["dev"], state=True,
        )
        Sensor.objects.filter(pk=a["sensor"].pk).update(persist_interval_seconds=60)

    async def _connect(self, key="a"):
        from apps.control.tokens import issue_device_ws_token
        from config.asgi import application

        token = await database_sync_to_async(issue_device_ws_token)(self.w[key]["dev"])
        comm = WebsocketCommunicator(application, f"/ws/device/?token={token}")
        ok, _ = await comm.connect()
        self.assertTrue(ok)
        self.assertEqual((await comm.receive_json_from())["event"], "config")
        return comm

    async def _probe(self, name):
        layer = get_channel_layer()
        await layer.group_add("greenhouse_%d" % self.w["a"]["gh"].pk, name)
        return layer

    async def _events(self, layer, name, kind, wait=0.5):
        import asyncio

        got = []
        while True:
            try:
                m = await asyncio.wait_for(layer.receive(name), wait)
            except asyncio.TimeoutError:
                return got
            if m.get("event") == kind:
                got.append(m["payload"])

    # ---- actuadores ----------------------------------------------------------
    async def test_al_conectar_recibe_el_estado_de_sus_actuadores(self):
        comm = await self._connect("a")
        msg = await comm.receive_json_from()
        self.assertEqual(msg["event"], "actuators")
        self.assertEqual(msg["actuators"], [
            {"id": self.w["a"]["act"].pk, "state": False}, {"id": self.manual.pk, "state": True},
        ])
        await comm.disconnect()
        comm_b = await self._connect("b")
        msg = await comm_b.receive_json_from()
        self.assertEqual([x["id"] for x in msg["actuators"]], [self.w["b"]["act"].pk])   # nada del otro
        await comm_b.disconnect()

    async def test_cambio_desde_la_pagina_llega_al_instante_solo_a_su_esp32(self):
        import time

        comm_a = await self._connect("a")
        await comm_a.receive_json_from()
        comm_b = await self._connect("b")
        await comm_b.receive_json_from()

        def apagar():
            from rest_framework.test import APIClient

            c = APIClient()
            c.force_authenticate(User.objects.get(username="operator"))
            return c.post(f"/api/v1/actuators/{self.manual.pk}/state/", {"state": False}, format="json")

        t0 = time.monotonic()
        r = await database_sync_to_async(apagar)()
        self.assertEqual(r.status_code, 200, r.content)
        msg = await comm_a.receive_json_from(timeout=2)
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual(msg, {"event": "actuator_state", "actuator_id": self.manual.pk, "state": False})
        self.assertTrue(await comm_b.receive_nothing(timeout=0.3))
        await comm_a.disconnect()
        await comm_b.disconnect()

    # ---- lecturas ------------------------------------------------------------
    async def test_lecturas_llegan_a_la_pagina_y_se_guardan_segun_la_regla(self):
        from apps.readings.models import Reading

        layer = await self._probe("p_ok")
        comm = await self._connect("a")
        await comm.receive_json_from()
        sid = self.w["a"]["sensor"].pk
        await comm.send_json_to({"event": "readings", "readings": [{"sensor_id": sid, "value": 24.5}]})
        await comm.send_json_to({"event": "readings", "readings": [{"sensor_id": sid, "value": 24.6}]})
        got = await self._events(layer, "p_ok", "sensor_reading")
        self.assertEqual([g["value"] for g in got], [24.5, 24.6])
        self.assertEqual([g["persisted"] for g in got], [True, False])     # "guardar cada 60 s"
        self.assertEqual(await database_sync_to_async(Reading.objects.count)(), 1)
        self.assertTrue(await comm.receive_nothing(timeout=0.2))           # todo bien: no hay aviso
        await comm.disconnect()

    async def test_sensor_ajeno_o_fuera_de_rango_se_rechaza_y_avisa(self):
        from apps.readings.models import Reading

        layer = await self._probe("p_bad")
        comm = await self._connect("a")
        await comm.receive_json_from()
        await comm.send_json_to({"event": "readings", "readings": [
            {"sensor_id": self.w["b"]["sensor"].pk, "value": 20},     # de otro dispositivo
            {"sensor_id": self.w["a"]["sensor"].pk, "value": 999},    # fuera del rango válido
            {"sensor_id": "x", "value": "y"},
        ]})
        msg = await comm.receive_json_from(timeout=2)
        self.assertEqual(msg["event"], "readings_result")
        self.assertEqual(len(msg["rejected"]), 3)
        self.assertEqual(await self._events(layer, "p_bad", "sensor_reading", wait=0.3), [])
        self.assertEqual(await database_sync_to_async(Reading.objects.count)(), 0)
        await comm.send_json_to({"event": "readings", "readings": "no es lista"})
        await comm.disconnect()

    async def test_lectura_por_ws_dispara_alerta_y_correo(self):
        from django.core import mail

        from apps.alerts.models import Alert, AlertRule

        def preparar():
            owner = self.u["owner"]
            owner.email = "duena@example.com"
            owner.save()
            AlertRule.objects.create(sensor=self.w["a"]["sensor"], greenhouse=self.w["a"]["gh"], max_value=30)

        await database_sync_to_async(preparar)()
        comm = await self._connect("a")
        await comm.receive_json_from()
        await comm.send_json_to({"event": "readings", "readings": [{"sensor_id": self.w["a"]["sensor"].pk, "value": 41}]})
        await self._wait(lambda: Alert.objects.count() == 1)
        self.assertEqual(await database_sync_to_async(Alert.objects.count)(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["duena@example.com"])
        await comm.disconnect()

    async def test_limite_de_lecturas_por_conexion(self):
        layer = await self._probe("p_lim")
        comm = await self._connect("a")
        await comm.receive_json_from()
        sid = self.w["a"]["sensor"].pk
        lote = [{"sensor_id": sid, "value": 20 + i / 10} for i in range(20)]
        await comm.send_json_to({"event": "readings", "readings": lote})       # 20 de 30 fichas
        await comm.send_json_to({"event": "readings", "readings": lote})       # ya no alcanza
        msg = await comm.receive_json_from(timeout=2)
        self.assertEqual((msg["event"], msg["error"]), ("readings_result", "rate_limited"))
        got = await self._events(layer, "p_lim", "sensor_reading")
        self.assertEqual(len(got), 20)
        await comm.disconnect()

    async def _wait(self, cond, tries=40):
        import asyncio

        for _ in range(tries):
            if await database_sync_to_async(cond)():
                return
            await asyncio.sleep(0.05)


class OriginEnProduccionTests(TransactionTestCase):
    """Con DEBUG=False el chequeo de Origin aplica a los navegadores, NO al ESP32.

    Un ESP32 no manda el header Origin; si el validador se aplicara también a
    /ws/device/, el hardware quedaría fuera en producción.
    """

    def setUp(self):
        cache.clear()
        self.w = build_world()

    async def test_dispositivo_sin_origin_conecta_y_navegador_sin_origin_no(self):
        import importlib

        from django.test import override_settings

        import config.asgi as asgi_mod
        from apps.control.tokens import issue_device_ws_token

        token = await database_sync_to_async(issue_device_ws_token)(self.w["a"]["dev"])
        try:
            with override_settings(DEBUG=False, ALLOWED_HOSTS=["invernadero.example.com"]):
                app = importlib.reload(asgi_mod).application
                dev = WebsocketCommunicator(app, f"/ws/device/?token={token}")
                self.assertTrue((await dev.connect())[0])
                await dev.disconnect()
                nav = WebsocketCommunicator(app, "/ws/greenhouses/1/?token=x")
                self.assertFalse((await nav.connect())[0])
        finally:
            importlib.reload(asgi_mod)
