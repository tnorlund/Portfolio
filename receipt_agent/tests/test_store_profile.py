"""Tests for coherent store-profile extraction from the Places cache."""

from receipt_agent.agents.label_evaluator.store_profile import (
    StoreProfile,
    alternate_profiles,
    extract_store_profiles,
    reflow_line_boxes,
    store_profile_coverage,
)


def _assert_clean_layout(boxes):
    assert boxes is not None
    for box in boxes:
        assert box[0] < box[2] and box[1] < box[3]  # non-degenerate
    for left, right in zip(boxes, boxes[1:]):
        assert left[2] <= right[0]  # ordered, non-overlapping


def test_reflow_same_token_count_keeps_band_and_order():
    original = [[379, 853, 462, 863], [467, 853, 633, 863], [638, 853, 721, 863]]
    out = reflow_line_boxes(original, ["5671", "Kanan", "Rd"])
    _assert_clean_layout(out)
    assert len(out) == 3
    assert out[0][0] == 379 and out[-1][2] == 721  # spans the original line
    assert {b[1] for b in out} == {853} and {b[3] for b in out} == {863}


def test_reflow_handles_growing_token_count():
    original = [[379, 853, 462, 863], [638, 853, 721, 863]]
    out = reflow_line_boxes(original, ["12345", "North", "Industrial", "Park", "Blvd"])
    _assert_clean_layout(out)
    assert len(out) == 5  # more tokens than original, still clean


def test_reflow_skips_when_value_cannot_fit():
    # A tiny span cannot hold many tokens without degenerate boxes -> skip.
    assert reflow_line_boxes([[500, 800, 505, 810]], list("abcdef")) is None


def test_reflow_rejects_empty_inputs():
    assert reflow_line_boxes([], ["x"]) is None
    assert reflow_line_boxes([[0, 0, 100, 10]], []) is None


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


def test_extract_merges_split_duplicate_place_rows_preserving_address():
    address_only = _place(
        "p1",
        phone_number="",
        website="",
        hours_summary=[],
    )
    contact_only = _place(
        "p1",
        formatted_address="",
        short_address="",
        phone_number="(805) 495-6303",
        website="https://www.vons.com",
        hours_summary=["Mon-Fri 8AM-9PM"],
    )

    [profile] = extract_store_profiles([contact_only, address_only])

    assert profile.street == "5671 Kanan Rd"
    assert profile.city_state_zip == "Agoura Hills, CA 91301"
    assert profile.phone == "(805) 495-6303"
    assert profile.website == "vons.com"
    assert profile.is_complete()


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
