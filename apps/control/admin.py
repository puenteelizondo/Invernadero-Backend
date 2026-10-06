from django.contrib import admin

from .models import ControlLoop, ControlLoopChange


@admin.register(ControlLoop)
class ControlLoopAdmin(admin.ModelAdmin):
    list_display = ("name", "greenhouse", "mode", "setpoint", "enabled", "version", "applied_version")
    list_filter = ("greenhouse", "mode", "enabled")


@admin.register(ControlLoopChange)
class ControlLoopChangeAdmin(admin.ModelAdmin):
    list_display = ("loop", "version", "changed_by", "created_at")
