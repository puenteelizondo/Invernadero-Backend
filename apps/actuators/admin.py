from django.contrib import admin

from .models import Actuator, ActuatorStateHistory, ActuatorType


@admin.register(ActuatorType)
class ActuatorTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code")
    prepopulated_fields = {"code": ("name",)}


class ActuatorStateHistoryInline(admin.TabularInline):
    model = ActuatorStateHistory
    extra = 0
    readonly_fields = ("state", "changed_by", "source", "changed_at")
    can_delete = False
    max_num = 0  # el historial se crea solo vía set_state(), no a mano
    ordering = ["-changed_at"]


@admin.register(Actuator)
class ActuatorAdmin(admin.ModelAdmin):
    list_display = ("name", "actuator_type", "greenhouse", "zone", "state", "is_active")
    list_filter = ("greenhouse", "actuator_type", "is_active", "state")
    search_fields = ("name",)
    inlines = [ActuatorStateHistoryInline]

    def save_model(self, request, obj, form, change):
        if change:
            # Si el estado cambió desde el formulario del admin, usamos
            # set_state() para que quede registrado en el historial,
            # en vez de dejar que el admin haga un save() plano.
            previous = Actuator.objects.get(pk=obj.pk)
            if previous.state != obj.state:
                obj.state = previous.state  # revertimos el save automático
                super().save_model(request, obj, form, change)
                obj.set_state(form.cleaned_data["state"], user=request.user)
                return
        super().save_model(request, obj, form, change)