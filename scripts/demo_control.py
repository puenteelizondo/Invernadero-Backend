#!/usr/bin/env python3
"""Demostración automática del panel Control: "juega" con setpoints y actuadores.

Pensado para correr JUNTO con el simulador del controlador:

  1) docker compose exec web python scripts/simulate_controller.py --key <API_KEY> --speed 5
  2) docker compose exec web python scripts/demo_control.py --user <usuario> --password <clave> --key <API_KEY>

Qué hace, en bucle, como lo haría una persona desde la web:
  - Activa los lazos del invernadero que estén en "Apagado" (PID, o On/Off para
    CO2 / humedad / luz). Los setpoints NO se tocan: el simulador del controlador
    mete perturbaciones (sol, puerta abierta...) que pasan la medición por encima
    o por debajo del setpoint, y se ve al lazo regresarla.
    Con --mover-setpoints, además cambia el setpoint de cada lazo (arriba, abajo
    y de regreso).
  - Prende y apaga los actuadores manuales (los que no están en un lazo activo).
  - Con --key, manda lecturas de los sensores SIN lazo de ese dispositivo (los
    que tienen lazo los manda el simulador del controlador), para que la planta
    de la web tenga todas sus variables.

Todo pasa por la API normal: los cambios de setpoint quedan en el historial de
cada lazo, como si los hubiera hecho el usuario. Al detenerlo con Ctrl+C
regresa los lazos y los actuadores a como estaban.

Solo usa la librería estándar.
"""
import argparse
import base64
import json
import math
import random
import time
import urllib.error
import urllib.request

# Variables donde On/Off es lo natural (actuador de prender/apagar).
ON_OFF_CODES = {"co2", "humidity", "light", "soil_moisture"}
# Rangos realistas para simular sensores sin lazo (código del tipo -> mín, máx).
REALISTIC = {
    "temperature": (18, 32), "humidity": (45, 85), "soil_moisture": (30, 70),
    "light": (5000, 40000), "co2": (400, 1200), "ph": (5.5, 7.0), "ec": (1.0, 3.0),
    "wind_speed": (0.0, 6.0),
}


class Api:
    def __init__(self, base, user, password):
        self.base = base.rstrip("/") + "/api/v1"
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.auth = {"Authorization": f"Basic {token}"}

    def req(self, method, path, body=None, headers=None):
        url = path if path.startswith("http") else self.base + path
        data = json.dumps(body).encode() if body is not None else None
        h = {"Accept": "application/json", **self.auth, **(headers or {})}
        h = {k: v for k, v in h.items() if v}          # "" = quitar ese encabezado
        if data is not None:
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(url, data=data, method=method, headers=h)
        with urllib.request.urlopen(r, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None

    def all(self, path):
        """GET que sigue la paginación de DRF (o acepta una lista simple)."""
        out, url = [], path
        while url:
            page = self.req("GET", url)
            if isinstance(page, list):
                return page
            out += page["results"]
            url = page.get("next")
        return out


def fmt(v):
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000", help="base del backend")
    ap.add_argument("--user", required=True, help="usuario owner/operator del invernadero (o staff)")
    ap.add_argument("--password", required=True)
    ap.add_argument("--greenhouse", type=int, default=None, help="id del invernadero (por defecto, el primero)")
    ap.add_argument("--key", default=None, help="API key de un dispositivo: simula sus sensores SIN lazo")
    ap.add_argument("--mover-setpoints", action="store_true", help="también cambiar los setpoints cada --step segundos")
    ap.add_argument("--step", type=float, default=40.0, help="segundos entre cambios de setpoint (con --mover-setpoints)")
    ap.add_argument("--toggle", type=float, default=15.0, help="segundos entre cambios de los actuadores manuales")
    ap.add_argument("--ingest", type=float, default=2.0, help="segundos entre lecturas simuladas (con --key)")
    a = ap.parse_args()

    api = Api(a.url, a.user, a.password)
    try:
        gh = a.greenhouse or api.all("/greenhouses/")[0]["id"]
        loops = api.all(f"/control-loops/?greenhouse={gh}")
        actuators = api.all(f"/actuators/?greenhouse={gh}")
        sensors = api.all(f"/sensors/?greenhouse={gh}")
        types = {t["id"]: t for t in api.all("/sensor-types/")}
        devices = api.all(f"/devices/?greenhouse={gh}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code}: {e.read().decode()[:200]}\n(¿usuario/contraseña correctos y con acceso al invernadero?)")
    except OSError as e:
        raise SystemExit(f"No se pudo conectar a {a.url}: {e}")

    print(f"Invernadero {gh}: {len(loops)} lazos, {len(actuators)} actuadores, {len(sensors)} sensores.")
    if not loops:
        print("  (no hay lazos: créalos en la página Control para ver los setpoints moverse)")

    sensor_by_id = {s["id"]: s for s in sensors}
    code_of = lambda s: types.get(s["sensor_type"], {}).get("code", "")  # noqa: E731

    # ---- 1. lazos: guardar cómo estaban y activarlos -------------------------
    original = {}
    plan = []   # (loop, [setpoints...])
    for l in loops:
        original[l["id"]] = {k: l[k] for k in ("mode", "enabled", "setpoint", "kp", "ki", "kd")}
        s = sensor_by_id.get(l["sensor"])
        code = code_of(s) if s else ""
        patch = {}
        if not l["enabled"] or l["mode"] == "off":
            patch = {"mode": "on_off" if code in ON_OFF_CODES else "pid", "enabled": True}
            if patch["mode"] == "pid" and l["kp"] == 1 and l["ki"] == 0 and l["kd"] == 0:
                patch.update(kp=8, ki=0.15, kd=4)       # ganancias razonables para la demo
        sp = float(l["setpoint"])
        span = max(abs(sp) * 0.15, 0.5)
        lo, hi = l.get("valid_min"), l.get("valid_max")
        clamp = lambda v: max(lo if lo is not None else -math.inf, min(hi if hi is not None else math.inf, v))  # noqa: E731
        plan.append((l, [round(clamp(sp + span), 2), round(clamp(sp - span), 2), round(sp, 2)]))
        if patch:
            api.req("PATCH", f"/control-loops/{l['id']}/", patch)
            print(f"  Lazo “{l['name']}”: activado en {patch['mode'].upper()} (setpoint {fmt(sp)} {l['unit']})")

    # ---- 2. actuadores manuales ---------------------------------------------
    active_loop_acts = {l["actuator"] for l in loops}   # tras activar, todos los lazos están activos
    manual = [x for x in actuators if x["id"] not in active_loop_acts and x["is_active"]]
    manual_orig = {x["id"]: x["state"] for x in manual}
    print(f"  Actuadores manuales que se prenderán y apagarán: {', '.join(x['name'] for x in manual) or 'ninguno'}")

    # ---- 3. sensores sin lazo (con --key) -----------------------------------
    feed = []
    if a.key:
        dev = next((d for d in devices if a.key.startswith(d["key_prefix"])), None)
        if dev is None:
            print("  --key: no coincide con ningún dispositivo de este invernadero; no se simulan sensores.")
        else:
            loop_sensors = {l["sensor"] for l in loops}
            for s in sensors:
                if s["device"] != dev["id"] or not s["is_active"] or s["id"] in loop_sensors:
                    continue
                t = types.get(s["sensor_type"], {})
                vmin, vmax = t.get("valid_min"), t.get("valid_max")
                lo, hi = REALISTIC.get(t.get("code", ""), (None, None))
                # Nunca fuera del rango válido del tipo (si no, el servidor rechaza la lectura).
                if lo is not None:
                    if vmin is not None:
                        lo, hi = max(lo, vmin), max(hi, vmin)
                    if vmax is not None:
                        lo, hi = min(lo, vmax), min(hi, vmax)
                if lo is None or hi - lo <= 0:
                    a0, b0 = (vmin, vmax) if vmin is not None and vmax is not None else (0, 100)
                    lo, hi = a0 + (b0 - a0) / 3, b0 - (b0 - a0) / 3
                feed.append({"id": s["id"], "name": s["name"], "lo": lo, "hi": hi, "v": (lo + hi) / 2})
            print(f"  Sensores sin lazo de “{dev['name']}” que se simulan: {', '.join(f['name'] for f in feed) or 'ninguno'}")
            nopersist = [s["name"] for s in sensors if s["id"] in {f["id"] for f in feed} and s["persist_interval_seconds"] is None]
            if nopersist:
                print(f"  Ojo: {', '.join(nopersist)} no guardan historial (solo se ven en vivo).")

    print("\nCorriendo. Mira la página Control y la de Actuadores. Ctrl+C para detener y restaurar.\n")

    step_i = 0
    next_step = time.monotonic() + 5
    next_toggle = time.monotonic() + 3
    next_ingest = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            if a.mover_setpoints and plan and now >= next_step:
                for l, sps in plan:
                    sp = sps[step_i % len(sps)]
                    try:
                        api.req("PATCH", f"/control-loops/{l['id']}/", {"setpoint": sp})
                        print(f"[setpoint] “{l['name']}” -> {fmt(sp)} {l['unit']}")
                    except urllib.error.HTTPError as e:
                        print(f"[setpoint] “{l['name']}”: HTTP {e.code} {e.read().decode()[:120]}")
                step_i += 1
                next_step = now + a.step
            if manual and now >= next_toggle:
                x = random.choice(manual)
                x["state"] = not x["state"]
                try:
                    api.req("POST", f"/actuators/{x['id']}/state/", {"state": x["state"]})
                    print(f"[actuador] {x['name']} -> {'ENCENDIDO' if x['state'] else 'apagado'}")
                except urllib.error.HTTPError as e:
                    print(f"[actuador] {x['name']}: HTTP {e.code} {e.read().decode()[:120]}")
                next_toggle = now + a.toggle
            if feed and now >= next_ingest:
                readings = []
                for f in feed:
                    span = f["hi"] - f["lo"]
                    f["v"] = min(f["hi"], max(f["lo"], f["v"] + random.uniform(-0.04, 0.04) * span))
                    readings.append({"sensor_id": f["id"], "value": round(f["v"], 2)})
                try:
                    r = api.req("POST", "/readings/ingest/", {"readings": readings},
                                headers={"X-Device-Key": a.key, "Authorization": ""})
                    if r and r.get("rejected"):
                        print(f"[lecturas] {r['rejected']} rechazadas: {[x for x in r.get('results', []) if x.get('status') == 'rejected'][:2]}")
                except urllib.error.HTTPError as e:
                    print(f"[lecturas] HTTP {e.code} {e.read().decode()[:120]}")
                next_ingest = now + a.ingest
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nRestaurando como estaba ...")
        for lid, cfg in original.items():
            try:
                api.req("PATCH", f"/control-loops/{lid}/", cfg)
            except Exception as e:  # noqa: BLE001
                print(f"  lazo {lid}: no se pudo restaurar ({e})")
        for aid, st in manual_orig.items():
            try:
                api.req("POST", f"/actuators/{aid}/state/", {"state": st})
            except Exception as e:  # noqa: BLE001
                print(f"  actuador {aid}: no se pudo restaurar ({e})")
        print("Listo.")


if __name__ == "__main__":
    main()
