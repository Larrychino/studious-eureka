"""Device simulator: plays fridge sensors and weight trays against a running MedVerify.

Start the server with demo data first:
    python -m medverify serve --demo
Then, in another terminal:
    python simulator/simulate.py                 # normal readings every 5 s
    python simulator/simulate.py --warm-fridge   # make Oak nursing-unit fridge go out of range
    python simulator/simulate.py --tray-removal  # take an item out of tray R-001 without recording it

Uses the demo device keys from medverify/seed.py.
"""

from __future__ import annotations

import argparse
import random
import time
from datetime import datetime, timezone

import httpx

SENSORS = {
    "SENSOR-OAK-1": "demo-sensor-oak-1",
    "SENSOR-OAK-2": "demo-sensor-oak-2",
    "SENSOR-BIRCH-1": "demo-sensor-birch-1",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def post(client: httpx.Client, path: str, device: str, key: str, payload: dict) -> dict:
    r = client.post(path, json=payload, headers={"X-Device-Id": device, "X-Device-Key": key})
    r.raise_for_status()
    return r.json()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--cycles", type=int, default=0, help="0 = run forever")
    ap.add_argument("--warm-fridge", action="store_true")
    ap.add_argument("--tray-removal", action="store_true")
    args = ap.parse_args()

    with httpx.Client(base_url=args.url, timeout=10) as client:
        if args.tray_removal:
            r = post(client, "/api/devices/tray-events", "TRAY-OAK-R001", "demo-tray-oak-r001",
                     {"events": [{"ts": now_iso(), "weight_before_g": 33.5, "weight_after_g": 9.5}]})
            print("tray removal sent:", r, "- a mismatch is raised once the reconcile window passes")
        n = 0
        while args.cycles == 0 or n < args.cycles:
            for device, key in SENSORS.items():
                temp = random.gauss(4.8, 0.3)
                if args.warm_fridge and device == "SENSOR-OAK-2":
                    temp = 9.5 + n * 0.3
                r = post(client, "/api/devices/readings", device, key,
                         {"readings": [{"ts": now_iso(), "temp_c": round(temp, 1), "door_open": False}],
                          "firmware": "sim-1.0"})
                print(f"{device}: {temp:.1f}°C -> {r}")
            n += 1
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
