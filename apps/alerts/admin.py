from django.contrib import admin

from .models import Alert, AlertRule


@admin.register(AlertRule)
class AlertRuleAdmin(admin.ModelAdmin):
    list_display = ("id", "sensor", "min_value", "max_value", "duration_seconds", "severity", "is_active")
    list_filter = ("severity", "is_active", "greenhouse")


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("id", "sensor", "kind", "severity", "status", "peak_value", "opened_at", "resolved_at")
    list_filter = ("status", "kind", "severity", "greenhouse")
