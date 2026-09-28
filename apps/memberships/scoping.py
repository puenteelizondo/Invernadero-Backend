from .models import Membership


def visible_greenhouse_ids(user):
    """
    Devuelve la lista de ids de invernadero que este usuario puede
    ver, o None como señal especial de "sin filtro, ve todo" — que
    es el caso de is_staff (el admin, para soporte).

    Se usa en el get_queryset() de cada ViewSet cuyos objetos
    pertenecen a un invernadero, para que un usuario nunca vea (ni
    pueda referenciar por id) recursos de un invernadero ajeno.
    """
    if user.is_staff:
        return None
    return Membership.objects.filter(user=user).values_list("greenhouse_id", flat=True)