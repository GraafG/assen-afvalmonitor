# Burgerportaal API Documentation

Reverse-engineered from the Burgerportaal Assen Android app (v4.5.8, package: `nl.qubz21.burgerportaal.assen.prod`).

## Authentication

The API uses Firebase Anonymous Authentication. No user account is needed.

### Step 1: Anonymous Sign-up

```http
POST https://www.googleapis.com/identitytoolkit/v3/relyingparty/signupNewUser?key={API_KEY}
Content-Type: application/json

{}
```

Returns `idToken`, `refreshToken`, and `localId` (uid).

### Step 2: Token Refresh (when expired)

```http
POST https://securetoken.googleapis.com/v1/token?key={API_KEY}
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token&refresh_token={refreshToken}
```

Returns fresh `id_token`.

### Using the Token

Pass the `id_token` in the `authorization` header (note: **no** "Bearer" prefix):

```
authorization: {id_token}
```

## Configuration

| Key | Value |
|-----|-------|
| Firebase API Key | `AIzaSyA6NkRqJypTfP-cjWzrZNFJzPUbBaGjOdk` |
| Firebase Project | `burgerportaal-production` |
| App ID | `1:486593522447:android:ab8f8a4307cde2beafec7a` |
| Region | `europe-west3` |
| Base URL | `https://europe-west3-burgerportaal-production.cloudfunctions.net` |

## Organisation IDs

| Organisation ID | Name |
|----------------|------|
| `138204213565303512` | Gemeente Assen |
| `138204213564933497` | NV BAR-Afvalbeheer |
| `138204213565304094` | Gemeente Nijkerk |
| `138204213564933597` | RMN |
| `452048812597326549` | Gemeente Groningen |
| `452048812597339353` | BAT Tilburg |
| `452048812597339479` | Gemeente Utrecht |
| `452048812597352136` | Leiden |
| `452048812597352458` | Cyclus NV |
| `452048812597352613` | Gemeente Breda |
| `452048812597416245` | Gemeente Almere |

## Endpoints

### Address Lookup

Look up an address to get the `addressId` needed for other calls.

```http
GET /exposed/v2/organisations/{orgId}/address?zipcode={zipcode}&housenumber={number}
```

**Parameters:**
- `zipcode` - Dutch postcode (e.g., `9402NV`)
- `housenumber` - House number (e.g., `10`)

**Response:**
```json
[
  {
    "addressId": "206321158129255491",
    "addition": "",
    "zipcode": "9402NV",
    "street": "Vondellaan",
    "city": "Assen",
    "housenumber": 10,
    "municipalityId": "0106",
    "latitude": 53.00178516,
    "longitude": 6.56723097
  }
]
```

### Waste Collection Calendar

Get the upcoming waste collection schedule for an address.

```http
GET /exposed/v2/organisations/{orgId}/address/{addressId}/calendar
```

**Response:**
```json
[
  {
    "year": 2026,
    "month": 6,
    "day": 22,
    "collectionDate": "2026-06-22T00:00:00.000Z",
    "fraction": "PAPIER",
    "placementPeriod": "",
    "placementDescription": "",
    "uuid": "20632115812925549122-06-2026PAPIER",
    "municipalityId": "0106",
    "organisationId": "138204213565303512"
  }
]
```

**Fractions (waste types) for Assen:**
- `GFT` - Garden/food/organic waste (green bin)
- `PAPIER` - Paper/cardboard
- `PMD` - Plastic/metal/drink cartons

## Container Fill Levels (Not Publicly Accessible)

The app shows underground container fill levels on a map. This data flows through:

1. **Mendix OData API** (`https://21qubz.mendixcloud.com/odata/Locations` and `/odata/Containers`)
2. **Cloud Function sync** → writes to Firestore
3. **Firestore real-time stream** → `organisations/{orgId}/locations` collection

The Mendix API requires authentication:
- Username: `21burger-mobile`
- Password: stored in Firebase Secret Manager as `QUBZ_SAAS_PROD_API_PASSWORD`

The Firestore collection is accessible to read but currently empty (sync not active or data is ephemeral).

**Container document fields** (from binary analysis):
- `containerNumber` - Container ID
- `containerFillLevel` - Fill percentage (0-100%)
- `position.geohash` - Geohash for geo-queries
- `fraction` / `Fractie` - Waste type
- `containerType` - e.g., "verzamelcontainer" (underground collection container)

## Home Assistant Integration

The [homeassistant-afvalwijzer](https://github.com/xirixiz/homeassistant-afvalwijzer) integration already supports Burgerportaal using the same API endpoints documented above. It provides:
- Next collection date per fraction
- Days until next collection
- Collection schedule sensors

To use it, configure with provider `burgerportaal` and your postcode/house number.

## Example: Python Client

```python
import requests

API_KEY = "AIzaSyA6NkRqJypTfP-cjWzrZNFJzPUbBaGjOdk"
BASE = "https://europe-west3-burgerportaal-production.cloudfunctions.net"
ORG_ID = "138204213565303512"  # Gemeente Assen

# 1. Get anonymous auth token
signup = requests.post(
    f"https://www.googleapis.com/identitytoolkit/v3/relyingparty/signupNewUser?key={API_KEY}",
    json={}
).json()
token = signup["idToken"]

headers = {"authorization": token}

# 2. Look up address
resp = requests.get(
    f"{BASE}/exposed/v2/organisations/{ORG_ID}/address",
    params={"zipcode": "9402NV", "housenumber": "10"},
    headers=headers
)
address = resp.json()[0]
address_id = address["addressId"]

# 3. Get collection calendar
resp = requests.get(
    f"{BASE}/exposed/v2/organisations/{ORG_ID}/address/{address_id}/calendar",
    headers=headers
)
calendar = resp.json()

# Print upcoming collections
for item in calendar[:5]:
    print(f"{item['collectionDate'][:10]}: {item['fraction']}")
```

## App Architecture (from reverse engineering)

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│  Flutter App    │────▶│  Firebase Cloud       │────▶│  Mendix Cloud   │
│  (Dart/arm64)   │     │  Functions (/exposed) │     │  (21qubz)       │
└────────┬────────┘     └──────────────────────┘     └─────────────────┘
         │                        │
         │                        │ (sync)
         ▼                        ▼
┌─────────────────┐     ┌──────────────────────┐
│  Firebase Auth  │     │  Cloud Firestore     │
│  (anonymous)    │     │  (container data)    │
└─────────────────┘     └──────────────────────┘
```

**Key source files** (from package references in compiled binary):
- `repositories/portal/http_portal_repository.dart` - HTTP API calls
- `repositories/portal/portal_repository.dart` - Abstract repository interface
- `viewModel/container_locations_view_model.dart` - Container map logic
- `models/collection_container.dart` - Container data model
- `utils/firestore_utils.dart` - Firestore geo-query helpers
- `views/maps_view.dart` - Map with container markers
