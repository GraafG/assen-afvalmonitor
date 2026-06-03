"""
Capture Burgerportaal fill rate data by monitoring Firestore traffic.

This script polls the Firestore locations collection while you use the app.
When the app syncs container data, this script captures it.

Usage:
    1. Open the Burgerportaal app on your phone
    2. Run this script: python scripts/capture_fillrates.py
    3. Navigate to the map view in the app
    4. Wait for data to appear (the script polls every 3 seconds for 2 minutes)
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

API_KEY = "AIzaSyA6NkRqJypTfP-cjWzrZNFJzPUbBaGjOdk"
ORG_ID = "138204213565303512"
PROJECT = "burgerportaal-production"
FIRESTORE_BASE = "https://firestore.googleapis.com/v1"


def get_auth():
    """Get anonymous Firebase auth token."""
    signup = requests.post(
        f"https://www.googleapis.com/identitytoolkit/v3/relyingparty/signupNewUser?key={API_KEY}",
        json={},
    ).json()
    return signup["idToken"], signup["localId"]


def query_locations(token: str) -> list[dict]:
    """Query Firestore locations collection."""
    parent = f"projects/{PROJECT}/databases/(default)/documents/organisations/{ORG_ID}"
    query_body = {
        "structuredQuery": {
            "from": [{"collectionId": "locations"}],
            "limit": 500,
        }
    }
    resp = requests.post(
        f"{FIRESTORE_BASE}/{parent}:runQuery",
        headers={"Authorization": f"Bearer {token}"},
        json=query_body,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return [item["document"] for item in data if "document" in item]


def extract_container_data(documents: list[dict]) -> list[dict]:
    """Extract container data from Firestore documents."""
    containers = []
    for doc in documents:
        fields = doc.get("fields", {})
        container = {}
        for key, value in fields.items():
            if "stringValue" in value:
                container[key] = value["stringValue"]
            elif "integerValue" in value:
                container[key] = int(value["integerValue"])
            elif "doubleValue" in value:
                container[key] = float(value["doubleValue"])
            elif "geoPointValue" in value:
                geo = value["geoPointValue"]
                container[key] = {"lat": geo.get("latitude"), "lng": geo.get("longitude")}
            elif "mapValue" in value:
                # Nested object (like position with geohash)
                nested = {}
                for nk, nv in value["mapValue"].get("fields", {}).items():
                    if "stringValue" in nv:
                        nested[nk] = nv["stringValue"]
                    elif "doubleValue" in nv:
                        nested[nk] = float(nv["doubleValue"])
                    elif "geoPointValue" in nv:
                        geo = nv["geoPointValue"]
                        nested[nk] = {"lat": geo.get("latitude"), "lng": geo.get("longitude")}
                container[key] = nested
        containers.append(container)
    return containers


def main():
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    interval = 3

    print(f"🔍 Monitoring Firestore for fill rate data ({duration}s)...")
    print("📱 Open the MAP VIEW in your Burgerportaal app now!\n")

    token, uid = get_auth()
    start = time.time()
    found = False

    while time.time() - start < duration:
        elapsed = int(time.time() - start)
        try:
            docs = query_locations(token)
            if docs:
                print(f"\n✅ FOUND {len(docs)} container documents at t={elapsed}s!")
                containers = extract_container_data(docs)

                output_dir = Path(__file__).parent.parent / "data"
                output_dir.mkdir(exist_ok=True)
                output_file = output_dir / "fillrates.json"
                with open(output_file, "w") as f:
                    json.dump(
                        {
                            "fetched_at": datetime.now().isoformat(),
                            "count": len(containers),
                            "containers": containers,
                        },
                        f,
                        indent=2,
                    )
                print(f"💾 Saved to {output_file}")

                # Print sample
                for c in containers[:5]:
                    fill = c.get("containerFillLevel", "?")
                    num = c.get("containerNumber", "?")
                    print(f"  Container #{num}: {fill}% full")

                found = True
                break
            else:
                print(f"  t={elapsed}s: waiting...", end="\r")
        except Exception as e:
            print(f"  t={elapsed}s: error: {e}")

        time.sleep(interval)

    if not found:
        print(f"\n⚠️  No data found after {duration}s.")
        print("   The fill rate data may not sync unless the app map view is active.")
        print("   Try: open the app → tap the map icon → wait a few seconds → re-run this script.")


if __name__ == "__main__":
    main()
