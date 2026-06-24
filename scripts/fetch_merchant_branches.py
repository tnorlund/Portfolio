#!/usr/bin/env python3
"""Fetch sibling store branches for a merchant and enrich its Places cache.

For a merchant whose cache has only one (or too few) complete store locations,
this queries Google Places Text Search around a known store, pulls full details
for each sibling branch of the SAME merchant, and appends them as
``receipt_places`` records to the merchant's grouped export. That gives
``compose_store_header`` the >=2 complete branches it needs to synthesize
coherent store-location diversity for thin-cache merchants.

Paid: makes Google Places API calls (textsearch + details). Pass the deployed
key (a Pulumi secret) via --api-key, e.g.

    python3.12 scripts/fetch_merchant_branches.py \
      --export ./grouped/gelsons_westlake_village.json \
      --api-key "$(cd infra && pulumi config get GOOGLE_PLACES_API_KEY --stack dev)" \
      --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


def _merchant_match(name_a: str, name_b: str) -> bool:
    """Loose same-merchant check: share the first significant token."""

    def key(name: str) -> str:
        tokens = re.findall(r"[A-Za-z0-9']+", str(name).lower())
        return tokens[0] if tokens else ""

    a, b = key(name_a), key(name_b)
    return bool(a) and a == b


def _build_client(api_key: str, table_name: str, aws_region: str = "us-east-1"):
    from receipt_places import PlacesClient, PlacesConfig

    # A valid DynamoDB table is required (the client always builds a cache
    # manager). Caching is ToS-compliant and persists fetched place details.
    return PlacesClient(
        config=PlacesConfig(
            api_key=api_key,
            table_name=table_name,
            aws_region=aws_region,
            cache_enabled=True,
            cache_ttl_days=30,
        )
    )


def _seed_place(places: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A complete cached place to bias the regional search around."""
    for place in places:
        if (
            place.get("place_id")
            and place.get("formatted_address")
            and place.get("latitude") is not None
            and place.get("longitude") is not None
        ):
            return place
    return places[0] if places else None


def _details_to_record(
    place: Any,
    merchant_name: str,
) -> dict[str, Any] | None:
    data = place if isinstance(place, dict) else (
        place.model_dump() if hasattr(place, "model_dump") else {}
    )
    place_id = data.get("place_id")
    address = data.get("formatted_address")
    if not place_id or not address:
        return None
    geometry = data.get("geometry") or {}
    location = geometry.get("location") or {}
    latitude = (
        data.get("latitude")
        if data.get("latitude") is not None
        else location.get("lat", location.get("latitude"))
    )
    longitude = (
        data.get("longitude")
        if data.get("longitude") is not None
        else location.get("lng", location.get("longitude"))
    )
    return {
        "place_id": place_id,
        "merchant_name": merchant_name,
        "formatted_address": address,
        "phone_number": data.get("formatted_phone_number")
        or data.get("phone_number"),
        "phone_intl": data.get("international_phone_number")
        or data.get("phone_intl"),
        "website": data.get("website"),
        "latitude": latitude,
        "longitude": longitude,
        "merchant_category": (data.get("types") or [None])[0]
        if data.get("types")
        else None,
        "business_status": data.get("business_status"),
        "validation_status": "branch_fetch",
        "source": "places_textsearch_branch_fetch",
    }


def fetch_branches(
    export_path: str,
    api_key: str,
    *,
    table_name: str = "ReceiptsTable-dc5be22",
    query: str | None = None,
    max_branches: int = 10,
    radius_m: int = 60000,
    dry_run: bool = False,
) -> dict[str, Any]:
    payload = json.loads(Path(export_path).read_text(encoding="utf-8"))
    merchant = str(payload.get("merchant_name") or "").strip()
    places = payload.get("receipt_places") or []
    existing_ids = {p.get("place_id") for p in places if p.get("place_id")}

    seed = _seed_place(places)
    if not seed:
        return {"merchant": merchant, "error": "no_seed_place"}

    client = _build_client(api_key, table_name)
    lat, lng = seed.get("latitude"), seed.get("longitude")
    # The canonical merchant name often includes the branch ("Gelson's Westlake
    # Village"), which returns only that one store. --query lets the caller pass
    # the bare brand ("Gelson's") to discover sibling branches.
    search_query = (query or merchant).strip()
    params: dict[str, Any] = {"query": search_query}
    if lat is not None and lng is not None:
        params["location"] = f"{lat},{lng}"
        params["radius"] = radius_m

    response = client._make_request("textsearch/json", params)  # noqa: SLF001
    results = response.get("results") or []

    new_records: list[dict[str, Any]] = []
    seen: set[str] = set(existing_ids)
    for result in results:
        if len(new_records) >= max_branches:
            break
        place_id = result.get("place_id")
        name = result.get("name") or ""
        if not place_id or place_id in seen:
            continue
        if not _merchant_match(merchant, name):
            continue
        seen.add(place_id)
        details = client.get_place_details(place_id)
        if details is None:
            continue
        record = _details_to_record(details, merchant)
        if record and record.get("formatted_address"):
            new_records.append(record)

    summary = {
        "merchant": merchant,
        "existing_places": len(existing_ids),
        "results_returned": len(results),
        "new_branches": len(new_records),
        "new_branch_addresses": [r["formatted_address"] for r in new_records],
        "dry_run": dry_run,
    }
    if not dry_run and new_records:
        payload["receipt_places"] = places + new_records
        Path(export_path).write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        summary["written_to"] = export_path
    return summary


def _load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from a gitignored .env into os.environ (no deps).

    Keeps the API key off the command line / process list and out of git.
    """
    import os

    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", required=True, help="grouped merchant JSON")
    parser.add_argument(
        "--api-key",
        default=None,
        help="Google Places key; defaults to RECEIPT_AGENT_GOOGLE_PLACES_API_KEY",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="search query / brand (defaults to the merchant name)",
    )
    parser.add_argument("--max", type=int, default=10, dest="max_branches")
    parser.add_argument("--radius-m", type=int, default=60000)
    parser.add_argument("--table", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    _load_dotenv()
    api_key = args.api_key or os.environ.get("RECEIPT_AGENT_GOOGLE_PLACES_API_KEY")
    if not api_key:
        print(
            json.dumps(
                {
                    "error": "no_api_key",
                    "hint": "set RECEIPT_AGENT_GOOGLE_PLACES_API_KEY in .env "
                    "or pass --api-key",
                }
            )
        )
        return 1
    table = args.table or os.environ.get("DYNAMO_TABLE_NAME", "ReceiptsTable-dc5be22")

    sys.path.insert(0, "receipt_agent")
    summary = fetch_branches(
        args.export,
        api_key,
        table_name=table,
        query=args.query,
        max_branches=args.max_branches,
        radius_m=args.radius_m,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
