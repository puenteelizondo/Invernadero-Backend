from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.greenhouses.urls")),
    path("api/v1/", include("apps.sensors.urls")),
    path("api/v1/", include("apps.actuators.urls")),
    path("api/v1/", include("apps.readings.urls")),
    # Login/logout de sesión: solo para navegar la API en el navegador
    # durante desarrollo. No se usa desde Postman ni desde el frontend.
    path("api-auth/", include("rest_framework.urls")),
]