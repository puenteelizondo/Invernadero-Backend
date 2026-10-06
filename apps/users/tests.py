from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

User = get_user_model()


class RegistroCerradoTests(APITestCase):
    """El registro público no existe; solo staff crea cuentas."""

    def setUp(self):
        self.admin = User.objects.create_user("admin", "a@x.com", "Adm1n-pass-123", is_staff=True)
        self.normal = User.objects.create_user("normal", "n@x.com", "Norm4l-pass-123")

    def test_registro_publico_ya_no_existe(self):
        r = self.client.post(
            "/api/v1/auth/register/",
            {"username": "intruso", "password": "Intruso-pass-123"},
            format="json",
        )
        self.assertEqual(r.status_code, 404)
        self.assertFalse(User.objects.filter(username="intruso").exists())

    def test_anonimo_no_puede_crear(self):
        r = self.client.post("/api/v1/admin/users/", {"username": "x"}, format="json")
        self.assertIn(r.status_code, (401, 403))

    def test_usuario_normal_no_puede_crear_ni_listar(self):
        self.client.force_authenticate(self.normal)
        self.assertEqual(self.client.get("/api/v1/admin/users/").status_code, 403)
        r = self.client.post("/api/v1/admin/users/", {"username": "x"}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertFalse(User.objects.filter(username="x").exists())

    def test_staff_crea_con_password_propia(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(
            "/api/v1/admin/users/",
            {"username": "nuevo", "email": "nuevo@x.com", "password": "Cl4ve-segura-987"},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIsNone(r.data["temporary_password"])
        self.assertNotIn("password", r.data)
        self.assertTrue(User.objects.get(username="nuevo").check_password("Cl4ve-segura-987"))

    def test_staff_crea_con_password_temporal(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post("/api/v1/admin/users/", {"username": "temp"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        temp = r.data["temporary_password"]
        self.assertTrue(temp and len(temp) >= 12)
        self.assertTrue(User.objects.get(username="temp").check_password(temp))

    def test_password_debil_se_rechaza(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(
            "/api/v1/admin/users/", {"username": "debil", "password": "123"}, format="json"
        )
        self.assertEqual(r.status_code, 400)

    def test_email_duplicado(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(
            "/api/v1/admin/users/", {"username": "dup", "email": "N@x.com"}, format="json"
        )
        self.assertEqual(r.status_code, 400)

    def test_desactivar_y_reactivar(self):
        self.client.force_authenticate(self.admin)
        url = f"/api/v1/admin/users/{self.normal.pk}/"
        self.assertEqual(self.client.patch(url, {"is_active": False}, format="json").status_code, 200)
        self.normal.refresh_from_db()
        self.assertFalse(self.normal.is_active)
        self.assertEqual(self.client.patch(url, {"is_active": True}, format="json").status_code, 200)

    def test_no_puede_desactivarse_a_si_mismo(self):
        self.client.force_authenticate(self.admin)
        url = f"/api/v1/admin/users/{self.admin.pk}/"
        self.assertEqual(self.client.patch(url, {"is_active": False}, format="json").status_code, 400)
        self.assertEqual(self.client.patch(url, {"is_staff": False}, format="json").status_code, 400)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active and self.admin.is_staff)

    def test_no_hay_delete(self):
        self.client.force_authenticate(self.admin)
        r = self.client.delete(f"/api/v1/admin/users/{self.normal.pk}/")
        self.assertEqual(r.status_code, 405)

    def test_reset_password_genera_temporal(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(f"/api/v1/admin/users/{self.normal.pk}/reset-password/", {}, format="json")
        self.assertEqual(r.status_code, 200)
        self.normal.refresh_from_db()
        self.assertTrue(self.normal.check_password(r.data["temporary_password"]))

    def test_buscador(self):
        self.client.force_authenticate(self.admin)
        r = self.client.get("/api/v1/admin/users/?search=norm")
        self.assertEqual([u["username"] for u in r.data["results"]], ["normal"])

    def test_usuario_desactivado_no_inicia_sesion(self):
        self.normal.is_active = False
        self.normal.save()
        self.client.get("/api/v1/auth/csrf/")
        r = self.client.post(
            "/api/v1/auth/login/",
            {"username": "normal", "password": "Norm4l-pass-123"},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
