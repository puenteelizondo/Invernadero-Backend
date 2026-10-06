from django.db import transaction
from rest_framework import serializers

from apps.actuators.models import Actuator
from apps.sensors.models import Sensor

from . import services
from .models import ControlLoop, ControlLoopChange


class ControlLoopSerializer(serializers.ModelSerializer):
    sensor_name = serializers.CharField(source="sensor.name", read_only=True)
    actuator_name = serializers.CharField(source="actuator.name", read_only=True)
    unit = serializers.SerializerMethodField()
    valid_min = serializers.FloatField(source="sensor.sensor_type.valid_min", read_only=True)
    valid_max = serializers.FloatField(source="sensor.sensor_type.valid_max", read_only=True)
    updated_by_name = serializers.CharField(source="updated_by.username", read_only=True, default=None)
    device_online = serializers.SerializerMethodField()
    pending = serializers.SerializerMethodField()
    last_telemetry = serializers.SerializerMethodField()

    class Meta:
        model = ControlLoop
        fields = [
            "id", "greenhouse", "name", "sensor", "sensor_name", "actuator", "actuator_name",
            "device", "unit", "valid_min", "valid_max",
            "mode", "direction", "setpoint", "hysteresis", "kp", "ki", "kd",
            "output_min", "output_max", "integral_limit", "sample_time_ms", "enabled",
            "version", "applied_version", "applied_at", "pending",
            "updated_by_name", "updated_at", "created_at",
            "device_online", "last_telemetry",
        ]
        read_only_fields = ["version", "applied_version", "applied_at", "updated_at", "created_at"]
        extra_kwargs = {"device": {"required": False}}

    # ---- campos calculados ----
    def get_unit(self, obj):
        return obj.sensor.get_unit()

    def get_device_online(self, obj):
        return services.is_online(obj.device_id)

    def get_pending(self, obj):
        return obj.applied_version < obj.version

    def get_last_telemetry(self, obj):
        return services.get_last_telemetry(obj.pk)

    # ---- validación ----
    def validate(self, attrs):
        inst = self.instance

        def val(name, default=None):
            if name in attrs:
                return attrs[name]
            if inst is not None:
                return getattr(inst, name, default)
            try:
                return ControlLoop._meta.get_field(name).get_default()   # valor por defecto del modelo
            except Exception:
                return default

        greenhouse = val("greenhouse")
        if inst is not None and "greenhouse" in attrs and attrs["greenhouse"].pk != inst.greenhouse_id:
            raise serializers.ValidationError({"greenhouse": "No se puede mover un lazo a otro invernadero."})

        sensor: Sensor = val("sensor")
        actuator: Actuator = val("actuator")
        errors = {}
        if sensor.greenhouse_id != greenhouse.pk:
            errors["sensor"] = "El sensor debe pertenecer al mismo invernadero."
        if actuator.greenhouse_id != greenhouse.pk:
            errors["actuator"] = "El actuador debe pertenecer al mismo invernadero."
        if errors:
            raise serializers.ValidationError(errors)

        clash = ControlLoop.objects.filter(actuator=actuator)
        if inst is not None:
            clash = clash.exclude(pk=inst.pk)
        if clash.exists():
            raise serializers.ValidationError(
                {"actuator": "Este actuador ya está controlado por otro lazo."}
            )

        # El dispositivo que ejecuta el lazo se deduce del hardware: sensor y
        # actuador tienen que colgar del mismo dispositivo.
        owners = {d for d in (sensor.device_id, actuator.device_id) if d}
        if len(owners) > 1:
            raise serializers.ValidationError(
                "El sensor y el actuador pertenecen a dispositivos distintos. "
                "Un lazo lo ejecuta un solo controlador: asigna ambos al mismo dispositivo."
            )
        explicit = attrs.get("device")
        if explicit is not None and explicit.greenhouse_id != greenhouse.pk:
            raise serializers.ValidationError({"device": "El dispositivo debe pertenecer al mismo invernadero."})
        if owners:
            device_id = next(iter(owners))
            if explicit is not None and explicit.pk != device_id:
                raise serializers.ValidationError(
                    {"device": "No coincide con el dispositivo al que están asignados el sensor y el actuador."}
                )
        elif explicit is not None:
            device_id = explicit.pk
        elif inst is not None:
            device_id = inst.device_id
        else:
            raise serializers.ValidationError(
                "Ni el sensor ni el actuador tienen un dispositivo asignado: asígnales el ESP32 "
                "que ejecutará el lazo, o elige el dispositivo."
            )
        attrs["device_id"] = device_id
        attrs.pop("device", None)

        sp = val("setpoint")
        st = sensor.sensor_type
        if st.valid_min is not None and sp < st.valid_min:
            errors["setpoint"] = f"Debe ser ≥ {st.valid_min:g} (rango válido del sensor)."
        if st.valid_max is not None and sp > st.valid_max:
            errors["setpoint"] = f"Debe ser ≤ {st.valid_max:g} (rango válido del sensor)."
        for f in ("kp", "ki", "kd", "hysteresis", "integral_limit"):
            if val(f) < 0:
                errors[f] = "Debe ser ≥ 0."
        omin, omax = val("output_min"), val("output_max")
        if not (0 <= omin <= 100):
            errors["output_min"] = "Debe estar entre 0 y 100 %."
        if not (0 <= omax <= 100):
            errors["output_max"] = "Debe estar entre 0 y 100 %."
        if "output_min" not in errors and "output_max" not in errors and omin >= omax:
            errors["output_max"] = "Debe ser mayor que la salida mínima."
        if not (100 <= val("sample_time_ms") <= 60000):
            errors["sample_time_ms"] = "Debe estar entre 100 y 60000 ms."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    # ---- escritura con versión + historial ----
    def _user(self):
        request = self.context.get("request")
        return request.user if request is not None and request.user.is_authenticated else None

    @transaction.atomic
    def create(self, validated):
        validated["updated_by"] = self._user()
        loop = ControlLoop.objects.create(**validated)
        ControlLoopChange.objects.create(
            loop=loop, version=loop.version, changed_by=self._user(), changes={"created": True}
        )
        transaction.on_commit(lambda: services.publish_loop_changed(loop))
        return loop

    @transaction.atomic
    def update(self, instance, validated):
        fields = ControlLoop.CONFIG_FIELDS
        before = {f: getattr(instance, f) for f in fields}
        old_device = instance.device_id
        for k, v in validated.items():
            setattr(instance, k, v)
        after = {f: getattr(instance, f) for f in fields}
        diff = {f: {"before": before[f], "after": after[f]} for f in fields if before[f] != after[f]}
        if not diff:
            return instance          # nada cambió: no sube la versión ni se avisa

        instance.version += 1
        instance.updated_by = self._user()
        instance.save()
        ControlLoopChange.objects.create(
            loop=instance, version=instance.version, changed_by=self._user(), changes=diff
        )
        transaction.on_commit(lambda: services.publish_loop_changed(instance, old_device))
        return instance


class ControlLoopChangeSerializer(serializers.ModelSerializer):
    changed_by_name = serializers.CharField(source="changed_by.username", read_only=True, default=None)

    class Meta:
        model = ControlLoopChange
        fields = ["id", "version", "changed_by_name", "changes", "created_at"]
