from collections import Counter

from django.db.models import ProtectedError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

# Nombres en español para los modelos que pueden bloquear un borrado
# (llaves foráneas con on_delete=PROTECT). Si aparece uno que no está
# aquí, se usa el verbose_name del modelo.
_NOMBRES = {
    "Greenhouse": ("invernadero", "invernaderos"),
    "Zone": ("zona", "zonas"),
    "Device": ("dispositivo", "dispositivos"),
    "Sensor": ("sensor", "sensores"),
    "Actuator": ("actuador", "actuadores"),
    "Reading": ("lectura", "lecturas"),
    "SensorType": ("tipo de sensor", "tipos de sensor"),
    "ActuatorType": ("tipo de actuador", "tipos de actuador"),
    "Membership": ("membresía", "membresías"),
}


def _describir(objetos):
    cuenta = Counter(type(o).__name__ for o in objetos)
    partes = []
    for modelo, n in sorted(cuenta.items()):
        singular, plural = _NOMBRES.get(modelo, (modelo.lower(), modelo.lower() + "s"))
        partes.append(f"{n} {singular if n == 1 else plural}")
    return ", ".join(partes)


def custom_exception_handler(exc, context):
    """
    Igual que el handler por defecto de DRF, pero convierte el
    ProtectedError de Django (intentar borrar algo que todavía tiene
    elementos que dependen de él, p. ej. un invernadero con sensores o
    un sensor con lecturas guardadas) en un 409 con un mensaje claro.
    Sin esto, Django respondía un 500 con la página de depuración.
    """
    if isinstance(exc, ProtectedError):
        detalle = _describir(exc.protected_objects)
        return Response(
            {
                "detail": (
                    "No se puede eliminar porque todavía tiene elementos que dependen de él"
                    + (f" ({detalle})" if detalle else "")
                    + ". Elimínalos o muévelos primero."
                )
            },
            status=status.HTTP_409_CONFLICT,
        )
    return exception_handler(exc, context)
