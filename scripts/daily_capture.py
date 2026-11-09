"""Stage, validate, and check daily snapshots without deployment dependencies."""

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import urlopen
from zoneinfo import ZoneInfo

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SNAPSHOT_FILES = (
    "containers_fillrates.json", "state.json", "events.ndjson",
    "history.json", "meta.json",
)


def read_json(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def collection_time(meta):
    if not isinstance(meta, dict) or not isinstance(meta.get("updated"), str):
        raise ValueError("Missing collection timestamp")
    timestamp = datetime.fromisoformat(meta["updated"])
    if timestamp.utcoffset() is None:
        raise ValueError("Collection timestamp must include a timezone")
    if timestamp > datetime.now(AMSTERDAM) + timedelta(minutes=5):
        raise ValueError("Collection timestamp is in the future")
    return timestamp.astimezone(AMSTERDAM)


def validate_containers(rows, previous_count=0):
    minimum = max(1, math.ceil(previous_count * 0.9))
    if not isinstance(rows, list) or len(rows) < minimum:
        raise ValueError(f"Expected at least {minimum} containers (90% of previous count)")
    numbered = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Container must be an object")
        fill = row.get("vulgraad")
        if type(fill) not in (int, float) or not math.isfinite(fill) or not 0 <= fill <= 100:
            raise ValueError(f"Invalid fill rate for container {row.get('nr')}: {fill!r}")
        if not isinstance(row.get("heeftSensor"), bool):
            raise ValueError("Container sensor flag must be boolean")
        if not isinstance(row.get("fractie"), str) or not row["fractie"]:
            raise ValueError("Container fraction is missing")
        for key, limit in (("lat", 90), ("lng", 180)):
            value = row.get(key)
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError(f"Invalid container {key}: {value!r}")
            coordinate = float(value)
            if not math.isfinite(coordinate) or not -limit <= coordinate <= limit:
                raise ValueError(f"Invalid container {key}: {value!r}")
        nr = row.get("nr")
        if nr is not None:
            if not isinstance(nr, str) or not nr or nr in numbered:
                raise ValueError(f"Invalid or duplicate container number: {nr!r}")
            numbered.add(nr)
    if not numbered:
        raise ValueError("Snapshot has no numbered containers")


def validate_snapshot(directory, previous_count=0):
    for name in SNAPSHOT_FILES:
        if not (directory / name).is_file():
            raise ValueError(f"Snapshot file is missing: {name}")
    rows = read_json(directory / "containers_fillrates.json")
    validate_containers(rows, previous_count)
    meta = read_json(directory / "meta.json")
    timestamp = collection_time(meta)
    if meta.get("source") != "burgerportaal-mendix" or meta.get("timezone") != "Europe/Amsterdam":
        raise ValueError("Unexpected snapshot source or timezone")
    if meta.get("containers") != len(rows):
        raise ValueError("Metadata container count does not match snapshot")
    if meta.get("with_sensor") != sum(row["heeftSensor"] for row in rows):
        raise ValueError("Metadata sensor count does not match snapshot")
    average = round(sum(row["vulgraad"] for row in rows) / len(rows), 1)
    if meta.get("avg_vulgraad") != average:
        raise ValueError("Metadata average does not match snapshot")
    state = {row["nr"]: row["vulgraad"] for row in rows if row["nr"]}
    if read_json(directory / "state.json") != state:
        raise ValueError("State does not match snapshot")
    history = read_json(directory / "history.json")
    if not isinstance(history, dict) or not isinstance(history.get("history"), list):
        raise ValueError("Snapshot history must be a list")
    entries = history["history"]
    if not 1 <= len(entries) <= 30:
        raise ValueError("Snapshot history must contain 1 to 30 days")
    if any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("Snapshot history entries must be objects")
    days = [collection_time({"updated": entry.get("ts")}).date() for entry in entries]
    if days != sorted(set(days)):
        raise ValueError("Snapshot history must have unique, ordered Amsterdam dates")
    if entries[-1].get("ts") != meta["updated"] or entries[-1].get("containers") != state:
        raise ValueError("Latest history entry does not match snapshot")
    with (directory / "events.ndjson").open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            event = json.loads(line)
            if not isinstance(event, dict) or not isinstance(event.get("nr"), str) or not event["nr"]:
                raise ValueError(f"Invalid event at line {line_number}")
            collection_time({"updated": event.get("ts")})
            for key in ("from", "to"):
                fill = event.get(key)
                if type(fill) not in (int, float) or not math.isfinite(fill) or not 0 <= fill <= 100:
                    raise ValueError(f"Invalid event fill rate at line {line_number}")
    return timestamp


def capture_needed(directory, force=False, now=None):
    if force:
        return True
    try:
        timestamp = validate_snapshot(directory)
    except (ValueError, OSError) as error:
        print(f"Existing snapshot is invalid; capture required: {error}", file=sys.stderr)
        return True
    now = now or datetime.now(AMSTERDAM)
    return timestamp.date() != now.astimezone(AMSTERDAM).date()


def capture(directory, output_dir, attempts=3, retry_delay=60):
    if output_dir.exists():
        raise ValueError(f"Capture output already exists: {output_dir}")
    previous_rows = read_json(directory / "containers_fillrates.json")
    if not isinstance(previous_rows, list):
        raise ValueError("Previous container data must be a list")
    for attempt in range(1, attempts + 1):
        print(f"Capture attempt {attempt}/{attempts}", flush=True)
        with tempfile.TemporaryDirectory(prefix="afvalmonitor-") as temporary:
            staged = Path(temporary)
            for name in SNAPSHOT_FILES:
                if (directory / name).exists():
                    shutil.copy2(directory / name, staged / name)
            started = datetime.now(AMSTERDAM)
            try:
                subprocess.run(
                    [sys.executable, str(Path(__file__).with_name("fetch_fillrates.py")),
                     "--output-dir", str(staged)],
                    check=True, timeout=300,
                )
                timestamp = validate_snapshot(staged, len(previous_rows))
                if timestamp < started:
                    raise ValueError("Collector did not produce a new timestamp")
                if timestamp.date() != datetime.now(AMSTERDAM).date():
                    raise ValueError("Capture crossed midnight; retry for the current Amsterdam date")
            except (subprocess.SubprocessError, ValueError, OSError) as error:
                print(f"Capture attempt {attempt} failed: {error}", file=sys.stderr, flush=True)
                if attempt == attempts:
                    raise RuntimeError("All capture attempts failed; committed data is unchanged") from error
            else:
                shutil.copytree(staged, output_dir)
                print(f"Validated snapshot saved to {output_dir}: {timestamp.isoformat()}")
                return
        time.sleep(retry_delay)


def check_freshness(directory, deployed_url=None, now=None):
    now = now or datetime.now(AMSTERDAM)
    timestamp = validate_snapshot(directory)
    today = now.astimezone(AMSTERDAM).date()
    if timestamp.date() != today:
        raise ValueError(f"Missing capture for {today}; latest committed capture: {timestamp.isoformat()}")
    print(f"Committed snapshot is current: {timestamp.isoformat()}")
    if deployed_url:
        with urlopen(deployed_url.rstrip("/") + "/data/meta.json", timeout=30) as response:
            deployed = collection_time(json.load(response))
        if deployed.date() != today or deployed < timestamp:
            raise ValueError(f"Deployment is behind committed data: {deployed.isoformat()}")
        print(f"Deployed snapshot is current: {deployed.isoformat()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="Print needed=true/false for GitHub Actions")
    status.add_argument("--force", action="store_true")
    collect = commands.add_parser("capture", help="Capture to a separate directory with three attempts")
    collect.add_argument("--output-dir", type=Path, required=True)
    check = commands.add_parser("check", help="Fail if today's valid snapshot is missing")
    check.add_argument("--deployed-url")
    args = parser.parse_args()
    if args.command == "status":
        print(f"needed={str(capture_needed(args.data_dir, args.force)).lower()}")
    elif args.command == "capture":
        capture(args.data_dir, args.output_dir)
    else:
        check_freshness(args.data_dir, args.deployed_url)


if __name__ == "__main__":
    main()
