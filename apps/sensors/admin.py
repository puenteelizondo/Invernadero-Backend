from django.contrib import admin

from .models import Device, Sensor, SensorType


@admin.register(SensorType)
class SensorTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "default_unit", "valid_min", "valid_max")
    prepopulated_fields = {"code": ("name",)}


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "greenhouse", "key_prefix", "is_active", "last_seen_at")
    list_filter = ("greenhouse", "is_active")
    readonly_fields = ("key_prefix", "api_key_hash", "last_seen_at")

    def save_model(self, request, obj, form, change):
        is_new = obj.pk is None
        super().save_model(request, obj, form, change)
        if is_new:
            # Generamos la key DESPUÉS del primer save porque
            # set_api_key() necesita que el objeto ya tenga datos
            # consistentes; luego guardamos de nuevo con la key ya
            # hasheada.
            raw_key = obj.set_api_key()
            obj.save()
            self.message_user(
                request,
                f"API key generada (solo se muestra una vez): {raw_key}",
                level="warning",
            )


@admin.register(Sensor)
class SensorAdmin(admin.ModelAdmin):
    list_display = ("name", "sensor_type", "greenhouse", "zone", "is_active")
    list_filter = ("greenhouse", "sensor_type", "is_active")
    search_fields = ("name",)