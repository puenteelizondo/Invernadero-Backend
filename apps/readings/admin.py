from django.contrib import admin

from .models import Reading


@admin.register(Reading)
class ReadingAdmin(admin.ModelAdmin):
    list_display = ("sensor", "timestamp", "value")
    list_filter = ("sensor__greenhouse", "sensor")
    date_hierarchy = "timestamp"