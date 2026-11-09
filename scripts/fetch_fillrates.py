"""
Fetch underground container fill rates from the Burgerportaal Mendix web app.

Uses Playwright to navigate the Mendix web app and intercept the XAS response
containing all container locations with live fill rates. The data is returned
by the map widget after navigating: address -> Informatie -> Ondergrondse containers.

Requirements:
    pip install playwright
    playwright install chromium
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from daily_capture import validate_containers

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Error: playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)

BASE_URL = "https://21burgerportaal.mendixcloud.com"

# Address config (any valid Assen address works - containers are city-wide)
ZIPCODE = "9402JK"
HOUSENUMBER = "4"
ADDRESS_MATCH = "4J"


def navigate_to_containers(page):
    """Select visible controls, not boxless responsive grid wrappers."""
    print("  Loading page...")
    page.goto(f"{BASE_URL}/p/assen/landing/", wait_until="domcontentloaded")

    print("  Filling address...")
    page.locator("input[placeholder='uw postcode']").first.fill(ZIPCODE)
    page.locator("input[placeholder='uw huisnummer']").first.fill(HOUSENUMBER)

    print("  Clicking Volgende...")
    page.get_by_role("button", name="Volgende", exact=True).click()

    print("  Selecting address...")
    page.get_by_role(
        "button", name=re.compile(rf"\b{re.escape(ADDRESS_MATCH)},")
    ).click()

    print("  Selecting Informatie...")
    page.get_by_role("button", name=re.compile(r"^Informatie\b")).click()

    print("  Selecting Ondergrondse containers...")
    page.get_by_role(
        "button", name=re.compile(r"^Ondergrondse containers\b")
    ).click()


def fetch_containers():
    """Navigate Burgerportaal and capture container fill rate data."""
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = context.new_page()

    container_data = []

    def on_response(response):
        if "/xas/" in response.url and response.status == 200:
            try:
                body = response.text()
                if len(body) > 50000:
                    data = json.loads(body)
                    objs = data.get("objects", [])
                    if any(o.get("objectType") == "Burger_Applicatie.Locatie" for o in objs):
                        container_data.append(data)
            except Exception:
                pass

    page.on("response", on_response)

    try:
        navigate_to_containers(page)

        print("  Waiting for container data...")
        for i in range(24):  # Up to 2 minutes
            if container_data:
                break
            page.wait_for_timeout(5000)
        else:
            raise TimeoutError("Container data not received within 2 minutes")

    finally:
        browser.close()
        pw.stop()

    return container_data[0] if container_data else None


def parse_containers(data):
    """Parse Mendix XAS response into container list."""
    objects = data.get("objects", [])

    fracties = {}
    locaties = []

    for obj in objects:
        ot = obj.get("objectType", "")
        attrs = obj.get("attributes", {})

        if ot == "Burger_Applicatie.Fractie":
            fracties[obj["guid"]] = attrs.get("Naam", {}).get("value", "")

        elif ot == "Burger_Applicatie.Locatie":
            fill_attribute = attrs.get("Vulgraad")
            if not isinstance(fill_attribute, dict) or "value" not in fill_attribute:
                raise ValueError("Container response is missing Vulgraad")
            vulgraad = fill_attribute["value"]
            if isinstance(vulgraad, str):
                vulgraad = int(vulgraad)
            elif vulgraad is None:
                vulgraad = 0

            loc = {
                "nr": attrs.get("ContainerNummer", {}).get("value"),
                "lat": attrs.get("Latitude", {}).get("value"),
                "lng": attrs.get("Longitude", {}).get("value"),
                "vulgraad": vulgraad,
                "adres": attrs.get("AdresVolledig", {}).get("value"),
                "fractieKleur": attrs.get("FractieKleur", {}).get("value"),
                "heeftSensor": attrs.get("HeeftSensor", {}).get("value", False),
            }
            fguid = attrs.get("Burger_Applicatie.Locatie_Fractie", {}).get("value")
            color_map = {
                "GREY": "Restafval", "GREEN": "GFT",
                "BLUE": "Papier", "ORANGE": "PMD", "SKYBLUE": "Glas"
            }
            loc["fractie"] = fracties.get(fguid, color_map.get(loc["fractieKleur"], "Onbekend"))
            locaties.append(loc)

    # Second pass for locations that preceded their fractie definitions
    for loc in locaties:
        if loc["fractie"] in ("Onbekend", ""):
            color_map = {
                "GREY": "Restafval", "GREEN": "GFT",
                "BLUE": "Papier", "ORANGE": "PMD", "SKYBLUE": "Glas"
            }
            loc["fractie"] = color_map.get(loc["fractieKleur"], "Onbekend")

    return locaties


def main(output_dir=None):
    output_dir = output_dir or Path(__file__).parent.parent / "data"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "containers_fillrates.json"
    state_file = output_dir / "state.json"
    events_file = output_dir / "events.ndjson"
    history_file = output_dir / "history.json"

    ams_tz = ZoneInfo("Europe/Amsterdam")

    print("Fetching container fill rates from Burgerportaal...\n")

    try:
        data = fetch_containers()

        if not data:
            print("\n!! No container data received!")
            sys.exit(1)

        locaties = parse_containers(data)

        if not locaties:
            print("\n!! Response received but no containers parsed!")
            sys.exit(1)

        previous_count = 0
        if output_file.exists():
            with open(output_file, encoding="utf-8") as f:
                previous_count = len(json.load(f))
        validate_containers(locaties, previous_count)
        now_ams = datetime.now(ams_tz)

        # Save full data
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(locaties, f, indent=2, ensure_ascii=False)

        # Build current state {nr: vulgraad}
        current_state = {l["nr"]: l["vulgraad"] for l in locaties if l["nr"]}

        # Load previous state and diff
        prev_state = {}
        if state_file.exists():
            with open(state_file, encoding="utf-8") as f:
                prev_state = json.load(f)

        # Detect changes
        changes = []
        for nr, val in current_state.items():
            prev_val = prev_state.get(nr)
            if prev_val is not None and prev_val != val:
                loc = next((l for l in locaties if l["nr"] == nr), None)
                changes.append({
                    "ts": now_ams.isoformat(),
                    "nr": nr,
                    "adres": loc["adres"] if loc else "",
                    "fractie": loc["fractie"] if loc else "",
                    "from": prev_val,
                    "to": val,
                })

        # Append changes to events log
        if changes:
            with open(events_file, "a", encoding="utf-8") as f:
                for ev in changes:
                    f.write(json.dumps(ev, separators=(",", ":"), ensure_ascii=False) + "\n")
            print(f"   Changes detected: {len(changes)} containers changed")

        # Save current state
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(current_state, f, separators=(",", ":"))

        # Update rolling history (append snapshot, keep last 30 days = 1 per day)
        snapshot = {"ts": now_ams.isoformat(), "containers": current_state}
        hist = {"samples": [], "history": []}
        if history_file.exists():
            with open(history_file, encoding="utf-8") as f:
                hist = json.load(f)
        # Replace today's entry if it already exists, otherwise append
        today = now_ams.strftime("%Y-%m-%d")
        hist["history"] = [h for h in hist["history"] if not h["ts"].startswith(today)]
        hist["history"].append(snapshot)
        # Keep max 30 snapshots (30 days)
        hist["history"] = hist["history"][-30:]
        # Update samples (first 8 containers with sensor + data)
        if not hist["samples"]:
            samples = [l for l in locaties if l["heeftSensor"] and l["vulgraad"] > 0][:8]
            hist["samples"] = [{"nr": s["nr"], "adres": s["adres"], "fractie": s["fractie"]} for s in samples]
        # Store fractie map for all containers (enables per-fractie timeline)
        hist["fractie_map"] = {l["nr"]: l["fractie"] for l in locaties if l["nr"] and l["fractie"]}
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(hist, f, separators=(",", ":"), ensure_ascii=False)

        # Stats
        with_sensor = sum(1 for l in locaties if l["heeftSensor"])
        with_fill = sum(1 for l in locaties if l["vulgraad"] > 0)
        avg_fill = sum(l["vulgraad"] for l in locaties) / len(locaties)

        print(f"\nSuccess! Saved {len(locaties)} containers to {output_file}")
        print(f"   Sensors: {with_sensor} | With fill data: {with_fill} | Avg fill: {avg_fill:.1f}%")

        # Save metadata
        meta_file = output_dir / "meta.json"
        meta = {
            "source": "burgerportaal-mendix",
            "url": BASE_URL,
            "updated": now_ams.isoformat(),
            "timezone": "Europe/Amsterdam",
            "containers": len(locaties),
            "with_sensor": with_sensor,
            "avg_vulgraad": round(avg_fill, 1),
        }
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

    except TimeoutError as e:
        print(f"\nTimeout: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    main(parser.parse_args().output_dir)
