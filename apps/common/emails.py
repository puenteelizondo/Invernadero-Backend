"""
Correos con diseño (HTML + texto plano) para todo el sistema.

Todos los correos (recuperación de contraseña, alertas, invitaciones,
cambios de acceso) pasan por `send_email()`, así comparten el mismo
diseño: ilustración del invernadero arriba, título, texto, un recuadro
de datos opcional y un botón.

- El HTML usa tablas y estilos en línea: es lo único que Gmail, Outlook y
  los clientes del celular respetan de forma fiable.
- La ilustración va INCRUSTADA en el correo (Content-ID), no como enlace,
  así se ve aunque cambie la dirección del servidor o del túnel.
- Siempre se manda también la versión en texto plano (clientes sin HTML,
  vista previa de notificaciones, filtros de spam).
"""
from email.mime.image import MIMEImage
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import conditional_escape, format_html, format_html_join
from django.utils.safestring import mark_safe

HEADER_CID = "invernadero-header"
_ASSETS = Path(__file__).resolve().parent / "email_assets"

BRAND = "#1F5E3B"
TEXT = "#1E2A21"
MUTED = "#5F6E60"
CANVAS = "#EEF3EC"

# Color de la etiqueta de arriba según el tipo de correo.
TONES = {
    "brand": ("#1F5E3B", "#DCEFE0"),
    "danger": ("#B42318", "#FEE4E2"),
    "warning": ("#B54708", "#FEF0C7"),
    "info": ("#175CD3", "#D1E9FF"),
}

FONT = "'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


@lru_cache(maxsize=1)
def _header_png() -> bytes:
    return (_ASSETS / "header.png").read_bytes()


def _paragraph(p) -> str:
    return format_html(
        '<p style="margin:0 0 14px;font:16px/1.6 {};color:{};">{}</p>', mark_safe(FONT), TEXT, p
    )


def _details(rows) -> str:
    if not rows:
        return ""
    body = format_html_join(
        "",
        '<tr><td style="padding:9px 14px;font:13px/1.4 {f};color:{m};white-space:nowrap;vertical-align:top;'
        'border-top:1px solid #E3EBE1;">{k}</td>'
        '<td style="padding:9px 14px;font:600 14px/1.4 {f};color:{t};border-top:1px solid #E3EBE1;">{v}</td></tr>',
        ({"f": mark_safe(FONT), "m": MUTED, "t": TEXT, "k": k, "v": v} for k, v in rows),
    )
    return format_html(
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="margin:6px 0 20px;border-collapse:separate;background:#F6F9F5;border:1px solid #E3EBE1;'
        'border-radius:12px;overflow:hidden;">{}</table>',
        body,
    )


def _button(button) -> str:
    if not button:
        return ""
    label, url = button
    return format_html(
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 22px;"><tr>'
        '<td bgcolor="{b}" style="border-radius:12px;">'
        '<a href="{u}" target="_blank" style="display:inline-block;padding:14px 26px;font:600 16px/1 {f};'
        'color:#FFFFFF;text-decoration:none;border-radius:12px;">{l}</a></td></tr></table>'
        '<p style="margin:0 0 18px;font:12px/1.5 {f};color:{m};">Si el botón no funciona, copia este enlace en tu '
        'navegador:<br><a href="{u}" style="color:{b};word-break:break-all;">{u}</a></p>',
        b=BRAND, u=url, f=mark_safe(FONT), l=label, m=MUTED,
    )


def _highlight(h, fg, bg) -> str:
    if not h:
        return ""
    label, value, sub = (list(h) + [None])[:3]
    return format_html(
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:4px 0 18px;">'
        '<tr><td style="padding:16px 18px;background:{bg};border-radius:14px;">'
        '<p style="margin:0 0 4px;font:600 12px/1.3 {f};color:{fg};text-transform:uppercase;letter-spacing:.05em;">{l}</p>'
        '<p style="margin:0;font:800 34px/1.1 {f};color:{fg};">{v}</p>{s}'
        '</td></tr></table>',
        bg=bg, fg=fg, f=mark_safe(FONT), l=label, v=value,
        s=format_html('<p style="margin:6px 0 0;font:13px/1.4 {};color:{};">{}</p>', mark_safe(FONT), fg, sub) if sub else "",
    )


def render_html(*, title, paragraphs, tone="brand", eyebrow=None, details=None, button=None, note=None,
                preheader="", footer=None, highlight=None) -> str:
    """`highlight` = (etiqueta, valor[, texto chico]): un dato grande y de color, p. ej. la lectura de una alerta."""
    fg, bg = TONES.get(tone, TONES["brand"])
    eyebrow_html = (
        format_html(
            '<span style="display:inline-block;margin:0 0 12px;padding:5px 12px;border-radius:999px;'
            'background:{};color:{};font:700 12px/1.2 {};letter-spacing:.04em;text-transform:uppercase;">{}</span>',
            bg, fg, mark_safe(FONT), eyebrow,
        )
        if eyebrow
        else ""
    )
    note_html = (
        format_html(
            '<p style="margin:4px 0 0;padding:12px 14px;border-left:3px solid {};background:#F6F9F5;'
            'font:13px/1.55 {};color:{};">{}</p>',
            fg, mark_safe(FONT), MUTED, note,
        )
        if note
        else ""
    )
    footer = footer or "Recibes este correo porque tienes una cuenta en el sistema Invernadero."
    return format_html(
        """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light"><title>{title}</title>
<style>@media only screen and (max-width:480px){{.px{{padding-left:20px!important;padding-right:20px!important}}
h1{{font-size:21px!important}}}}</style></head>
<body style="margin:0;padding:0;background:{canvas};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{canvas};">
<tr><td align="center" style="padding:24px 12px;">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0"
         style="width:100%;max-width:600px;background:#FFFFFF;border-radius:18px;overflow:hidden;
                border:1px solid #DCE6DA;box-shadow:0 6px 24px rgba(31,94,59,.08);">
    <tr><td style="padding:0;line-height:0;">
      <img src="cid:{cid}" width="600" alt="Ilustración de un invernadero"
           style="display:block;width:100%;max-width:600px;height:auto;border:0;">
    </td></tr>
    <tr><td class="px" style="padding:14px 32px 0;">
      <table role="presentation" cellpadding="0" cellspacing="0"><tr>
        <td style="width:26px;height:26px;background:{brand};border-radius:8px;text-align:center;
                   font:700 14px/26px {font};color:#D7EBDA;">&#127793;</td>
        <td style="padding-left:9px;font:700 15px/1 {font};color:{brand};letter-spacing:-.01em;">Invernadero</td>
      </tr></table>
    </td></tr>
    <tr><td class="px" style="padding:22px 32px 8px;">
      {eyebrow}
      <h1 style="margin:0 0 16px;font:700 24px/1.25 {font};color:{text};letter-spacing:-.01em;">{title}</h1>
      {paragraphs}
      {highlight}
      {details}
      {button}
      {note}
    </td></tr>
    <tr><td class="px" style="padding:18px 32px 26px;border-top:1px solid #E8EEE6;">
      <p style="margin:0;font:12px/1.6 {font};color:{muted};">{footer}</p>
    </td></tr>
  </table>
  <p style="margin:14px 0 0;font:11px/1.5 {font};color:#8A9989;">Invernadero · monitoreo y control</p>
</td></tr></table>
</body></html>""",
        title=title, canvas=CANVAS, preheader=preheader, cid=HEADER_CID, brand=BRAND, font=mark_safe(FONT),
        eyebrow=eyebrow_html, text=TEXT, muted=MUTED, footer=footer,
        paragraphs=mark_safe("".join(_paragraph(p) for p in paragraphs)),
        highlight=mark_safe(_highlight(highlight, fg, bg)),
        details=mark_safe(_details(details)), button=mark_safe(_button(button)), note=note_html,
    )


def _strip(s) -> str:
    """Texto plano de un valor que puede traer HTML seguro (p. ej. <strong>)."""
    import re
    from html import unescape

    return unescape(re.sub(r"<[^>]+>", "", str(conditional_escape(s))))


def render_text(*, title, paragraphs, details=None, button=None, note=None, footer=None, highlight=None, **_) -> str:
    lines = [_strip(title), "=" * min(60, len(_strip(title))), ""]
    lines += [_strip(p) + "\n" for p in paragraphs]
    if highlight:
        lines += [f"{_strip(highlight[0])}: {_strip(highlight[1])}", ""]
    if details:
        lines += [f"{_strip(k)}: {_strip(v)}" for k, v in details] + [""]
    if button:
        lines += [f"{_strip(button[0])}: {button[1]}", ""]
    if note:
        lines += [_strip(note), ""]
    lines += ["--", _strip(footer or "Invernadero · monitoreo y control")]
    return "\n".join(lines)


def build_email(to, subject, **content) -> EmailMultiAlternatives:
    msg = EmailMultiAlternatives(subject, render_text(**content), settings.DEFAULT_FROM_EMAIL, list(to))
    msg.attach_alternative(render_html(**content), "text/html")
    msg.mixed_subtype = "related"   # la imagen va "relacionada" al HTML (se muestra dentro, no como adjunto)
    img = MIMEImage(_header_png(), "png")
    img.add_header("Content-ID", f"<{HEADER_CID}>")
    img.add_header("Content-Disposition", "inline", filename="invernadero.png")
    msg.attach(img)
    return msg


def send_email(to, subject, **content) -> int:
    """Manda el correo con diseño. Lanza la excepción del SMTP si falla (quien llama decide)."""
    to = [t for t in to if t]
    if not to:
        return 0
    return build_email(to, subject, **content).send(fail_silently=False)
