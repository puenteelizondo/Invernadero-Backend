from django.core.cache import cache

_UNSET = object()   # "no me pasaron lo último guardado: búscalo en Redis"


def _persisted_cache_key(sensor_id: int) -> str:
    return f"reading_persist:sensor:{sensor_id}"


def _latest_cache_key(sensor_id: int) -> str:
    return f"sensor:{sensor_id}:latest"


def should_persist(sensor, value: float, timestamp, last=_UNSET) -> bool:
    """
    Decide si esta lectura debe escribirse en Postgres, según la
    política de persistencia del sensor (campos ya existentes en
    apps.sensors.models.Sensor: persist_interval_seconds y
    persist_deadband).

    Reglas (documentadas también en el help_text de esos campos):
    - persist_interval_seconds es None -> nunca persistir (solo tiempo real).
    - persist_interval_seconds == 0    -> persistir siempre.
    - persist_interval_seconds == N    -> como máximo 1 lectura guardada
      cada N segundos, salvo que el valor cambie al menos
      persist_deadband desde la ÚLTIMA lectura GUARDADA (no desde la
      última recibida) — en ese caso se persiste aunque no haya pasado
      el intervalo.

    El "último guardado" (valor + timestamp) se consulta en Redis, no
    en Postgres: es un dato efímero, de altísima frecuencia de
    escritura, que no necesita durabilidad (la verdad duradera son las
    filas de Reading que sí se guardan) ni tiene sentido consultar con
    una query SQL extra en cada lectura ingerida.

    Importante: la comparación de "cuánto tiempo pasó" usa el
    timestamp de la propia lectura (el dato del sensor), no el reloj
    del servidor — así el intervalo refleja la frecuencia de muestreo
    real del sensor, sin importar cuándo llegó la petición HTTP.
    """
    interval = sensor.persist_interval_seconds
    if interval is None:
        return False
    if interval == 0:
        return True

    if last is _UNSET:
        last = cache.get(_persisted_cache_key(sensor.id))
    if last is None:
        # Nunca se ha guardado nada de este sensor: la primera lectura
        # siempre se persiste, para tener un punto de partida.
        return True

    elapsed = (timestamp - last["timestamp"]).total_seconds()
    if elapsed >= interval:
        return True

    deadband = sensor.persist_deadband
    if deadband is not None and deadband > 0 and abs(value - last["value"]) >= deadband:
        return True

    return False


def mark_persisted(sensor, value: float, timestamp) -> None:
    """
    Registra en Redis que esta lectura fue la última en persistirse
    para este sensor. Se llama SOLO cuando should_persist() devolvió
    True y la fila ya se escribió en Postgres — así el siguiente
    cálculo de "cuánto cambió" compara contra lo que de verdad quedó
    guardado, no contra cualquier lectura recibida.

    timeout=None: sin expiración. Si el sensor deja de mandar datos,
    no queremos "olvidar" su última lectura guardada y tratar la
    siguiente que llegue, meses después, como si fuera la primera vez.
    """
    cache.set(
        _persisted_cache_key(sensor.id),
        {"value": value, "timestamp": timestamp},
        timeout=None,
    )


def load_persisted(sensor_ids) -> dict:
    """Lo último GUARDADO de varios sensores en UN viaje a Redis: {sensor_id: {"value", "timestamp"}}."""
    keys = {_persisted_cache_key(sid): sid for sid in sensor_ids}
    if not keys:
        return {}
    return {keys[k]: v for k, v in cache.get_many(list(keys)).items()}


def mark_latest_many(latest: dict) -> None:
    """Como mark_latest, para varios sensores en UN viaje a Redis. `latest` = {sensor_id: (value, timestamp)}."""
    if latest:
        cache.set_many(
            {_latest_cache_key(sid): {"value": v, "timestamp": ts} for sid, (v, ts) in latest.items()},
            timeout=None,
        )


def mark_persisted_many(items) -> None:
    """Como mark_persisted, para varias lecturas en UN viaje a Redis. `items` = [(sensor, value, timestamp)]."""
    data = {_persisted_cache_key(s.id): {"value": v, "timestamp": ts} for s, v, ts in items}
    if data:
        cache.set_many(data, timeout=None)


def mark_latest(sensor, value: float, timestamp) -> None:
    """
    Registra en Redis el último valor RECIBIDO de este sensor, se haya
    persistido o no en Postgres. Es una caché distinta de la de
    mark_persisted/should_persist (esa refleja lo guardado; esta
    refleja lo más reciente en tiempo real) porque responden preguntas
    distintas:

    - should_persist() necesita "¿cuánto cambió desde lo último que
      GUARDAMOS?" para decidir si vale la pena otra fila en Postgres.
    - El snapshot al conectar un WebSocket (Etapa 9) necesita "¿cuál
      es el dato más reciente que tenemos de este sensor, ahora
      mismo?", sin importar si esa lectura en particular se guardó.

    Mezclar ambas cachés haría que un sensor con intervalo largo (ej.
    guardar cada 10 minutos) le mostrara a un cliente que se conecta
    un valor de hace 10 minutos, en vez del dato real más reciente que
    ya tenemos en memoria.
    """
    cache.set(
        _latest_cache_key(sensor.id),
        {"value": value, "timestamp": timestamp},
        timeout=None,
    )


def get_latest(sensor_id: int):
    """
    Devuelve {"value": ..., "timestamp": ...} o None si nunca ha
    llegado una lectura de este sensor desde que Redis tiene datos
    (por ejemplo, un sensor recién creado, o Redis reiniciado).
    """
    return cache.get(_latest_cache_key(sensor_id))