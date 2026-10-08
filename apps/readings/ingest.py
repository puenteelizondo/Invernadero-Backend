"""
Núcleo de la ingesta de lecturas, compartido por:

- POST /api/v1/readings/ingest/ (HTTP, `ReadingIngestView`), y
- el evento "readings" del WebSocket del dispositivo (`apps/control/consumers.py`).

Así las dos vías validan, disparan alertas, deciden qué se guarda en el
historial ("guardar cada N") y avisan a la página EXACTAMENTE igual.
"""
from apps.alerts import engine as alert_engine
from apps.common.realtime import publish_events
from apps.sensors.models import Sensor

from .models import Reading
from .persistence import mark_latest, mark_persisted, should_persist
from .serializers import ReadingIngestItemSerializer


def ingest_readings(device, raw_items):
    """
    Procesa una lista de lecturas `{"sensor_id", "value", "timestamp"?}` del
    dispositivo `device`. Devuelve `{"accepted", "persisted", "rejected", "results"}`.
    """
    # Una sola consulta para todos los sensores del lote (antes, una por lectura).
    wanted = {
        item["sensor_id"] for item in raw_items
        if isinstance(item, dict) and isinstance(item.get("sensor_id"), int)
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

    for index, item in enumerate(raw_items):
        serializer = ReadingIngestItemSerializer(
            data=item, context={"device": device, "sensors": sensors}
        )
        if serializer.is_valid():
            data = serializer.validated_data
            sensor = data["sensor"]
            value = data["value"]
            timestamp = data["timestamp"]

            persisted = should_persist(sensor, value, timestamp)
            if persisted:
                to_create.append(
                    Reading(sensor=sensor, value=value, timestamp=timestamp)
                )
                to_mark_persisted.append((sensor, value, timestamp))

            mark_latest(sensor, value, timestamp)
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

    publish_events(events)

    if to_create:
        Reading.objects.bulk_create(to_create, ignore_conflicts=True)
        for sensor, value, timestamp in to_mark_persisted:
            mark_persisted(sensor, value, timestamp)

    accepted = sum(1 for r in results if r["status"] == "accepted")
    persisted_count = sum(1 for r in results if r.get("persisted"))
    return {
        "accepted": accepted,
        "persisted": persisted_count,
        "rejected": len(results) - accepted,
        "results": results,
    }
