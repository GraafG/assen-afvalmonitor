# Assen Afvalmonitor

Live underground container fill rate monitoring dashboard for Gemeente Assen, built by reverse-engineering the Burgerportaal app (by Qubz21).

**🌐 [Live Dashboard](https://graafg.github.io/assen-afvalmonitor/)**

## What it does

- Monitors **606 underground waste containers** across Assen
- Shows **live fill rates** (vulgraad %) updated every 4 hours
- Interactive map with clustering, filtering by fractie/sensor/fill level
- Dark/light mode auto-detection
- Change log with emptied-container detection

## Architecture

```
Burgerportaal Mendix Web App (21burgerportaal.mendixcloud.com)
        │
        ▼  Playwright browser automation
GitHub Actions (every 4 hours)
        │
        ▼  Commits data/containers_fillrates.json
GitHub Pages (static dashboard)
```

The Mendix XAS protocol requires client-side hash computation, so direct API calls don't work. Instead, we use **Playwright** to automate the web portal and intercept the XAS response containing all container data.

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

## API Documentation

See [docs/api.md](docs/api.md) for full endpoint and protocol documentation.

## GitHub Actions

The workflow runs every 4 hours (`0 */4 * * *`), fetches fresh data, commits to `main`, and deploys to GitHub Pages. All timestamps use `Europe/Amsterdam` timezone.

## Related Projects

- [homeassistant-afvalwijzer](https://github.com/xirixiz/homeassistant-afvalwijzer) - HA integration for Burgerportaal calendar
- [assen-meldingen](https://github.com/GraafG/assen-meldingen) - Similar dashboard for Assen public complaints