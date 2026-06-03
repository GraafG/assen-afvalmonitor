"""
Burgerportaal API client for Gemeente Assen waste collection data.
Fetches collection schedule using Firebase Anonymous Auth + Cloud Functions.
"""

import json
import sys
from datetime import datetime, date
from pathlib import Path

import requests

API_KEY = "AIzaSyA6NkRqJypTfP-cjWzrZNFJzPUbBaGjOdk"
BASE_URL = "https://europe-west3-burgerportaal-production.cloudfunctions.net"
ORG_ID = "138204213565303512"  # Gemeente Assen


def get_auth_token() -> str:
    """Get a Firebase anonymous auth token."""
    resp = requests.post(
        f"https://www.googleapis.com/identitytoolkit/v3/relyingparty/signupNewUser?key={API_KEY}",
        json={},
    )
    resp.raise_for_status()
    return resp.json()["idToken"]


def lookup_address(token: str, zipcode: str, housenumber: str) -> dict:
    """Look up an address and return the first match."""
    resp = requests.get(
        f"{BASE_URL}/exposed/v2/organisations/{ORG_ID}/address",
        params={"zipcode": zipcode, "housenumber": housenumber},
        headers={"authorization": token},
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise ValueError(f"No address found for {zipcode} {housenumber}")
    return results[0]


def get_calendar(token: str, address_id: str) -> list[dict]:
    """Get the waste collection calendar for an address."""
    resp = requests.get(
        f"{BASE_URL}/exposed/v2/organisations/{ORG_ID}/address/{address_id}/calendar",
        headers={"authorization": token},
    )
    resp.raise_for_status()
    return resp.json()


def main():
    zipcode = sys.argv[1] if len(sys.argv) > 1 else "9402NV"
    housenumber = sys.argv[2] if len(sys.argv) > 2 else "10"

    print(f"Fetching waste collection data for {zipcode} {housenumber}...")

    token = get_auth_token()
    address = lookup_address(token, zipcode, housenumber)
    print(f"Address: {address['street']} {address['housenumber']}, {address['city']}")

    calendar = get_calendar(token, address["addressId"])

    today = date.today()
    upcoming = [
        item
        for item in calendar
        if date(item["year"], item["month"], item["day"]) >= today
    ]
    upcoming.sort(key=lambda x: x["collectionDate"])

    print(f"\nUpcoming collections ({len(upcoming)} total):\n")
    print(f"{'Date':<12} {'Fraction':<10} {'Days'}")
    print("-" * 35)

    for item in upcoming[:10]:
        collection_date = date(item["year"], item["month"], item["day"])
        days_until = (collection_date - today).days
        day_label = "today" if days_until == 0 else f"in {days_until}d"
        print(f"{collection_date.isoformat():<12} {item['fraction']:<10} {day_label}")

    # Save full data as JSON
    output_dir = Path(__file__).parent.parent / "data"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "calendar.json"
    with open(output_file, "w") as f:
        json.dump(
            {
                "fetched_at": datetime.now().isoformat(),
                "address": address,
                "calendar": calendar,
            },
            f,
            indent=2,
        )
    print(f"\nFull data saved to {output_file}")


if __name__ == "__main__":
    main()
