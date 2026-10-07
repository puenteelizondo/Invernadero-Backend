"""
Avisos por correo de invitaciones y cambios de acceso a un invernadero.

Solo se manda si el destinatario tiene email. Un fallo de SMTP nunca rompe
la petición: queda en el log (`docker compose logs web`). Por defecto se
manda en un hilo aparte para no hacer esperar al usuario (EMAIL_ASYNC=False
lo hace síncrono, útil en pruebas). El diseño está en apps/common/emails.py.
"""
import logging
import threading

from django.conf import settings
from django.utils.html import format_html

from apps.common.emails import send_email
from apps.common.frontend import frontend_base

logger = logging.getLogger(__name__)

ROLE_NAME = {"owner": "Propietario", "operator": "Operador", "viewer": "Solo lectura"}
ROLE_TEXT = {
    "owner": "puede todo, incluso invitar y quitar miembros",
    "operator": "puede manejar actuadores y lazos de control",
    "viewer": "puede ver todo, pero no cambiar nada",
}


def _role(role):
    return f"{ROLE_NAME.get(role, role)} ({ROLE_TEXT.get(role, '')})"


def _who(user):
    if user is None:
        return "Alguien"
    full = f"{user.first_name} {user.last_name}".strip()
    return f"{full} ({user.username})" if full else user.username


def _send(to_user, subject, **content):
    email = getattr(to_user, "email", "")
    if not email:
        return

    def run():
        try:
            send_email([email], subject, **content)
        except Exception:
            logger.exception("No se pudo mandar el aviso '%s' a %s", subject, email)

    if getattr(settings, "EMAIL_ASYNC", True):
        threading.Thread(target=run, daemon=True).start()
    else:
        run()


def _url(path, request=None):
    base = frontend_base(request)
    return f"{base}{path}" if base else None


def invitation_created(inv, request=None):
    url = _url("/greenhouses", request)
    _send(
        inv.user,
        f"Te invitaron al invernadero {inv.greenhouse.name}",
        title=f"Te invitaron a «{inv.greenhouse.name}»",
        eyebrow="Invitación",
        preheader=f"{_who(inv.invited_by)} te invitó como {ROLE_NAME.get(inv.role, inv.role).lower()}.",
        paragraphs=[
            format_html("Hola <strong>{}</strong>:", inv.user.username),
            format_html(
                "<strong>{}</strong> te invitó a colaborar en el invernadero <strong>{}</strong>.",
                _who(inv.invited_by), inv.greenhouse.name,
            ),
            "Entra con tu cuenta: la invitación aparece hasta arriba de la página, con los botones "
            "Aceptar y Rechazar.",
        ],
        details=[("Invernadero", inv.greenhouse.name), ("Tu rol", _role(inv.role)),
                 ("Invitado por", _who(inv.invited_by))],
        button=("Ver la invitación", url) if url else None,
        note="Mientras no la aceptes no tienes acceso. Si no esperabas esta invitación, puedes rechazarla o ignorarla.",
        footer="Recibes este correo porque alguien te invitó a un invernadero del sistema Invernadero.",
    )


def invitation_answered(inv, request=None):
    if inv.invited_by is None or inv.invited_by_id == inv.user_id:
        return
    accepted = inv.status == "accepted"
    verb = "aceptó" if accepted else "rechazó"
    url = _url(f"/greenhouses/{inv.greenhouse_id}/members", request) if accepted else None
    _send(
        inv.invited_by,
        f"{inv.user.username} {verb} la invitación a {inv.greenhouse.name}",
        title=f"{inv.user.username} {verb} tu invitación",
        eyebrow="Invitación aceptada" if accepted else "Invitación rechazada",
        tone="brand" if accepted else "info",
        preheader=f"Respuesta a tu invitación a {inv.greenhouse.name}.",
        paragraphs=[
            format_html("Hola <strong>{}</strong>:", inv.invited_by.username),
            format_html(
                "<strong>{}</strong> {} tu invitación al invernadero <strong>{}</strong>.",
                _who(inv.user), verb, inv.greenhouse.name,
            ),
            "Ya aparece en la lista de miembros con su rol." if accepted
            else "No tiene acceso. Si fue un error, puedes volver a invitarle desde Miembros.",
        ],
        details=[("Invernadero", inv.greenhouse.name), ("Rol", _role(inv.role))],
        button=("Ver miembros", url) if url else None,
        footer="Recibes este correo porque mandaste esta invitación.",
    )


def role_changed(membership, old_role, by, request=None):
    if by is not None and by.pk == membership.user_id:
        return
    gh = membership.greenhouse
    url = _url(f"/greenhouses/{gh.pk}", request)
    _send(
        membership.user,
        f"Cambió tu rol en el invernadero {gh.name}",
        title="Cambió tu rol",
        eyebrow="Acceso actualizado",
        tone="info",
        preheader=f"Ahora eres {ROLE_NAME.get(membership.role, membership.role).lower()} en {gh.name}.",
        paragraphs=[
            format_html("Hola <strong>{}</strong>:", membership.user.username),
            format_html("<strong>{}</strong> cambió tu rol en el invernadero <strong>{}</strong>.", _who(by), gh.name),
        ],
        details=[("Antes", _role(old_role)), ("Ahora", _role(membership.role))],
        button=("Abrir el invernadero", url) if url else None,
        footer="Recibes este correo porque eres miembro de este invernadero.",
    )


def access_removed(user, greenhouse, by):
    if by is not None and by.pk == user.pk:
        return
    _send(
        user,
        f"Ya no tienes acceso al invernadero {greenhouse.name}",
        title=f"Ya no tienes acceso a «{greenhouse.name}»",
        eyebrow="Acceso retirado",
        tone="warning",
        preheader=f"Te quitaron el acceso a {greenhouse.name}.",
        paragraphs=[
            format_html("Hola <strong>{}</strong>:", user.username),
            format_html(
                "<strong>{}</strong> te quitó el acceso al invernadero <strong>{}</strong>. "
                "Ya no aparecerá en tu lista.",
                _who(by), greenhouse.name,
            ),
        ],
        note="Si crees que es un error, habla con el propietario del invernadero.",
        footer="Recibes este correo porque eras miembro de este invernadero.",
    )
