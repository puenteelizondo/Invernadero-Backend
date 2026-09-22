from django.contrib import admin

from .models import Greenhouse, Zone


class ZoneInline(admin.TabularInline):
    model = Zone
    extra = 1


@admin.register(Greenhouse)
class GreenhouseAdmin(admin.ModelAdmin):
    list_display = ("name", "timezone", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name",)
    inlines = [ZoneInline]


@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):
    list_display = ("name", "greenhouse")
    list_filter = ("greenhouse",)