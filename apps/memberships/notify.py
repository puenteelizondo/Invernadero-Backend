"""
Avisos por correo de invitaciones y cambios de acceso a un invernadero.

Solo se manda si el destinatario tiene email. Un fallo de SMTP nunca rompe
la petición: queda en el log (`docker compose logs web`). Por defecto se
manda en un hilo aparte para no hacer esperar al usuario (EMAIL_ASYNC=False
lo hace síncrono, útil en pruebas).
"""
import logging
import threading

from django.conf import settings
from django.core.mail import send_mail

from apps.common.frontend import frontend_base

logger = logging.getLogger(__name__)

ROLE_TEXT = {
    "owner": "propietario (puede todo, incluso invitar a otros)",
    "operator": "operador (puede manejar actuadores y lazos)",
    "viewer": "solo lectura (puede ver, no cambiar)",
}


def _who(user):
    if user is None:
        return "Alguien"
    full = f"{user.first_name} {user.last_name}".strip()
    return f"{full} ({user.username})" if full else user.username


def _send(to_user, subject, body):
    email = getattr(to_user, "email", "")
    if not email:
        return

    def run():
        try:
            send_mail(subject, body, None, [email], fail_silently=False)
        except Exception:
            logger.exception("No se pudo mandar el aviso '%s' a %s", subject, email)

    if getattr(settings, "EMAIL_ASYNC", True):
        threading.Thread(target=run, daemon=True).start()
    else:
        run()


def _link(path, request=None):
    base = frontend_base(request)
    return f"\n{base}{path}\n" if base else ""


def invitation_created(inv, request=None):
    link = _link("/greenhouses", request)
    _send(
        inv.user,
        f"Te invitaron al invernadero {inv.greenhouse.name}",
        f"Hola {inv.user.username}:\n\n"
        f"{_who(inv.invited_by)} te invitó al invernadero «{inv.greenhouse.name}» "
        f"como {ROLE_TEXT.get(inv.role, inv.role)}.\n\n"
        "Para aceptar o rechazar la invitación, entra a la página del Invernadero "
        "con tu cuenta: la verás arriba de todo."
        f"{link}\n"
        "Si no esperabas esta invitación, puedes rechazarla o ignorarla: "
        "mientras no la aceptes no tienes acceso.",
        )


def invitation_answered(inv, request=None):
    if inv.invited_by is None or inv.invited_by_id == inv.user_id:
        return
    accepted = inv.status == "accepted"
    link = _link(f"/greenhouses/{inv.greenhouse_id}/members", request) if accepted else ""
    _send(
        inv.invited_by,
        f"{inv.user.username} {'aceptó' if accepted else 'rechazó'} la invitación a {inv.greenhouse.name}",
        f"Hola {inv.invited_by.username}:\n\n"
        f"{_who(inv.user)} {'aceptó' if accepted else 'rechazó'} tu invitación al invernadero "
        f"«{inv.greenhouse.name}» como {ROLE_TEXT.get(inv.role, inv.role)}."
        + (f"\nYa aparece en Miembros:{link}" if accepted else "\n"),
    )


def role_changed(membership, old_role, by, request=None):
    if by is not None and by.pk == membership.user_id:
        return
    _send(
        membership.user,
        f"Cambió tu rol en el invernadero {membership.greenhouse.name}",
        f"Hola {membership.user.username}:\n\n"
        f"{_who(by)} cambió tu rol en «{membership.greenhouse.name}»:\n"
        f"antes: {ROLE_TEXT.get(old_role, old_role)}\n"
        f"ahora: {ROLE_TEXT.get(membership.role, membership.role)}"
        f"{_link(f'/greenhouses/{membership.greenhouse_id}', request)}",
    )


def access_removed(user, greenhouse, by):
    if by is not None and by.pk == user.pk:
        return
    _send(
        user,
        f"Ya no tienes acceso al invernadero {greenhouse.name}",
        f"Hola {user.username}:\n\n"
        f"{_who(by)} te quitó el acceso al invernadero «{greenhouse.name}». "
        "Si crees que es un error, habla con el propietario del invernadero.",
    )
