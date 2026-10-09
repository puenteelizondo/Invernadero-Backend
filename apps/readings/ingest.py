"""
Núcleo de la ingesta de lecturas, compartido por:

- POST /api/v1/readings/ingest/ (HTTP, `ReadingIngestView`), y
- el evento "readings" del WebSocket del dispositivo (`apps/control/consumers.py`).

Así las dos vías validan, disparan alertas, deciden qué se guarda en el
historial ("guardar cada N") y avisan a la página EXACTAMENTE igual.
"""
import time

from django.core.cache import cache

from apps.alerts import engine as alert_engine
from apps.common.realtime import publish_events
from apps.sensors.models import Sensor

from .models import Reading
from .persistence import load_persisted, mark_latest_many, mark_persisted_many, should_persist
from .serializers import ReadingIngestItemSerializer


# Límite de lecturas POR DISPOSITIVO, el mismo para HTTP y WebSocket (un solo
# contador en la caché): cambiar de vía o abrir más conexiones no lo multiplica.
# 30 lecturas cada 3 s = 10 por segundo en promedio, de sobra para un ESP32.
READINGS_WINDOW = 3
READINGS_PER_WINDOW = 30


def within_device_limit(device_id, n) -> bool:
    key = f"ctrl:rl:{device_id}:{int(time.time() // READINGS_WINDOW)}"
    cache.add(key, 0, READINGS_WINDOW * 2)
    try:
        used = cache.incr(key, n)
    except ValueError:            # la clave expiró justo entre add e incr
        cache.set(key, n, READINGS_WINDOW * 2)
        used = n
    return used <= READINGS_PER_WINDOW


def ingest_readings(device, raw_items):
    """
    Procesa una lista de lecturas `{"sensor_id", "value", "timestamp"?}` del
    dispositivo `device`. Devuelve `{"accepted", "persisted", "rejected", "results"}`.
    """
    # Una sola consulta para todos los sensores del lote (antes, una por lectura).
    wanted = {
        item["sensor_id"] for item in raw_items
        if isinstance(item, dict) and isinstance(item.get("sensor_id"), int)
        and 0 < item["sensor_id"] < 2**63      # un id gigante no debe llegar a la base de datos
    }
    sensors = {
        sn.id: sn
        for sn in Sensor.objects.select_related("sensor_type", "greenhouse").filter(pk__in=wanted)
    }

    rules = alert_engine.load_rules(sensors.keys())  # reglas de alerta del lote (1 consulta)

    results = []
    events = []
    to_create = []
    to_mark_persisted = []
    latest = {}
    # Lo último guardado de todos los sensores del lote, en un solo viaje a Redis.
    persisted_last = load_persisted(
        [sid for sid, sn in sensors.items() if sn.persist_interval_seconds]
    )

    for index, item in enumerate(raw_items):
        serializer = ReadingIngestItemSerializer(
            data=item, context={"device": device, "sensors": sensors}
        )
        if serializer.is_valid():
            data = serializer.validated_data
            sensor = data["sensor"]
            value = data["value"]
            timestamp = data["timestamp"]

            persisted = should_persist(sensor, value, timestamp, last=persisted_last.get(sensor.id))
            if persisted:
                persisted_last[sensor.id] = {"value": value, "timestamp": timestamp}
                to_create.append(
                    Reading(sensor=sensor, value=value, timestamp=timestamp)
                )
                to_mark_persisted.append((sensor, value, timestamp))

            latest[sensor.id] = (value, timestamp)
            sensor_rules = rules.get(sensor.id)
            if sensor_rules:
                events.extend(alert_engine.evaluate(sensor_rules, sensor, value, timestamp))

            results.append(
                {"index": index, "status": "accepted", "persisted": persisted}
            )

            events.append((
                sensor.greenhouse_id,
                "sensor_reading",
                {
                    "sensor_id": sensor.id,
                    "sensor_name": sensor.name,
                    "sensor_type": sensor.sensor_type.code,
                    "unit": sensor.get_unit(),
                    "value": value,
                    "timestamp": timestamp.isoformat(),
                    "persisted": persisted,
                },
            ))
        else:
            results.append(
                {"index": index, "status": "rejected", "errors": serializer.errors}
            )

    mark_latest_many(latest)
    publish_events(events)

    if to_create:
        Reading.objects.bulk_create(to_create, ignore_conflicts=True)
        mark_persisted_many(to_mark_persisted)

    accepted = sum(1 for r in results if r["status"] == "accepted")
    persisted_count = sum(1 for r in results if r.get("persisted"))
    return {
        "accepted": accepted,
        "persisted": persisted_count,
        "rejected": len(results) - accepted,
        "results": results,
    }
