import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import daily_capture as daily
import fetch_fillrates


def containers(count=10):
    return [
        {"nr": f"C{index}", "lat": "53.0", "lng": "6.5", "vulgraad": 20,
         "heeftSensor": True, "fractie": "Papier"}
        for index in range(count)
    ]


def write_snapshot(directory, timestamp, rows=None):
    directory.mkdir(exist_ok=True)
    rows = containers() if rows is None else rows
    state = {row["nr"]: row["vulgraad"] for row in rows if row["nr"]}
    documents = {
        "containers_fillrates.json": rows,
        "state.json": state,
        "meta.json": {
            "updated": timestamp.isoformat(), "containers": len(rows),
            "with_sensor": sum(row["heeftSensor"] for row in rows),
            "avg_vulgraad": round(sum(row["vulgraad"] for row in rows) / len(rows), 1),
            "source": "burgerportaal-mendix", "timezone": "Europe/Amsterdam",
        },
        "history.json": {
            "samples": [],
            "history": [{"ts": timestamp.isoformat(), "containers": state}],
        },
    }
    for name, value in documents.items():
        (directory / name).write_text(json.dumps(value), encoding="utf-8")
    (directory / "events.ndjson").write_text("", encoding="utf-8")


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "data"
        self.now = datetime.now(daily.AMSTERDAM)
        write_snapshot(self.directory, self.now)

    def test_valid_snapshot_and_skip_today(self):
        self.assertEqual(daily.validate_snapshot(self.directory), self.now)
        self.assertFalse(daily.capture_needed(self.directory, now=self.now))
        self.assertTrue(daily.capture_needed(self.directory, force=True, now=self.now))
        self.assertTrue(daily.capture_needed(self.directory, now=self.now + timedelta(days=1)))

    def test_amsterdam_date_not_utc_date(self):
        for month in (1, 7):
            with self.subTest(month=month):
                timestamp = datetime(self.now.year - 1, month, 1, 23, 30, tzinfo=timezone.utc)
                write_snapshot(self.directory, timestamp)
                local_now = timestamp.astimezone(daily.AMSTERDAM) + timedelta(minutes=10)
                self.assertNotEqual(timestamp.date(), local_now.date())
                self.assertFalse(daily.capture_needed(self.directory, now=local_now))

    def test_missing_or_invalid_files_require_capture(self):
        for name in daily.SNAPSHOT_FILES:
            with self.subTest(name=name):
                original = (self.directory / name).read_bytes()
                (self.directory / name).unlink()
                self.assertTrue(daily.capture_needed(self.directory))
                (self.directory / name).write_bytes(original)
        (self.directory / "meta.json").write_text("invalid", encoding="utf-8")
        self.assertTrue(daily.capture_needed(self.directory))

    def test_partial_count_threshold(self):
        daily.validate_containers(containers(9), previous_count=10)
        with self.assertRaises(ValueError):
            daily.validate_containers(containers(8), previous_count=10)
        with self.assertRaises(ValueError):
            daily.validate_containers([])
        daily.validate_containers(containers(549), previous_count=609)
        with self.assertRaises(ValueError):
            daily.validate_containers(containers(548), previous_count=609)

    def test_malformed_containers_rejected(self):
        for key, value in (
            ("vulgraad", -1), ("vulgraad", 101), ("vulgraad", True),
            ("vulgraad", float("nan")), ("vulgraad", "20"),
            ("lat", None), ("lat", 91), ("lng", float("inf")),
            ("heeftSensor", None), ("fractie", ""),
        ):
            with self.subTest(key=key, value=value):
                rows = containers()
                rows[0][key] = value
                with self.assertRaises(ValueError):
                    daily.validate_containers(rows)
        rows = containers()
        rows[1]["nr"] = rows[0]["nr"]
        with self.assertRaises(ValueError):
            daily.validate_containers(rows)

    def test_unnumbered_locations_are_preserved(self):
        rows = containers()
        rows.append(dict(rows[0], nr=None, lat="0", lng="0"))
        write_snapshot(self.directory, self.now, rows)
        daily.validate_snapshot(self.directory)

    def test_inconsistent_bundle_rejected(self):
        for name, value in (
            ("state.json", {}),
            ("events.ndjson", []),
            ("history.json", {"history": []}),
            ("history.json", {"history": [
                {"ts": self.now.isoformat(), "containers": {}},
                {"ts": self.now.isoformat(), "containers": {}},
            ]}),
            ("meta.json", {"updated": self.now.isoformat(), "containers": 999}),
        ):
            with self.subTest(name=name, value=value):
                write_snapshot(self.directory, self.now)
                (self.directory / name).write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    daily.validate_snapshot(self.directory)

    def test_invalid_timestamps_rejected(self):
        for timestamp in (
            "invalid", "2026-11-09T12:00:00",
            (self.now + timedelta(days=1)).isoformat(),
        ):
            with self.subTest(timestamp=timestamp):
                with self.assertRaises(ValueError):
                    daily.collection_time({"updated": timestamp})

    def test_check_reports_missing_day(self):
        with self.assertRaisesRegex(ValueError, "Missing capture"):
            daily.check_freshness(self.directory, now=self.now + timedelta(days=1))

    def test_check_reports_stale_deployment(self):
        deployed = {"updated": (self.now - timedelta(days=1)).isoformat()}
        with patch.object(daily, "urlopen", return_value=io.BytesIO(json.dumps(deployed).encode())):
            with self.assertRaisesRegex(ValueError, "Deployment is behind"):
                daily.check_freshness(self.directory, "https://example.test", now=self.now)

    def test_check_current_deployment(self):
        meta = (self.directory / "meta.json").read_bytes()
        with patch.object(daily, "urlopen", return_value=io.BytesIO(meta)):
            daily.check_freshness(self.directory, "https://example.test", now=self.now)

    def test_failed_attempts_preserve_committed_files(self):
        before = {name: (self.directory / name).read_bytes() for name in daily.SNAPSHOT_FILES}
        output = self.directory.parent / "captured"

        def fail(command, **kwargs):
            staged = Path(command[-1])
            (staged / "state.json").write_text("partial write", encoding="utf-8")
            raise subprocess.CalledProcessError(1, command)

        with patch.object(daily.subprocess, "run", side_effect=fail) as run:
            with patch.object(daily.time, "sleep") as sleep:
                with self.assertRaisesRegex(RuntimeError, "All capture attempts failed"):
                    daily.capture(self.directory, output)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertFalse(output.exists())
        self.assertEqual(before, {name: (self.directory / name).read_bytes() for name in daily.SNAPSHOT_FILES})

    def test_retry_uses_clean_baseline_and_saves_valid_bundle(self):
        output = self.directory.parent / "captured"
        original_state = (self.directory / "state.json").read_bytes()
        calls = 0

        def collect(command, **kwargs):
            nonlocal calls
            calls += 1
            staged = Path(command[-1])
            self.assertEqual((staged / "state.json").read_bytes(), original_state)
            if calls == 1:
                (staged / "state.json").write_text("partial write", encoding="utf-8")
                raise subprocess.TimeoutExpired(command, 300)
            write_snapshot(staged, datetime.now(daily.AMSTERDAM))

        with patch.object(daily.subprocess, "run", side_effect=collect):
            with patch.object(daily.time, "sleep"):
                daily.capture(self.directory, output)
        self.assertEqual(calls, 2)
        daily.validate_snapshot(output)
        self.assertEqual((self.directory / "state.json").read_bytes(), original_state)

    def test_successful_process_with_old_or_partial_data_is_not_success(self):
        output = self.directory.parent / "captured"
        with patch.object(daily.subprocess, "run"):
            with self.assertRaises(RuntimeError):
                daily.capture(self.directory, output, attempts=1)
        self.assertFalse(output.exists())

    def test_raw_fill_values_are_not_silently_zeroed(self):
        response = {"objects": [{"objectType": "Burger_Applicatie.Locatie",
                                 "attributes": {"Vulgraad": {"value": "broken"}}}]}
        with self.assertRaises(ValueError):
            fetch_fillrates.parse_containers(response)
        missing = copy.deepcopy(response)
        missing["objects"][0]["attributes"] = {}
        with self.assertRaises(ValueError):
            fetch_fillrates.parse_containers(missing)
        missing["objects"][0]["attributes"] = {"Vulgraad": {}}
        with self.assertRaises(ValueError):
            fetch_fillrates.parse_containers(missing)
        response["objects"][0]["attributes"]["Vulgraad"]["value"] = None
        self.assertEqual(fetch_fillrates.parse_containers(response)[0]["vulgraad"], 0)

    def test_invalid_fetch_does_not_replace_baseline(self):
        before = {name: (self.directory / name).read_bytes() for name in daily.SNAPSHOT_FILES}
        with patch.object(fetch_fillrates, "fetch_containers", return_value={"objects": []}):
            with patch.object(fetch_fillrates, "parse_containers", return_value=containers(8)):
                with self.assertRaises(SystemExit):
                    fetch_fillrates.main(self.directory)
        self.assertEqual(before, {name: (self.directory / name).read_bytes() for name in daily.SNAPSHOT_FILES})


if __name__ == "__main__":
    unittest.main()
