"""
Evaluación de reglas de alerta durante la ingesta de lecturas.

Se llama una vez por lectura aceptada, con las reglas del sensor ya
cargadas (una sola consulta por petición, ver `load_rules`). Solo toca la
base de datos cuando hay un CAMBIO de estado (empieza a incumplirse,
se abre, se resuelve) o aparece un valor más extremo que el pico anterior,
así que una lectura normal sin reglas incumplidas no cuesta ninguna
escritura.
"""
import threading
from collections import defaultdict

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Max
from django.core.mail import send_mail
from django.utils import timezone

from apps.memberships.models import Membership

from .models import Alert, AlertRule


def load_rules(sensor_ids):
    """Reglas activas de esos sensores, agrupadas por sensor_id (1 consulta)."""
    rules = defaultdict(list)
    qs = AlertRule.objects.filter(sensor_id__in=sensor_ids, is_active=True).select_related("active_alert")
    for rule in qs:
        rules[rule.sensor_id].append(rule)
    return rules


def _unit(sensor):
    return sensor.get_unit()


def _payload(alert, sensor, value):
    return {
        "alert_id": alert.id,
        "rule_id": alert.rule_id,
        "sensor_id": sensor.id,
        "sensor_name": sensor.name,
        "unit": _unit(sensor),
        "kind": alert.kind,
        "severity": alert.severity,
        "threshold": alert.threshold,
        "value": value,
        "opened_at": alert.opened_at.isoformat(),
    }


def evaluate(rules, sensor, value, timestamp):
    """
    Evalúa una lectura contra las reglas de su sensor. Devuelve una lista de
    eventos (greenhouse_id, nombre, payload) para publicar por WebSocket.
    """
    events = []
    for rule in rules:
        if rule.rule_type == AlertRule.RuleType.NO_SIGNAL:
            # Llegó una lectura: el sensor está vivo, se cierra la alerta de "sin señal" si la había.
            events += _on_normal(rule, sensor, value)
            continue
        kind = threshold = None
        if rule.max_value is not None and value > rule.max_value:
            kind, threshold = Alert.Kind.HIGH, rule.max_value
        elif rule.min_value is not None and value < rule.min_value:
            kind, threshold = Alert.Kind.LOW, rule.min_value

        if kind is None:
            events += _on_normal(rule, sensor, value)
        else:
            events += _on_breach(rule, sensor, value, timestamp, kind, threshold)
    return events


def _on_breach(rule, sensor, value, timestamp, kind, threshold):
    alert = rule.active_alert
    if alert is not None:
        # Ya hay una alerta abierta: solo se actualiza el pico si este valor es más extremo.
        worse = value > alert.peak_value if alert.kind == Alert.Kind.HIGH else value < alert.peak_value
        if worse:
            alert.peak_value = value
            Alert.objects.filter(pk=alert.pk).update(peak_value=value)
        return []

    if rule.breach_since is None:
        rule.breach_since = timestamp
        AlertRule.objects.filter(pk=rule.pk).update(breach_since=timestamp)
    if (timestamp - rule.breach_since).total_seconds() < rule.duration_seconds:
        return []  # todavía no se sostuvo el tiempo suficiente

    alert = Alert.objects.create(
        rule=rule, sensor=sensor, greenhouse_id=sensor.greenhouse_id, kind=kind,
        severity=rule.severity, threshold=threshold, trigger_value=value, peak_value=value,
        opened_at=rule.breach_since,
    )
    rule.active_alert = alert
    AlertRule.objects.filter(pk=rule.pk).update(active_alert=alert)
    if rule.notify_email:
        _email_owners(alert, rule, sensor, value)
    return [(sensor.greenhouse_id, "alert_opened", _payload(alert, sensor, value))]


def _on_normal(rule, sensor, value):
    events = []
    if rule.breach_since is not None:
        rule.breach_since = None
        AlertRule.objects.filter(pk=rule.pk).update(breach_since=None)
    alert = rule.active_alert
    if alert is not None:
        now = timezone.now()
        Alert.objects.filter(pk=alert.pk).update(status=Alert.Status.RESOLVED, resolved_at=now)
        AlertRule.objects.filter(pk=rule.pk).update(active_alert=None)
        rule.active_alert = None
        alert.status, alert.resolved_at = Alert.Status.RESOLVED, now
        payload = _payload(alert, sensor, value)
        payload["resolved_at"] = now.isoformat()
        events.append((sensor.greenhouse_id, "alert_resolved", payload))
        maybe_cleanup()
    return events


def resolve_active(rule):
    """Cierra la alerta abierta de una regla (al desactivarla o borrarla). Devuelve el evento o None."""
    alert = rule.active_alert
    if alert is None:
        return None
    now = timezone.now()
    Alert.objects.filter(pk=alert.pk).update(status=Alert.Status.RESOLVED, resolved_at=now)
    AlertRule.objects.filter(pk=rule.pk).update(active_alert=None, breach_since=None)
    rule.active_alert, rule.breach_since = None, None
    payload = _payload(alert, alert.sensor, alert.peak_value)
    payload["resolved_at"] = now.isoformat()
    return (alert.greenhouse_id, "alert_resolved", payload)


def _email_owners(alert, rule, sensor, value):
    recipients = list(
        Membership.objects.filter(greenhouse_id=sensor.greenhouse_id, role=Membership.Role.OWNER)
        .exclude(user__email="").values_list("user__email", flat=True)
    )
    if not recipients:
        return
    unit = f" {_unit(sensor)}".rstrip()
    if alert.kind == Alert.Kind.STALE:
        subject = f"[{sensor.greenhouse.name}] Sin señal: {sensor.name} no manda datos hace {fmt_seconds(value)}"
        detail = f"Sin lecturas desde hace {fmt_seconds(value)} (tolerancia: {fmt_seconds(alert.threshold)})"
    else:
        what = "por encima del máximo" if alert.kind == Alert.Kind.HIGH else "por debajo del mínimo"
        subject = f"[{sensor.greenhouse.name}] Alerta: {sensor.name} {what} ({value:g}{unit})"
        detail = f"Valor: {value:g}{unit} ({what}; límite {alert.threshold:g}{unit})"
    body = (
        f"Invernadero: {sensor.greenhouse.name}\n"
        f"Sensor: {sensor.name}\n"
        f"Regla: {rule}\n"
        f"Severidad: {alert.get_severity_display()}\n"
        f"{detail}\n"
        f"Inicio: {alert.opened_at:%Y-%m-%d %H:%M:%S} UTC\n"
    )

    def _send():
        try:
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=True)
        except Exception:  # un correo caído nunca debe afectar la ingesta
            pass

    # En un hilo aparte: el SMTP puede tardar segundos y la ingesta no debe esperar.
    threading.Thread(target=_send, daemon=True).start()


def purge_old_resolved(days=None):
    """Borra las alertas resueltas hace más de `days` días (por defecto ALERT_RETENTION_DAYS). Devuelve cuántas."""
    days = settings.ALERT_RETENTION_DAYS if days is None else days
    if not days:
        return 0
    cutoff = timezone.now() - timezone.timedelta(days=days)
    deleted, _ = Alert.objects.filter(status=Alert.Status.RESOLVED, resolved_at__lt=cutoff).delete()
    return deleted


def maybe_cleanup():
    """
    Limpieza automática del historial, como mucho UNA vez al día. No hay un
    programador de tareas en el proyecto, así que se aprovecha el momento en que
    se resuelve una alerta; `cache.add` asegura que solo un proceso la ejecute.
    Se desactiva con ALERT_RETENTION_DAYS=0.
    """
    if not settings.ALERT_RETENTION_DAYS:
        return
    if cache.add("alerts:cleanup", 1, 24 * 3600):
        purge_old_resolved()


def fmt_seconds(sec) -> str:
    """90 -> '1 min 30 s'."""
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return " ".join(p for p in (h and f"{h} h", m and f"{m} min", s and f"{s} s") if p) or "0 s"


def last_signal(sensor):
    """
    Cuándo llegó por última vez una lectura de este sensor. Se mira la caché
    del "último valor recibido" (cubre también las lecturas que no se guardan
    en la base) y, si Redis la perdió, la última lectura guardada. Si nunca hubo
    ninguna, se cuenta desde que se creó el sensor.
    """
    from apps.readings.models import Reading
    from apps.readings.persistence import get_latest

    candidates = []
    latest = get_latest(sensor.id)
    if latest and latest.get("timestamp"):
        candidates.append(latest["timestamp"])
    if not candidates:
        last = Reading.objects.filter(sensor_id=sensor.id).aggregate(m=Max("timestamp"))["m"]
        if last:
            candidates.append(last)
    return max(candidates) if candidates else sensor.created_at


def check_no_signal(now=None):
    """
    Revisa todas las reglas "sin señal" activas y abre/actualiza alertas.
    Lo ejecuta el monitor (`manage.py monitor_alerts`) cada pocos segundos: es
    el único lugar que detecta silencio, porque la ingesta solo se entera de
    lo que SÍ llega. Devuelve los eventos a publicar por WebSocket.
    """
    now = now or timezone.now()
    events = []
    rules = AlertRule.objects.filter(
        rule_type=AlertRule.RuleType.NO_SIGNAL, is_active=True, sensor__is_active=True
    ).select_related("sensor", "sensor__greenhouse", "sensor__sensor_type", "active_alert")
    for rule in rules:
        sensor = rule.sensor
        silence = (now - last_signal(sensor)).total_seconds()
        if silence <= rule.duration_seconds:
            continue  # todavía dentro de lo tolerado (si había alerta, la cierra la próxima lectura)
        if rule.active_alert_id:
            if silence > rule.active_alert.peak_value:
                Alert.objects.filter(pk=rule.active_alert_id).update(peak_value=silence)
            continue
        with transaction.atomic():  # por si hubiera dos monitores a la vez: solo uno abre la alerta
            fresh = AlertRule.objects.select_for_update().get(pk=rule.pk)
            if fresh.active_alert_id:
                continue
            alert = Alert.objects.create(
                rule=rule, sensor=sensor, greenhouse_id=sensor.greenhouse_id, kind=Alert.Kind.STALE,
                severity=rule.severity, threshold=rule.duration_seconds, trigger_value=silence,
                peak_value=silence, opened_at=now - timezone.timedelta(seconds=silence - rule.duration_seconds),
            )
            AlertRule.objects.filter(pk=rule.pk).update(active_alert=alert)
        if rule.notify_email:
            _email_owners(alert, rule, sensor, silence)
        payload = _payload(alert, sensor, silence)
        events.append((sensor.greenhouse_id, "alert_opened", payload))
    return events
