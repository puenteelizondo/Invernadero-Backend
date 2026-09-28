from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import Membership


def _resolve_greenhouse(obj):
    """
    Dado un objeto de cualquiera de las apps relacionadas con un
    invernadero, encuentra su invernadero. Cubre los distintos
    "caminos" que existen en el modelo de datos: el objeto ES un
    Greenhouse, tiene un FK directo .greenhouse (Zone, Sensor,
    Actuator, Device), o hay que subir un nivel más (Reading, cuyo
    invernadero se llega vía .sensor.greenhouse).
    """
    from apps.greenhouses.models import Greenhouse

    if isinstance(obj, Greenhouse):
        return obj
    if hasattr(obj, "greenhouse_id"):
        return obj.greenhouse
    if hasattr(obj, "sensor_id"):
        return obj.sensor.greenhouse
    return None


def role_of(user, greenhouse):
    """
    El rol del usuario en ese invernadero, o None si no tiene
    membresía ahí. is_staff se trata como Owner de cualquier
    invernadero (soporte técnico), sin necesitar una fila de
    Membership para cada uno.
    """
    if greenhouse is None:
        return None
    if user.is_staff:
        return Membership.Role.OWNER
    membership = Membership.objects.filter(user=user, greenhouse=greenhouse).first()
    return membership.role if membership else None


class IsGreenhouseMember(BasePermission):
    """
    Permiso por defecto para recursos "estructurales" de un
    invernadero (Greenhouse, Zone, Device, Sensor, Actuator): leer
    (GET/HEAD/OPTIONS) requiere CUALQUIER rol; escribir
    (POST/PUT/PATCH/DELETE) requiere ser Owner.

    has_permission() solo exige estar autenticado — la restricción
    real de "no ver invernaderos ajenos" la hace el queryset filtrado
    de cada ViewSet (ver GreenhouseScopedMixin); has_object_permission
    es la segunda capa, que además decide lectura vs. escritura.
    """

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        role = role_of(request.user, _resolve_greenhouse(obj))
        if role is None:
            return False
        if request.method in SAFE_METHODS:
            return True
        return role == Membership.Role.OWNER


class IsGreenhouseOperatorOrAbove(BasePermission):
    """
    Para controlar actuadores (el action 'state'): Owner u Operator.
    Viewer puede ver el actuador pero no cambiar su estado.
    """

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        role = role_of(request.user, _resolve_greenhouse(obj))
        return role in (Membership.Role.OWNER, Membership.Role.OPERATOR)