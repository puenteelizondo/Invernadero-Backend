from rest_framework import serializers


class FixedGreenhouseMixin:
    """
    El invernadero de un objeto (dispositivo, sensor, actuador, zona, tipo
    propio) se elige al crearlo y NO se puede cambiar después.

    Sin esto, alguien con permiso de edición en SU invernadero podía mandar
    `{"greenhouse": <otro id>}` en un PATCH y mover el objeto a un invernadero
    ajeno (donde ni siquiera es miembro), metiendo ahí datos o equipos falsos.
    """

    def validate_greenhouse(self, value):
        instance = getattr(self, "instance", None)
        if instance is not None and getattr(value, "pk", value) != instance.greenhouse_id:
            raise serializers.ValidationError(
                "No se puede mover a otro invernadero. Créalo de nuevo en el invernadero correcto."
            )
        return value
