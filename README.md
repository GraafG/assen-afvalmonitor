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
GitHub Actions (daily)
        │
        ▼  Commits data/containers_fillrates.json
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
| `scripts/fetch_fillrates.py` | Fetch current fill rates (runs in CI) |
| `scripts/collect_timeseries.py` | Monitor changes over time (local use) |
| `scripts/fetch_calendar.py` | Fetch collection schedules |

### Running locally

```bash
pip install playwright
playwright install chromium

# Single fetch
python scripts/fetch_fillrates.py

# Monitor changes (every 60s for 1 hour)
python scripts/collect_timeseries.py --interval 60 --duration 3600
```

## Documentation

- [API Documentation](docs/api.md) - Full endpoint and protocol documentation
- [Home Assistant Integration](docs/homeassistant.md) - Monitor container fill levels in HA

## GitHub Actions

The workflow is scheduled daily at 03:00 UTC (`0 3 * * *`), which is 05:00 Amsterdam time in summer and 04:00 in winter. GitHub Actions may start scheduled runs later. It fetches fresh data, commits to `main`, and deploys to GitHub Pages. All collection timestamps use the `Europe/Amsterdam` timezone.

The dashboard data-loading and freshness regression tests use Node's built-in test runner: `node --test`.

## Related Projects

- [homeassistant-afvalwijzer](https://github.com/xirixiz/homeassistant-afvalwijzer) - HA integration for Burgerportaal calendar
- [assen-meldingen](https://github.com/GraafG/assen-meldingen) - Similar dashboard for Assen public complaints