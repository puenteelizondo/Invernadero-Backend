#!/usr/bin/env python3
"""Simula un CONTROLADOR (ESP32) con planta incluida, para probar el panel de control sin hardware.

Hace lo mismo que el firmware de referencia (firmware/esp32_control/esp32_control.ino):

  1. pide un token con POST /api/v1/devices/ws-token/ (header X-Device-Key),
  2. se conecta a /ws/device/?token=...,
  3. recibe `config` / `config_update`, aplica los parámetros y responde `ack`,
  4. ejecuta el lazo (On/Off, P, PI o PID) cada `sample_time_ms` y manda `telemetry`,
  5. simula una planta de primer orden con retardo y manda la variable de proceso a
     /api/v1/readings/ingest/ como lo haría el sensor real (así la web y la planta
     ilustrada ven el valor en vivo).

El PID se calcula AQUÍ, en el "controlador"; el servidor nunca calcula la salida.

Requisitos:   pip install websockets
Ejemplo:      python scripts/simulate_controller.py --key <API_KEY> --speed 5

Con --speed 5 la planta evoluciona 5 veces más rápido que el reloj real, para ver
la respuesta al cambiar el setpoint o Kp en la web sin esperar minutos.
"""
import argparse
import asyncio
import json
import math
import random
import time
import urllib.error
import urllib.request
from collections import deque

try:
    import websockets
    # Import explícito: en websockets 14+ el atributo `websockets.exceptions` no
    # existe hasta importar el submódulo (lo cargan perezosamente).
    from websockets.exceptions import WebSocketException
except ImportError:  # mensaje claro en vez de un traceback
    raise SystemExit("Falta la librería: pip install websockets")


# --------------------------------------------------------------------------- #
#  Controlador (misma lógica que el firmware)
# --------------------------------------------------------------------------- #
class Controller:
    """On/Off, P, PI o PID con anti-windup, derivada sobre la medición y cambio sin saltos."""

    def __init__(self, cfg):
        self.cfg = {}
        self.integ = 0.0          # término integral, ya en unidades de salida (%)
        self.out = 0.0            # última salida
        self.prev_pv = None
        self.on = False           # estado del On/Off (con histéresis)
        self.p = self.i = self.d = 0.0
        self.apply(cfg)

    def apply(self, cfg):
        old = self.cfg
        self.cfg = dict(cfg)
        # Cambio sin saltos: si cambia el modo/dirección/habilitado, el integrador
        # arranca de modo que la salida continúe donde estaba.
        # Al ENCENDER desde apagado no hay nada que preservar (la salida era 0).
        was_active = bool(old) and old["enabled"] and old["mode"] != "off"
        now_active = cfg["enabled"] and cfg["mode"] != "off"
        self._bumpless = (was_active and now_active
                          and (old["mode"] != cfg["mode"] or old["direction"] != cfg["direction"]))

    def step(self, pv, dt):
        c = self.cfg
        omin, omax = c["output_min"], c["output_max"]
        if not c["enabled"] or c["mode"] == "off":
            self.out = 0.0
            self.integ = 0.0
            self.on = False
            self.prev_pv = pv
            self.p = self.i = self.d = 0.0
            return 0.0

        sign = 1.0 if c["direction"] == "direct" else -1.0
        err = sign * (c["setpoint"] - pv)

        if c["mode"] == "on_off":
            h = c["hysteresis"] / 2.0
            if err > h:
                self.on = True
            elif err < -h:
                self.on = False
            self.out = omax if self.on else 0.0
            self.p = self.i = self.d = 0.0
            self.prev_pv = pv
            return self.out

        self.p = c["kp"] * err
        # Derivada sobre la MEDICIÓN: un salto de setpoint no provoca un pico.
        if c["mode"] == "pid" and self.prev_pv is not None and dt > 0:
            self.d = -c["kd"] * sign * (pv - self.prev_pv) / dt
        else:
            self.d = 0.0
        self.prev_pv = pv

        if self._bumpless:
            self._bumpless = False
            if c["mode"] in ("pi", "pid"):
                self.integ = self.out - self.p - self.d
            else:
                self.integ = 0.0
            self.integ = max(-c["integral_limit"], min(c["integral_limit"], self.integ))

        if c["mode"] in ("pi", "pid"):
            unsat = self.p + self.integ + self.d
            # Anti-windup: no integrar si la salida ya está saturada y el error empuja más.
            saturating = (unsat >= omax and err > 0) or (unsat <= omin and err < 0)
            if not saturating:
                self.integ += c["ki"] * err * dt
            self.integ = max(-c["integral_limit"], min(c["integral_limit"], self.integ))
            self.i = self.integ
        else:
            self.i = 0.0

        u = self.p + self.i + self.d
        self.out = max(omin, min(omax, u))
        return self.out


# --------------------------------------------------------------------------- #
#  Planta simulada: primer orden + retardo de transporte
# --------------------------------------------------------------------------- #
class Plant:
    def __init__(self, ambient, gain, tau, delay, noise):
        self.pv = ambient
        self.ambient, self.gain, self.tau, self.delay, self.noise = ambient, gain, tau, delay, noise
        self.queue = deque()      # (t_efectivo, salida%)
        self.t = 0.0
        self.u_eff = 0.0

    def step(self, u, dt, direction):
        self.t += dt
        self.queue.append((self.t + self.delay, u))
        while self.queue and self.queue[0][0] <= self.t:
            self.u_eff = self.queue.popleft()[1]
        # direct: la salida SUBE la variable (calefactor); reverse: la BAJA (ventilador).
        g = self.gain if direction == "direct" else -self.gain
        d = (-(self.pv - self.ambient) + g * self.u_eff / 100.0) / self.tau
        self.pv += d * dt
        return self.pv + random.gauss(0, self.noise)


# --------------------------------------------------------------------------- #
async def post_json(url, key, body):
    def _do():
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "X-Device-Key": key},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    return await asyncio.to_thread(_do)


async def run_session(a):
    base = a.url.rstrip("/")
    tok = await post_json(f"{base}/api/v1/devices/ws-token/", a.key, {})
    ws_url = base.replace("http", "ws", 1) + f"/ws/device/?token={tok['token']}"
    print(f"Conectando a {ws_url.split('?')[0]} ...")

    loops = {}   # id -> {"ctrl", "plant", "cfg", "next"}
    async with websockets.connect(ws_url, ping_interval=None) as ws:
        print("Conectado. Esperando configuración del servidor.")

        def load(cfg):
            lid = cfg["id"]
            if lid in loops:
                loops[lid]["ctrl"].apply(cfg)
                loops[lid]["cfg"] = cfg
            else:
                plant = Plant(a.ambient, a.gain, a.tau, a.delay, a.noise)
                loops[lid] = {"ctrl": Controller(cfg), "plant": plant, "cfg": cfg,
                              "next": 0.0, "last": time.monotonic()}
            return lid

        async def ack(cfg):
            await ws.send(json.dumps({"event": "ack", "loop_id": cfg["id"], "version": cfg["version"]}))
            print(f"  -> ack lazo {cfg['id']} v{cfg['version']}  "
                  f"[{cfg['mode']} sp={cfg['setpoint']} kp={cfg['kp']} ki={cfg['ki']} kd={cfg['kd']} "
                  f"{'ON' if cfg['enabled'] else 'deshabilitado'}]")

        async def reader():
            async for raw in ws:
                msg = json.loads(raw)
                ev = msg.get("event")
                if ev == "config":
                    for cfg in msg["loops"]:
                        load(cfg)
                        await ack(cfg)
                    if not msg["loops"]:
                        print("  (este dispositivo aún no tiene lazos; créalos en la web)")
                elif ev == "config_update":
                    load(msg["loop"])
                    await ack(msg["loop"])
                elif ev == "config_remove":
                    loops.pop(msg["loop_id"], None)
                    print(f"  lazo {msg['loop_id']} eliminado")
                elif ev == "ping":
                    await ws.send(json.dumps({"event": "pong"}))

        async def runner():
            last_ingest = 0.0
            while True:
                await asyncio.sleep(0.05)
                now = time.monotonic()
                for lid, L in list(loops.items()):
                    cfg = L["cfg"]
                    if now < L["next"]:
                        continue
                    L["next"] = now + cfg["sample_time_ms"] / 1000.0
                    dt_real = now - L["last"]
                    L["last"] = now
                    dt = dt_real * a.speed
                    ctrl, plant = L["ctrl"], L["plant"]
                    pv = plant.pv
                    out = ctrl.step(pv, dt)
                    L["pv_meas"] = plant.step(out, dt, cfg["direction"])
                    await ws.send(json.dumps({
                        "event": "telemetry", "loop_id": lid, "version": cfg["version"],
                        "pv": round(pv, 3), "setpoint": cfg["setpoint"], "output": round(out, 2),
                        "error": round(cfg["setpoint"] - pv, 3),
                        "p": round(ctrl.p, 2), "i": round(ctrl.i, 2), "d": round(ctrl.d, 2),
                        "mode": cfg["mode"] if cfg["enabled"] else "off",
                    }))
                # La medición real entra por la ingesta normal (no por el WebSocket).
                if now - last_ingest >= a.ingest_interval and loops:
                    last_ingest = now
                    readings = [{"sensor_id": L["cfg"]["sensor_id"], "value": round(L["pv_meas"], 2)}
                                for L in loops.values() if "pv_meas" in L]
                    if readings:
                        try:
                            await post_json(f"{base}/api/v1/readings/ingest/", a.key, {"readings": readings})
                        except urllib.error.HTTPError as e:
                            print(f"  ingesta HTTP {e.code}: {e.read().decode()[:120]}")

        await asyncio.gather(reader(), runner())


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:8000", help="base del backend")
    ap.add_argument("--key", required=True, help="API key completa del dispositivo")
    ap.add_argument("--speed", type=float, default=5.0, help="aceleración de la planta (1 = tiempo real)")
    ap.add_argument("--ambient", type=float, default=18.0, help="valor inicial/ambiente de la variable")
    ap.add_argument("--gain", type=float, default=15.0, help="cuánto cambia la variable con salida 100%% (en régimen)")
    ap.add_argument("--tau", type=float, default=120.0, help="constante de tiempo de la planta (s simulados)")
    ap.add_argument("--delay", type=float, default=8.0, help="retardo de transporte (s simulados)")
    ap.add_argument("--noise", type=float, default=0.03, help="ruido de medición (desv. estándar)")
    ap.add_argument("--ingest-interval", type=float, default=2.0, help="cada cuántos s se manda la lectura")
    a = ap.parse_args()

    backoff = 1.0
    while True:
        try:
            await run_session(a)
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code}: {e.read().decode()[:160]}. Reintento en {backoff:.0f} s ...")
        except (OSError, WebSocketException) as e:
            print(f"Sin conexión ({e}). Reintento en {backoff:.0f} s ...")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30.0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSimulador detenido.")
