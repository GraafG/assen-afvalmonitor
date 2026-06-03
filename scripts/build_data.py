"""
Build script: fetches latest data from ArcGIS and Burgerportaal APIs,
outputs to data/ for the dashboard.
"""

import json
from datetime import datetime
from pathlib import Path

import requests

API_KEY = "AIzaSyA6NkRqJypTfP-cjWzrZNFJzPUbBaGjOdk"
BASE_URL = "https://europe-west3-burgerportaal-production.cloudfunctions.net"
ORG_ID = "138204213565303512"
ARCGIS_URL = "https://services1.arcgis.com/p5QhXC0i0sZjprM1/arcgis/rest/services/kaart_Ondergrondse_containers_Assen_weergave_NV/FeatureServer/0/query"

OUTPUT_DIR = Path(__file__).parent.parent / "data"


def fetch_containers():
    """Fetch container locations from ArcGIS."""
    print("📦 Fetching container locations from ArcGIS...")
    resp = requests.get(
        ARCGIS_URL,
        params={
            "where": "1=1",
            "outFields": "*",
            "outSR": "4326",
            "returnGeometry": "true",
            "resultRecordCount": 1000,
            "f": "json",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    features = data.get("features", [])

    containers = []
    for f in features:
        attrs = f["attributes"]
        geom = f.get("geometry", {})
        containers.append(
            {
                "id": attrs["OBJECTID"],
                "number": attrs["Nummer_"],
                "street": attrs["Straat"],
                "type": attrs["Type__"],
                "subtype": attrs["Subtype_"],
                "condition": attrs["Staat_"],
                "color": attrs["Kleur_"],
                "supplier": attrs["Leverancier_"],
                "lat": geom.get("y"),
                "lng": geom.get("x"),
            }
        )
    print(f"  ✅ {len(containers)} containers fetched")
    return containers


def fetch_calendar(zipcode: str = "9402NV", housenumber: str = "10"):
    """Fetch collection calendar from Burgerportaal API."""
    print("📅 Fetching collection calendar...")
    signup = requests.post(
        f"https://www.googleapis.com/identitytoolkit/v3/relyingparty/signupNewUser?key={API_KEY}",
        json={},
    ).json()
    token = signup["idToken"]
    headers = {"authorization": token}

    resp = requests.get(
        f"{BASE_URL}/exposed/v2/organisations/{ORG_ID}/address",
        params={"zipcode": zipcode, "housenumber": housenumber},
        headers=headers,
    )
    resp.raise_for_status()
    address = resp.json()[0]

    resp = requests.get(
        f"{BASE_URL}/exposed/v2/organisations/{ORG_ID}/address/{address['addressId']}/calendar",
        headers=headers,
    )
    resp.raise_for_status()
    calendar = resp.json()
    print(f"  ✅ {len(calendar)} collection dates fetched")
    return {"address": address, "calendar": calendar}


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    containers = fetch_containers()
    with open(OUTPUT_DIR / "containers.json", "w") as f:
        json.dump(containers, f)
    print(f"  💾 Saved data/containers.json")

    calendar_data = fetch_calendar()
    with open(OUTPUT_DIR / "calendar.json", "w") as f:
        json.dump(
            {"fetched_at": datetime.now().isoformat(), **calendar_data},
            f,
            indent=2,
        )
    print(f"  💾 Saved data/calendar.json")

    # Write metadata
    meta = {
        "last_updated": datetime.now().isoformat(),
        "containers_count": len(containers),
        "calendar_items": len(calendar_data["calendar"]),
        "has_fillrates": (OUTPUT_DIR / "fillrates.json").exists(),
    }
    with open(OUTPUT_DIR / "meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    print(f"\n✅ Build complete! Open index.html to view the dashboard.")


if __name__ == "__main__":
    main()
