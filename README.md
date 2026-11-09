# Assen Afvalmonitor

Live underground container fill rate monitoring dashboard for Gemeente Assen, built by reverse-engineering the Burgerportaal app (by Qubz21).

**🌐 [Live Dashboard](https://graafg.github.io/assen-afvalmonitor/)**

## What it does

- Monitors **606 underground waste containers** across Assen
- Shows **live fill rates** (vulgraad %) updated daily
- Interactive map with clustering, filtering by fractie/sensor/fill level
- Dark/light mode auto-detection
- Change log with emptied-container detection

## Architecture

```
Burgerportaal Mendix Web App (21burgerportaal.mendixcloud.com)
        │
        ▼  Playwright browser automation
Capture data workflow (daily, with recovery attempts)
        │
        ▼  Validate snapshot, save artifact, commit data/
Deploy dashboard workflow (no scraping)
        │
        ▼  Publish the latest committed data and dashboard
GitHub Pages (static dashboard)
```

The Mendix XAS protocol requires client-side hash computation, so direct API calls don't work. Instead, we use **Playwright** to automate the web portal and intercept the XAS response containing all container data.

### Map tiles

The map uses key-free [OpenStreetMap raster tiles](https://operations.osmfoundation.org/policies/tiles/) with Leaflet. CARTO started displaying an API-key-required watermark; [Home Assistant solved the same issue by switching to OpenStreetMap vector tiles](https://github.com/home-assistant/frontend/pull/53816). This dashboard uses OSM's raster endpoint to keep its existing Leaflet setup without adding MapLibre, WebGL requirements, or bundled vector styles/fonts. Dark mode applies a filter only to the base tiles, leaving container colours unchanged.

OSM attribution stays visible, and the browser sends its normal referrer and honours tile caching headers. There is no tile prefetching or offline download.

### Data freshness

Both pages display the actual collection timestamp from `data/meta.json` in Amsterdam time, with a warning when it is at least 48 hours old. This is the collector's timestamp, not the time the page was opened or the sensor's own measurement time. Missing metadata and location-only fallback data are explicitly labelled instead of being presented as current fill readings.

Dashboard data requests revalidate the HTTP cache on page load; map tiles retain normal caching. Reload an already-open page to retrieve the latest daily snapshot. The statistics timeline also names its latest snapshot, since chart axes may skip date labels.

## Available Data

| Data | Status | Method |
|------|--------|--------|
| Container fill levels (606 containers) | ✅ Working | Playwright + Mendix XAS |
| Container locations + sensor status | ✅ Working | Playwright + Mendix XAS |
| Collection schedule (GFT, PAPIER, PMD) | ✅ Working | HTTP API |
| Address lookup | ✅ Working | HTTP API |
| Public holidays + weekends | ✅ Working | Static `data/holidays.json` |

The stats timeline shades **weekends** (Saturday/Sunday, no working days) and **Dutch public holidays** (officiële feestdagen voor het Rijk / ambtenaren) as background bands. Holidays for 2026–2027 live in `data/holidays.json`; weekends are detected client-side. Extend the JSON with future years as needed.

The stats page also shows **emptied containers per weekday**. A container counts as *emptied* when its fill rate drops **≥ 30 percentage points** between two consecutive readings **and** ends **≤ 20%** (filters sensor noise / partial settling). Sensors report a collection roughly **one day after** it actually happens, so the weekday attribution is shifted back by `EMPTIED_REPORT_LAG_DAYS` (default 1) to land on the real collection day. This weekday breakdown plus the holiday/weekend shading are the foundation for future fill-rate prediction, where holidays and non-working days are expected to be a key factor.

## Container Data Fields

Each container provides: `ContainerNummer`, `Latitude`, `Longitude`, `Vulgraad` (fill %), `AdresVolledig`, `FractieKleur`, `HeeftSensor`.

**Fracties:** Restafval (grey), GFT (green), Papier (blue), PMD (orange), Glas (skyblue)

> **Note on GFT/organic containers:** The source marks `HeeftSensor: true` for all GFT ("BIO") containers, but in practice they have no working fill sensor and always report `0%`. The dashboard treats organic containers (fractie `GFT` / `Groente, Fruit en Tuinafval`) as **"no fill data"** rather than 0% — they're hidden from fill rates, averages and statistics. This is detected by fractie, so newly added organic containers are handled automatically.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/fetch_fillrates.py` | Fetch current fill rates; supports `--output-dir` |
| `scripts/daily_capture.py` | Stage and validate captures, retry failures, check daily freshness |
| `scripts/collect_timeseries.py` | Monitor changes over time (local use) |
| `scripts/fetch_calendar.py` | Fetch collection schedules |

### Running locally

```bash
pip install playwright
playwright install chromium

# Single fetch
python scripts/fetch_fillrates.py

# Check whether today's valid Amsterdam snapshot exists
python scripts/daily_capture.py status

# Capture into a NEW directory without changing committed data/
python scripts/daily_capture.py capture --output-dir captured-data

# Check committed data and the live deployment
python scripts/daily_capture.py check --deployed-url https://graafg.github.io/assen-afvalmonitor/

# Monitor changes (every 60s for 1 hour)
python scripts/collect_timeseries.py --interval 60 --duration 3600
```

## Documentation

- [API Documentation](docs/api.md) - Full endpoint and protocol documentation
- [Home Assistant Integration](docs/homeassistant.md) - Monitor container fill levels in HA

## GitHub Actions

Capture and deployment use separate workflows. A deployment never contacts Burgerportaal or changes captured data.

| Workflow | Triggers | Responsibility |
|----------|----------|----------------|
| **Capture data** (`capture.yml`) | Daily at 03:00, 06:00, and 09:00 UTC; manual run | Capture, validate, back up, and commit data to `main` |
| **Deploy dashboard** (`deploy.yml`) | Dashboard/data changes on `main`; successful capture workflow; manual run | Publish the latest committed dashboard and data to GitHub Pages |
| **Check daily data freshness** (`freshness.yml`) | Daily at 12:17 UTC; manual run | Require today's valid committed snapshot and a deployment that includes it |

UTC times are one hour earlier than Amsterdam time in winter and two hours earlier in summer. The first capture is therefore at 04:00 in winter or 05:00 in summer. The freshness check is at 13:17 or 14:17. GitHub can delay scheduled runs.

### Capture safeguards

- Scheduled and ordinary manual runs skip collection when today's complete, valid snapshot already exists. The date uses `Europe/Amsterdam`, not UTC. Skipped runs do not install Playwright or launch a browser. Manual runs can select **force** to replace today's snapshot.
- Each capture gets three attempts, with 60 seconds between failures. Each attempt has a five-minute collector timeout and starts from a separate copy of the committed baseline. Invalid or partial writes cannot change `data/`.
- Validation rejects empty data, invalid fields, duplicate container numbers, fill rates outside 0–100%, and inventories below 90% of the previous container count. It also checks metadata, state, and the latest history entry for consistency. Known unnumbered source locations remain supported.
- Missing or malformed source fill fields fail collection. The source's explicit `null` fill value still means zero, as before. Collection timestamps record when data was received, not when the workflow started.
- Only a complete, validated snapshot can reach the commit step. It contains `containers_fillrates.json`, `state.json`, `events.ndjson`, `history.json`, and `meta.json`. History keeps one snapshot per Amsterdam date, for up to 30 days.
- Before committing, capture saves those five files in a `container-snapshot-<run-id>-<attempt>` artifact for 30 days. A failed Git push leaves that backup available. Push retries rebase without automatic conflict resolution; conflicts fail explicitly.
- Capture runs are serialized and never canceled by a later capture. Deployment uses a separate concurrency group and permissions.

The inventory threshold detects incomplete responses; it cannot prove the source is correct. If Assen legitimately removes more than 10% of its containers, verify the source and review the baseline before accepting the change.

### Deployment and failure behavior

Deployment checks the dashboard and publishes `main` without Python, Playwright, or a fresh scrape. It always checks out the latest `main`, not the capture run's starting commit. This includes the data commit created during capture.

GitHub normally suppresses push-triggered workflows for commits made with `GITHUB_TOKEN`. The explicit `workflow_run` trigger therefore starts deployment after a successful **Capture data** run. A successful skip also redeploys the saved snapshot. Failed captures do not trigger deployment.

| Failure | Result and recovery |
|---------|---------------------|
| Portal unavailable or changed | Capture fails after its retries. Committed data and the live site remain unchanged. The later capture windows can recover the same day. |
| Git push fails | The validated artifact remains available. Capture fails, so automatic deployment does not run. |
| Pages deployment fails | The data commit and capture artifact remain available. Rerun deployment without another scrape. |
| No valid daily capture, invalid saved data, or deployment behind the saved snapshot | The independent freshness workflow fails and records the problem in its log and summary. |

Failed runs use GitHub's normal Actions notifications. Enable those notifications for this repository if alerts are required. No external notification service is configured. The dashboard still displays the actual collection time and its existing 48-hour stale warning; deployment never resets that timestamp.

### Manual recovery

1. For a capture failure, inspect **Capture data** in Actions. Fix the source/navigation problem, then run it on `main`. Leave **force** off unless today's saved snapshot must be replaced.
2. For a deployment failure, run **Deploy dashboard** on `main`. This does not capture data.
3. If capture succeeded but its push failed, download its validated snapshot artifact. Compare timestamps with the latest `main` first. Do not overwrite a newer snapshot. Restore all five files together, preserve their timestamps, run the freshness check, and commit them to `main`. A normal data push triggers deployment.
4. Run **Check daily data freshness** after recovery to check both the saved data and the published timestamp.

Artifacts expire after 30 days. A missed historical reading cannot be reconstructed by assigning an old date to a new capture.

### Regression checks

Run `node --test` for dashboard loading and freshness behavior.

Run `python -m unittest discover -s tests -p "test_*.py"` after installing Playwright and Chromium. These tests cover navigation, validation, Amsterdam dates, retries, unchanged baselines, and deployment freshness. CI runs them before a capture. Both collectors select visible buttons by their accessible names, not responsive grid wrappers with no clickable box.

## Related Projects

- [homeassistant-afvalwijzer](https://github.com/xirixiz/homeassistant-afvalwijzer) - HA integration for Burgerportaal calendar
- [assen-meldingen](https://github.com/GraafG/assen-meldingen) - Similar dashboard for Assen public complaints