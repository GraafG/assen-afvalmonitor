# Assen Afvalmonitor

Waste collection monitoring dashboard for Gemeente Assen, built by reverse-engineering the Burgerportaal app (by Qubz21).

## Architecture

The Burgerportaal app uses:
- **Firebase Anonymous Auth** for API access (no user account needed)
- **Cloud Functions** (`/exposed` and `/exposed/v2`) proxying a Mendix backend
- **Firestore** for real-time container fill level data (geo-queried)
- **Mendix OData** as the ultimate data source (`21qubz.mendixcloud.com`)

## API Documentation

See [docs/api.md](docs/api.md) for full endpoint documentation.

## Available Data

| Data | Status | Method |
|------|--------|--------|
| Collection schedule (GFT, PAPIER, PMD) | ✅ Working | HTTP API |
| Address lookup | ✅ Working | HTTP API |
| Container fill levels | ❌ Not accessible | Mendix credentials required |
| Waste guide/fractions | ❌ Not found | Endpoint not exposed |

## Related Projects

- [homeassistant-afvalwijzer](https://github.com/xirixiz/homeassistant-afvalwijzer) - HA integration that already supports Burgerportaal calendar
- [assen-meldingen](https://github.com/GraafG/assen-meldingen) - Similar dashboard for Assen public complaints
- [tripper-deals](https://graafg.github.io/tripper-deals/) - UI template reference