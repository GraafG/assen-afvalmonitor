"""
Fetch underground container fill rates from the Burgerportaal Mendix web app.

Uses Playwright to navigate the Mendix web app and intercept the XAS response
containing all container locations with live fill rates. The data is returned
by the map widget after navigating: address -> Informatie -> Ondergrondse containers.

Requirements:
    pip install playwright
    playwright install chromium
"""

import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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
        print("  Loading page...")
        page.goto(f"{BASE_URL}/p/assen/landing/", wait_until="networkidle")
        page.wait_for_timeout(2000)

        print("  Filling address...")
        page.locator("input[placeholder='uw postcode']").first.fill(ZIPCODE)
        page.locator("input[placeholder='uw huisnummer']").first.fill(HOUSENUMBER)
        page.wait_for_timeout(500)

        print("  Clicking Volgende...")
        page.locator("button:has-text('Volgende')").first.click()
        page.wait_for_timeout(6000)

        print("  Selecting address...")
        for item in page.locator(".mx-templategrid-item, .mx-listview-item").all():
            if ADDRESS_MATCH in item.inner_text():
                item.click()
                break
        page.wait_for_timeout(5000)

        print("  Selecting Informatie...")
        for item in page.locator(".mx-templategrid-item, .mx-listview-item").all():
            if "Informatie" in item.inner_text():
                item.click()
                break
        page.wait_for_timeout(5000)

        print("  Selecting Ondergrondse containers...")
        for item in page.locator(".mx-templategrid-item, .mx-listview-item").all():
            if "Ondergrondse" in item.inner_text():
                item.click()
                break

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
            vulgraad = attrs.get("Vulgraad", {}).get("value", 0)
            if isinstance(vulgraad, str):
                vulgraad = int(vulgraad) if vulgraad.isdigit() else 0
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


def main():
    output_dir = Path(__file__).parent.parent / "data"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "containers_fillrates.json"
    state_file = output_dir / "state.json"
    events_file = output_dir / "events.ndjson"
    history_file = output_dir / "history.json"

    ams_tz = ZoneInfo("Europe/Amsterdam")
    now_ams = datetime.now(ams_tz)

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

        # Save full data
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(locaties, f, indent=2, ensure_ascii=False)

        # Build current state {nr: vulgraad}
        current_state = {l["nr"]: l["vulgraad"] for l in locaties if l["nr"]}

        # Load previous state and diff
        prev_state = {}
        if state_file.exists():
            with open(state_file) as f:
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
        with open(state_file, "w") as f:
            json.dump(current_state, f, separators=(",", ":"))

        # Update rolling history (append snapshot, keep last 30 days = 180 points at 4h)
        snapshot = {"ts": now_ams.isoformat(), "containers": current_state}
        hist = {"samples": [], "history": []}
        if history_file.exists():
            with open(history_file) as f:
                hist = json.load(f)
        hist["history"].append(snapshot)
        # Keep max 180 snapshots (30 days at 4h intervals)
        hist["history"] = hist["history"][-180:]
        # Update samples (first 8 containers with sensor + data)
        if not hist["samples"]:
            samples = [l for l in locaties if l["heeftSensor"] and l["vulgraad"] > 0][:8]
            hist["samples"] = [{"nr": s["nr"], "adres": s["adres"], "fractie": s["fractie"]} for s in samples]
        with open(history_file, "w") as f:
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
        with open(meta_file, "w") as f:
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
    main()
