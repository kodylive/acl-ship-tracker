"""Collect American Cruise Lines ship schedules from CruiseMapper's public ship pages.

Writes docs/data/schedule.json, which the web app reads. Standard library only,
so it runs on GitHub Actions without installing anything.
"""

import html
import json
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

FLEET_URL = "https://www.cruisemapper.com/cruise-lines/American-Cruise-Lines-87"
OUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "data" / "schedule.json"
USER_AGENT = "ACL-Ship-Tracker/1.0 (personal schedule viewer; github.com)"
REQUEST_DELAY_SECONDS = 2  # be polite: one page every couple of seconds

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def text(fragment: str) -> str:
    """Strip tags and collapse whitespace."""
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(fragment)).replace("\xa0", " ").strip()


def ship_links(fleet_html: str) -> list[str]:
    links = re.findall(r'href="(https://www\.cruisemapper\.com/ships/[A-Za-z0-9-]+-\d+)"', fleet_html)
    return sorted(set(links))


def parse_cruise_list(page: str) -> list[dict]:
    """The 'Itineraries' table: one row per cruise (start date, length, from/to)."""
    cruises = []
    for row in re.findall(r'<tr data-row="\d+"[^>]*>(.*?)</tr>', page, re.S):
        start_m = re.search(r'class="cruiseDatetime">\s*(\d{4}) (\w{3}) (\d{1,2})', row)
        title_m = re.search(r'class="cruiseTitle">(.*?)</td>', row, re.S)
        if not (start_m and title_m):
            continue
        start = date(int(start_m.group(1)), MONTHS[start_m.group(2)], int(start_m.group(3)))
        title = text(title_m.group(1))
        nights_m = re.search(r"(\d+)\s+nights?", title)
        nights = int(nights_m.group(1)) if nights_m else None

        origin = dest = None
        one_way = re.search(r"one-way from (.+?) to (.+)$", title)
        round_trip = re.search(r"round-trip from (.+)$", title)
        if one_way:
            origin, dest = one_way.group(1), one_way.group(2)
        elif round_trip:
            origin = dest = round_trip.group(1)

        dep_m = re.search(r'class="cruiseDeparture">(.*?)</td>', row, re.S)
        price_m = re.search(r'class="cruisePrice">(.*?)</td>', row, re.S)
        cruises.append({
            "start": start.isoformat(),
            "end": (start + timedelta(days=nights)).isoformat() if nights is not None else None,
            "nights": nights,
            "title": title,
            "from": origin or (text(dep_m.group(1)) if dep_m else None),
            "to": dest,
            "price": (text(price_m.group(1)) or None) if price_m else None,
            "days": None,
        })
    return cruises


def parse_current_itinerary(page: str) -> tuple[str | None, list[dict]]:
    """The 'Current itinerary' block: day-by-day ports for the current or next cruise."""
    block_m = re.search(r'Current\s+itinerary\s+of.*?<table[^>]*>(.*?)</table>', page, re.S)
    begins_m = re.search(r"itinerary\s+begins\s+on\s*<strong>\s*(\w+ \d{1,2}, \d{4})\s*</strong>", page)
    if not (block_m and begins_m):
        return None, []
    begins = datetime.strptime(begins_m.group(1), "%B %d, %Y").date()

    days = []
    for row in re.findall(r"<tr>(.*?)</tr>", block_m.group(1), re.S):
        date_m = re.search(r'class="date">(.*?)</td>', row, re.S)
        cell_m = re.search(r'class="text">(.*?)</td>', row, re.S)
        if not (date_m and cell_m):
            continue
        d = re.match(r"(\d{1,2}) (\w{3})(?:\s+(\d{1,2}:\d{2}))?", text(date_m.group(1)))
        if not d or d.group(2) not in MONTHS:
            continue
        month, day_num = MONTHS[d.group(2)], int(d.group(1))
        year = begins.year + (1 if month < begins.month else 0)  # cruise crosses New Year

        cell = cell_m.group(1)
        port_m = re.search(r'<a href="https://www\.cruisemapper\.com/ports/[^"]+">(.*?)</a>', cell, re.S)
        if "fa-flag-checkered" in cell:
            kind = "arrive"
        elif "fa-flag" in cell:
            kind = "depart"
        elif port_m:
            kind = "port"
        else:
            kind = "cruising"
        days.append({
            "date": date(year, month, day_num).isoformat(),
            "time": d.group(3),
            "type": kind,
            "port": text(port_m.group(1)) if port_m else None,
            "note": None if port_m else text(cell),
        })
    return begins.isoformat(), days


def parse_ship(url: str, page: str) -> dict:
    name_m = re.search(r'<h1[^>]*>(.*?)</h1>', page, re.S)
    name = text(name_m.group(1)) if name_m else url.rsplit("/", 1)[-1]
    name = re.sub(r"^ACL\s+", "", name)

    pos_m = re.search(r'class="col-md-4 currentItineraryInfo">\s*<p>(.*?)</p>', page, re.S)
    position = text(pos_m.group(1)) if pos_m else None
    mmsi_m = re.search(r'\?mmsi=(\d{9})', page)  # the ship's AIS id, used by ais_eta.py

    cruises = parse_cruise_list(page)
    begins, days = parse_current_itinerary(page)
    if begins and days:
        match = next((c for c in cruises if c["start"] == begins), None)
        if match:
            match["days"] = days

    return {"name": name, "url": url, "mmsi": mmsi_m.group(1) if mmsi_m else None,
            "position": position, "cruises": cruises}


def main() -> int:
    fleet = fetch(FLEET_URL)
    links = ship_links(fleet)
    if not links:
        print("No ship links found on the fleet page; leaving existing data untouched.")
        return 1

    ships, failures = [], []
    for url in links:
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            ships.append(parse_ship(url, fetch(url)))
            print(f"ok   {url}")
        except Exception as exc:  # keep going; one bad page shouldn't sink the run
            failures.append(url)
            print(f"FAIL {url}: {exc}")

    if not ships:
        print("Every ship page failed; leaving existing data untouched.")
        return 1

    # Ships with no cruise from today onward (e.g. retired vessels) aren't useful here.
    today = date.today().isoformat()
    ships = [s for s in ships if any((c["end"] or c["start"]) >= today for c in s["cruises"])]
    ships.sort(key=lambda s: s["name"])

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": FLEET_URL,
        "ships": ships,
    }, indent=1), encoding="utf-8")
    print(f"Wrote {len(ships)} ships to {OUT_PATH} ({len(failures)} failed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
