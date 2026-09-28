from django.contrib import admin

from .models import Membership


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    """
    Mientras no exista el frontend, esta es la forma más rápida de
    dar de alta membresías a mano: /admin/memberships/membership/add/.
    """
    list_display = ["user", "greenhouse", "role", "created_at"]
    list_filter = ["role", "greenhouse"]
    search_fields = ["user__username", "greenhouse__name"]