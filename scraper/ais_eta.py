"""Collect each ship's next-port ETA from live AIS data (aisstream.io).

Ships broadcast a "static data" message every few minutes that includes the
destination and ETA the crew entered. This listens for a while, keeps the newest
report per ship, and writes docs/data/eta.json for the web app.

Needs the AISSTREAM_API_KEY environment variable (free key from aisstream.io).
Without it, the script does nothing and exits cleanly.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"
SCHEDULE_PATH = DATA_DIR / "schedule.json"
OUT_PATH = DATA_DIR / "eta.json"
STREAM_URL = "wss://stream.aisstream.io/v0/stream"
LISTEN_SECONDS = 8 * 60      # static data repeats about every 6 minutes
KEEP_REPORTS_FOR = timedelta(hours=24)  # reuse an older report if this run didn't hear the ship


def parse_eta(eta: dict, now: datetime) -> datetime | None:
    """AIS ETA is month/day/hour/minute in UTC with no year; zeros/24/60 mean 'not set'."""
    try:
        month, day, hour, minute = (int(eta[k]) for k in ("Month", "Day", "Hour", "Minute"))
    except (KeyError, TypeError, ValueError):
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60):
        return None
    for year in (now.year, now.year + 1, now.year - 1):
        try:
            when = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            return None
        if abs(when - now) < timedelta(days=183):  # pick the year that puts it nearest today
            return when
    return None


def report_from_message(msg: dict, now: datetime) -> tuple[str, dict] | None:
    if msg.get("MessageType") != "ShipStaticData":
        return None
    static = msg.get("Message", {}).get("ShipStaticData", {})
    mmsi = str(msg.get("MetaData", {}).get("MMSI") or static.get("UserID") or "")
    eta = parse_eta(static.get("Eta") or {}, now)
    destination = " ".join(str(static.get("Destination") or "").replace("@", " ").split())
    if not mmsi or not (eta or destination):
        return None
    return mmsi, {
        "destination": destination or None,
        "eta": eta.isoformat() if eta else None,
        "reported": now.isoformat(timespec="seconds"),
    }


async def listen(api_key: str, mmsis: list[str]) -> dict:
    import websockets  # installed by the workflow; imported here so the parsing above has no deps

    reports = {}
    subscribe = {
        "APIKey": api_key,
        "BoundingBoxes": [[[-90, -180], [90, 180]]],
        "FiltersShipMMSI": mmsis,
        "FilterMessageTypes": ["ShipStaticData"],
    }
    loop = asyncio.get_running_loop()
    deadline = loop.time() + LISTEN_SECONDS
    async with websockets.connect(STREAM_URL) as ws:
        await ws.send(json.dumps(subscribe))
        while (remaining := deadline - loop.time()) > 0:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            if "error" in msg:
                raise RuntimeError(f"aisstream: {msg['error']}")
            found = report_from_message(msg, datetime.now(timezone.utc))
            if found:
                reports[found[0]] = found[1]
                print(f"heard {found[0]}: {found[1]['destination']} ETA {found[1]['eta']}")
    return reports


def main() -> int:
    api_key = os.environ.get("AISSTREAM_API_KEY", "").strip()
    if not api_key:
        print("AISSTREAM_API_KEY not set; skipping AIS ETA update.")
        return 0

    ships = json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))["ships"]
    mmsis = sorted({s["mmsi"] for s in ships if s.get("mmsi")})
    if not mmsis:
        print("No MMSI numbers in schedule.json; nothing to listen for.")
        return 0

    now = datetime.now(timezone.utc)
    previous = {}
    if OUT_PATH.exists():
        previous = json.loads(OUT_PATH.read_text(encoding="utf-8")).get("ships", {})
    fresh = asyncio.run(listen(api_key, mmsis))

    merged = {}
    for mmsi in mmsis:
        old = previous.get(mmsi)
        if mmsi in fresh:
            merged[mmsi] = fresh[mmsi]
        elif old and now - datetime.fromisoformat(old["reported"]) < KEEP_REPORTS_FOR:
            merged[mmsi] = old

    OUT_PATH.write_text(json.dumps({
        "updated": now.isoformat(timespec="seconds"),
        "ships": merged,
    }, indent=1), encoding="utf-8")
    print(f"Heard {len(fresh)} of {len(mmsis)} ships this run; {len(merged)} ETAs saved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
