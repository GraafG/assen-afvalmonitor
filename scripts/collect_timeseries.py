"""
Collect container fill rate snapshots at regular intervals to determine
how often the source data actually changes.

Stores results as NDJSON (one JSON line per snapshot) in data/timeseries.ndjson.
Each line: {"ts": "ISO8601", "containers": {nr: vulgraad, ...}}

Usage:
    python scripts/collect_timeseries.py [--interval 60] [--duration 3600]

Defaults: every 60 seconds for 1 hour.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Error: playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)

BASE_URL = "https://21burgerportaal.mendixcloud.com"
ZIPCODE = "9402JK"
HOUSENUMBER = "4"
ADDRESS_MATCH = "4J"

OUTPUT_FILE = Path(__file__).parent.parent / "data" / "timeseries.ndjson"


def setup_session():
    """Launch browser and navigate to the container map page. Returns (pw, browser, page)."""
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = context.new_page()

    print("Setting up session - navigating to container map...")
    page.goto(f"{BASE_URL}/p/assen/landing/", wait_until="networkidle")
    page.wait_for_timeout(2000)

    page.locator("input[placeholder='uw postcode']").first.fill(ZIPCODE)
    page.locator("input[placeholder='uw huisnummer']").first.fill(HOUSENUMBER)
    page.wait_for_timeout(500)

    page.locator("button:has-text('Volgende')").first.click()
    page.wait_for_timeout(6000)

    for item in page.locator(".mx-templategrid-item, .mx-listview-item").all():
        if ADDRESS_MATCH in item.inner_text():
            item.click()
            break
    page.wait_for_timeout(5000)

    for item in page.locator(".mx-templategrid-item, .mx-listview-item").all():
        if "Informatie" in item.inner_text():
            item.click()
            break
    page.wait_for_timeout(5000)

    for item in page.locator(".mx-templategrid-item, .mx-listview-item").all():
        if "Ondergrondse" in item.inner_text():
            item.click()
            break
    page.wait_for_timeout(10000)

    print("Session ready - on container map page.")
    return pw, browser, page


def capture_snapshot(page):
    """Reload the map and capture fill rate data. Returns {nr: vulgraad} dict."""
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

    # Trigger a refresh by navigating back and forward to the container template
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(3000)

    # Re-navigate to underground containers
    for item in page.locator(".mx-templategrid-item, .mx-listview-item").all():
        if "Ondergrondse" in item.inner_text():
            item.click()
            break

    # Wait for data
    for _ in range(24):
        if container_data:
            break
        page.wait_for_timeout(5000)

    page.remove_listener("response", on_response)

    if not container_data:
        return None

    # Parse into {nr: vulgraad}
    snapshot = {}
    for obj in container_data[0].get("objects", []):
        if obj.get("objectType") == "Burger_Applicatie.Locatie":
            attrs = obj.get("attributes", {})
            nr = attrs.get("ContainerNummer", {}).get("value")
            vulgraad = attrs.get("Vulgraad", {}).get("value", 0)
            if isinstance(vulgraad, str):
                vulgraad = int(vulgraad) if vulgraad.isdigit() else 0
            elif vulgraad is None:
                vulgraad = 0
            if nr:
                snapshot[nr] = vulgraad

    return snapshot


def main():
    parser = argparse.ArgumentParser(description="Collect fill rate time series")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between captures (default: 60)")
    parser.add_argument("--duration", type=int, default=3600, help="Total duration in seconds (default: 3600 = 1 hour)")
    args = parser.parse_args()

    OUTPUT_FILE.parent.mkdir(exist_ok=True)

    iterations = args.duration // args.interval
    print(f"Will collect {iterations} snapshots over {args.duration}s (every {args.interval}s)")
    print(f"Output: {OUTPUT_FILE}\n")

    pw, browser, page = setup_session()

    # First snapshot uses the already-loaded data
    prev_snapshot = None
    changes_log = []

    AMS_TZ = ZoneInfo("Europe/Amsterdam")

    try:
        for i in range(iterations):
            start = time.time()
            ts = datetime.now(AMS_TZ).isoformat()

            print(f"[{i+1}/{iterations}] Capturing at {ts}...", end=" ")
            snapshot = capture_snapshot(page)

            if snapshot is None:
                print("FAILED (no data)")
                continue

            # Write NDJSON line
            record = {"ts": ts, "count": len(snapshot), "containers": snapshot}
            with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, separators=(",", ":")) + "\n")

            # Compare with previous
            if prev_snapshot:
                changed = {nr: (prev_snapshot.get(nr), v) for nr, v in snapshot.items()
                           if prev_snapshot.get(nr) != v}
                if changed:
                    print(f"OK ({len(snapshot)} containers, {len(changed)} CHANGED)")
                    for nr, (old, new) in list(changed.items())[:5]:
                        print(f"       {nr}: {old}% -> {new}%")
                    changes_log.append({"ts": ts, "changes": len(changed), "details": changed})
                else:
                    print(f"OK ({len(snapshot)} containers, no changes)")
            else:
                print(f"OK ({len(snapshot)} containers, baseline)")

            prev_snapshot = snapshot

            # Wait for next interval
            elapsed = time.time() - start
            wait = max(0, args.interval - elapsed)
            if wait > 0 and i < iterations - 1:
                time.sleep(wait)

    except KeyboardInterrupt:
        print("\n\nStopped by user.")
    finally:
        browser.close()
        pw.stop()

    # Summary
    print(f"\n{'='*50}")
    print(f"Collection complete. {len(changes_log)} intervals had changes.")
    if changes_log:
        print("\nChange events:")
        for evt in changes_log:
            print(f"  {evt['ts']}: {evt['changes']} containers changed")

        # Save change summary
        summary_file = OUTPUT_FILE.parent / "timeseries_changes.json"
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(changes_log, f, indent=2, ensure_ascii=False)
        print(f"\nChange log saved to {summary_file}")
    else:
        print("No fill rate changes detected during collection period.")


if __name__ == "__main__":
    main()
