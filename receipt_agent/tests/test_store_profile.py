"""Tests for coherent store-profile extraction from the Places cache."""

from receipt_agent.agents.label_evaluator.store_profile import (
    StoreProfile,
    alternate_profiles,
    extract_store_profiles,
    store_profile_coverage,
)


def _place(place_id, **over):
    base = {
        "place_id": place_id,
        "merchant_name": "Vons",
        "formatted_address": "5671 Kanan Rd, Agoura Hills, CA 91301, USA",
        "short_address": "5671 Kanan Rd, Agoura Hills",
        "phone_number": "(805) 495-6303",
        "website": "https://local.vons.com/ca/agoura-hills",
        "hours_summary": ["Mon-Sun 6AM-11PM"],
    }
    base.update(over)
    return base


def test_extract_splits_address_and_cleans_website():
    [profile] = extract_store_profiles([_place("p1")])
    assert profile.street == "5671 Kanan Rd"
    assert profile.city_state_zip == "Agoura Hills, CA 91301"  # country dropped
    assert profile.phone == "(805) 495-6303"
    assert profile.website == "local.vons.com"  # scheme + www + path stripped
    assert profile.address_lines == ("5671 Kanan Rd", "Agoura Hills, CA 91301")
    assert profile.is_complete()


def test_extract_dedupes_by_place_keeping_most_complete():
    sparse = _place("p1", phone_number="", website="", hours_summary=[])
    rich = _place("p1")  # same place_id, fully populated
    profiles = extract_store_profiles([sparse, rich])
    assert len(profiles) == 1
    assert profiles[0].phone == "(805) 495-6303"
    assert profiles[0].website == "local.vons.com"


def test_extract_skips_rows_without_place_id_or_address():
    profiles = extract_store_profiles(
        [
            _place("p1"),
            {"merchant_name": "Vons"},  # no place_id
            _place("p2", formatted_address=""),  # no address -> incomplete
        ]
    )
    by_id = {p.place_id: p for p in profiles}
    assert by_id["p1"].is_complete()
    # p2 is retained but flagged incomplete (no address to compose from)
    assert "p2" in by_id and not by_id["p2"].is_complete()


def test_alternate_profiles_excludes_own_and_incomplete():
    profiles = [
        StoreProfile("p1", "Vons", "1 A St", "Town, CA 90001"),
        StoreProfile("p2", "Vons", "2 B St", "City, CA 90002"),
        StoreProfile("p3", "Vons"),  # incomplete (no address)
    ]
    alts = alternate_profiles(profiles, own_place_id="p1")
    assert [p.place_id for p in alts] == ["p2"]


def test_coverage_flags_thin_cache():
    multi = store_profile_coverage([_place("p1"), _place("p2")])
    assert multi["distinct_complete_locations"] == 2
    assert multi["supports_header_composition"] is True

    thin = store_profile_coverage([_place("p1")])
    assert thin["supports_header_composition"] is False  # no alternate branch


def test_coverage_handles_empty_or_missing():
    assert store_profile_coverage(None)["distinct_locations"] == 0
    assert store_profile_coverage([])["supports_header_composition"] is False
