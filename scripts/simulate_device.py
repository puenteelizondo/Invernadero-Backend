#!/usr/bin/env python3
"""Simula un dispositivo físico (ESP32) mandando lecturas a /readings/ingest/.

Solo usa la librería estándar. Ejemplo:

  python scripts/simulate_device.py --key <API_KEY_DEL_DISPOSITIVO> ^
      --sensor 1:18:32 --sensor 2:40:80 --interval 5

Cada --sensor es ID:MIN:MAX; el valor sigue una caminata aleatoria suave
dentro de ese rango (como lo haría un sensor real). Con --spike N, cada N
envíos manda un valor fuera de rango para probar el rechazo.
"""
import argparse
import json
import random
import time
import urllib.error
import urllib.request


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000/api/v1/readings/ingest/")
    ap.add_argument("--key", required=True, help="API key completa del dispositivo")
    ap.add_argument("--sensor", action="append", required=True, metavar="ID:MIN:MAX")
    ap.add_argument("--interval", type=float, default=5.0, help="segundos entre envíos")
    ap.add_argument("--count", type=int, default=0, help="0 = infinito")
    ap.add_argument("--spike", type=int, default=0, help="cada N envíos, un valor fuera de rango")
    a = ap.parse_args()

    sensors = []
    for s in a.sensor:
        sid, lo, hi = s.split(":")
        lo, hi = float(lo), float(hi)
        sensors.append({"id": int(sid), "lo": lo, "hi": hi, "v": (lo + hi) / 2})

    n = 0
    while a.count == 0 or n < a.count:
        n += 1
        readings = []
        for s in sensors:
            span = s["hi"] - s["lo"]
            s["v"] = min(s["hi"], max(s["lo"], s["v"] + random.uniform(-0.03, 0.03) * span))
            v = s["v"]
            if a.spike and n % a.spike == 0:
                v = s["hi"] + span  # fuera de rango a propósito
            readings.append({"sensor_id": s["id"], "value": round(v, 2)})

        req = urllib.request.Request(
            a.url,
            data=json.dumps({"readings": readings}).encode(),
            headers={"Content-Type": "application/json", "X-Device-Key": a.key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                body = json.load(r)
            print(f"#{n} enviado {[x['value'] for x in readings]} -> "
                  f"aceptadas={body['accepted']} guardadas={body['persisted']} rechazadas={body['rejected']}")
            for res in body["results"]:
                if res["status"] == "rejected":
                    print("   rechazada:", res["errors"])
        except urllib.error.HTTPError as e:
            print(f"#{n} HTTP {e.code}: {e.read().decode()[:200]}")
        except Exception as e:  # red caída, etc.
            print(f"#{n} error: {e}")
        time.sleep(a.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:  # Ctrl+C: salida limpia, sin traceback
        print("\nSimulador detenido.")
