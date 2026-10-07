from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.greenhouses.models import Greenhouse

from .models import Invitation, Membership

User = get_user_model()
PASS = "Clave-segura-123"


@override_settings(FRONTEND_URL="https://inv.example.com", EMAIL_ASYNC=False)
class InvitacionesTests(APITestCase):
    def setUp(self):
        self.gh = Greenhouse.objects.create(name="UdeC")
        self.otro = Greenhouse.objects.create(name="Ajeno")
        self.owner = User.objects.create_user("duena", email="duena@example.com", password=PASS)
        self.oper = User.objects.create_user("oper", email="oper@example.com", password=PASS)
        self.ana = User.objects.create_user("ana", email="ana@example.com", password=PASS)
        self.staff = User.objects.create_user("admin", password=PASS, is_staff=True)
        Membership.objects.create(user=self.owner, greenhouse=self.gh, role="owner")
        Membership.objects.create(user=self.oper, greenhouse=self.gh, role="operator")

    def invitar(self, who="duena", invite="ana", role="operator", gh=None):
        self.client.force_authenticate(User.objects.get(username=who))
        return self.client.post("/api/v1/invitations/", {"greenhouse": (gh or self.gh).pk, "invite": invite, "role": role}, format="json")

    def test_invitar_no_da_acceso_hasta_aceptar(self):
        r = self.invitar(invite="ANA@example.com")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertFalse(Membership.objects.filter(user=self.ana, greenhouse=self.gh).exists())
        # le llega correo con enlace
        self.assertEqual(mail.outbox[-1].to, ["ana@example.com"])
        self.assertIn("UdeC", mail.outbox[-1].subject)
        self.assertIn("https://inv.example.com/greenhouses", mail.outbox[-1].body)
        # todavía no ve el invernadero
        self.client.force_authenticate(self.ana)
        names = [g["name"] for g in self.client.get("/api/v1/greenhouses/").json()["results"]]
        self.assertNotIn("UdeC", names)
        # ve su invitación pendiente
        lst = self.client.get("/api/v1/invitations/").json()["results"]
        self.assertEqual([(i["greenhouse_name"], i["role"], i["invited_by_username"]) for i in lst], [("UdeC", "operator", "duena")])
        # acepta
        r = self.client.post(f"/api/v1/invitations/{lst[0]['id']}/accept/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Membership.objects.get(user=self.ana, greenhouse=self.gh).role, "operator")
        self.assertEqual(self.client.get("/api/v1/invitations/").json()["results"], [])
        self.assertIn("aceptó", mail.outbox[-1].subject)
        self.assertEqual(mail.outbox[-1].to, ["duena@example.com"])
        # responder dos veces no se puede
        self.assertEqual(self.client.post(f"/api/v1/invitations/{lst[0]['id']}/decline/").status_code, 400)

    def test_rechazar_no_crea_membresia(self):
        inv = self.invitar().json()
        self.client.force_authenticate(self.ana)
        self.assertEqual(self.client.post(f"/api/v1/invitations/{inv['id']}/decline/").status_code, 200)
        self.assertFalse(Membership.objects.filter(user=self.ana, greenhouse=self.gh).exists())
        self.assertIn("rechazó", mail.outbox[-1].subject)
        # se puede volver a invitar
        self.assertEqual(self.invitar().status_code, 201)

    def test_solo_el_invitado_responde(self):
        inv = self.invitar().json()
        for who in (self.owner, self.oper, self.staff):
            self.client.force_authenticate(who)
            r = self.client.post(f"/api/v1/invitations/{inv['id']}/accept/")
            self.assertIn(r.status_code, (403, 404), who.username)
        self.assertFalse(Membership.objects.filter(user=self.ana).exists())

    def test_solo_owner_o_staff_invita(self):
        self.assertEqual(self.invitar(who="oper").status_code, 403)
        self.assertEqual(self.invitar(who="duena", gh=self.otro).status_code, 403)
        self.assertEqual(self.invitar(who="admin", gh=self.otro).status_code, 201)

    def test_validaciones(self):
        self.assertEqual(self.invitar(invite="nadie").status_code, 400)
        self.assertIn("ya es miembro", str(self.invitar(invite="oper").json()))
        self.assertIn("ti mismo", str(self.invitar(invite="duena").json()))
        self.assertEqual(self.invitar().status_code, 201)
        self.assertIn("pendiente", str(self.invitar().json()))

    def test_cancelar(self):
        inv = self.invitar().json()
        self.client.force_authenticate(self.owner)
        sent = self.client.get(f"/api/v1/invitations/?box=sent&greenhouse={self.gh.pk}").json()["results"]
        self.assertEqual([i["username"] for i in sent], ["ana"])
        self.assertEqual(self.client.post(f"/api/v1/invitations/{inv['id']}/cancel/").status_code, 200)
        self.client.force_authenticate(self.ana)
        self.assertEqual(self.client.get("/api/v1/invitations/").json()["results"], [])
        self.assertEqual(self.client.post(f"/api/v1/invitations/{inv['id']}/accept/").status_code, 400)

    def test_operator_no_ve_invitaciones_enviadas(self):
        self.invitar()
        self.client.force_authenticate(self.oper)
        self.assertEqual(self.client.get("/api/v1/invitations/?box=sent").json()["results"], [])

    def test_owner_ya_no_agrega_directo_pero_staff_si(self):
        self.client.force_authenticate(self.owner)
        r = self.client.post("/api/v1/memberships/", {"invite": "ana", "greenhouse": self.gh.pk, "role": "viewer"}, format="json")
        self.assertEqual(r.status_code, 403)
        self.client.force_authenticate(self.staff)
        r = self.client.post("/api/v1/memberships/", {"invite": "ana", "greenhouse": self.gh.pk, "role": "viewer"}, format="json")
        self.assertEqual(r.status_code, 201)


@override_settings(EMAIL_ASYNC=False)
class MiembrosTests(APITestCase):
    def setUp(self):
        self.gh = Greenhouse.objects.create(name="UdeC")
        self.otro = Greenhouse.objects.create(name="Ajeno")
        self.owner = User.objects.create_user("duena", email="duena@example.com", password=PASS)
        self.ana = User.objects.create_user("ana", email="ana@example.com", password=PASS)
        self.m_owner = Membership.objects.create(user=self.owner, greenhouse=self.gh, role="owner")
        self.m_ana = Membership.objects.create(user=self.ana, greenhouse=self.gh, role="viewer")
        Membership.objects.create(user=self.owner, greenhouse=self.otro, role="viewer")
        self.client.force_authenticate(self.owner)

    def test_filtra_por_invernadero(self):
        r = self.client.get(f"/api/v1/memberships/?greenhouse={self.gh.pk}").json()["results"]
        self.assertEqual(sorted(m["username"] for m in r), ["ana", "duena"])

    def test_no_se_puede_mover_una_membresia_a_otro_invernadero(self):
        r = self.client.patch(f"/api/v1/memberships/{self.m_owner.pk}/", {"greenhouse": self.otro.pk}, format="json")
        self.assertEqual(r.status_code, 400)
        self.m_owner.refresh_from_db()
        self.assertEqual(self.m_owner.greenhouse, self.gh)

    def test_cambiar_rol_avisa_por_correo(self):
        r = self.client.patch(f"/api/v1/memberships/{self.m_ana.pk}/", {"role": "operator"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(mail.outbox[-1].to, ["ana@example.com"])
        self.assertIn("Cambió tu rol", mail.outbox[-1].subject)
        self.assertIn("Operador", mail.outbox[-1].body)
        html = mail.outbox[-1].alternatives[0][0]
        self.assertIn("cid:invernadero-header", html)
        self.assertIn("Solo lectura", html)

    def test_quitar_acceso_avisa_por_correo(self):
        self.assertEqual(self.client.delete(f"/api/v1/memberships/{self.m_ana.pk}/").status_code, 204)
        self.assertEqual(mail.outbox[-1].to, ["ana@example.com"])
        self.assertIn("Ya no tienes acceso", mail.outbox[-1].subject)
