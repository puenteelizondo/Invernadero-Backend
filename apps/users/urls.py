from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views import (
    AdminUserViewSet,
    CsrfCookieView,
    LogoutView,
    MeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    SessionLoginView,
)

router = SimpleRouter()
router.register("admin/users", AdminUserViewSet, basename="admin-users")

# El registro público (auth/register/) se cerró a propósito: las cuentas
# las crea un administrador desde /api/v1/admin/users/.
urlpatterns = [
    path("auth/password-reset/", PasswordResetRequestView.as_view(), name="password-reset"),
    path(
        "auth/password-reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path("auth/csrf/", CsrfCookieView.as_view(), name="csrf"),
    path("auth/login/", SessionLoginView.as_view(), name="login"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/me/", MeView.as_view(), name="me"),
    path("", include(router.urls)),
]
