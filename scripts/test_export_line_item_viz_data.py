"""Keep the public walkthrough aligned with the current offline decoder."""

import copy
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from receipt_upload.line_items import blocks
from receipt_upload.line_items.geometry import (
    extract_items,
    reconcile_extracted_items,
)

SPEC = importlib.util.spec_from_file_location(
    "line_item_viz_export",
    Path(__file__).with_name("export_line_item_viz_data.py"),
)
export = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export)


def inputs():
    return (
        json.loads(export.DEFAULT_OUTPUT.read_text()),
        json.loads(export.OCR_FIXTURE.read_text()),
        json.loads(export.GOLDEN_FIXTURE.read_text()),
        blocks.load_default_priors(),
    )


def test_committed_export_replays_current_decoder_without_aws():
    payload, fixture, golden, priors = inputs()
    with patch(
        "boto3.client", side_effect=AssertionError("offline export used AWS")
    ), patch.object(
        export,
        "check_cdn",
        side_effect=AssertionError("offline export used network"),
    ):
        refreshed = export.refresh_recorded_export(
            payload, fixture, golden, priors
        )
    assert refreshed == payload
    assert len(payload["receipts"]) == 8
    assert all(
        r["reconcile"]["status"]
        in {"match", "near", "mismatch", "no-baseline"}
        for r in payload["receipts"]
    )
    golden_by_key = {
        (r["image_id"], r["receipt_id"]): r for r in golden["receipts"]
    }
    fixture_by_key = {
        (r["image_id"], r["receipt_id"]): r for r in fixture["receipts"]
    }
    for receipt in payload["receipts"]:
        key = receipt["image_id"], receipt["receipt_id"]
        source = fixture_by_key[key]
        summary = export.build_summary(golden_by_key.get(key))
        items, _ = extract_items(
            source["words"], set(source["items_line_ids"]), summary=summary
        )
        y = {(w["line_id"], w["word_id"]): w["y_mid"] for w in source["words"]}
        assert receipt["items"] == [
            export.dump_item(item, y) for item in items
        ]
        result = reconcile_extracted_items(items, summary)
        assert receipt["reconcile"]["status"] == result.status
        assert receipt["reconcile"]["item_sum"] == result.item_sum
        assert receipt["reconcile"]["baseline"] == result.baseline


def test_band_annotations_match_roles_from_production_decoder():
    _, fixture, _, priors = inputs()
    for receipt in fixture["receipts"]:
        bands = blocks._zone_bands(receipt)
        with patch.object(blocks, "_zone_bands", return_value=bands):
            blocks.decode_band_blocks(receipt, priors)
        annotated = export.annotate_bands(receipt, priors)
        assert [band["role"] for band in bands] == [
            band["role"] for band in annotated
        ]


def test_offline_refresh_rejects_stale_geometry():
    payload, fixture, golden, priors = inputs()
    payload = copy.deepcopy(payload)
    receipt = payload["receipts"][0]
    word = next(w for w in receipt["words"] if w["in_zone"])
    word["text"] = "changed fixture token"
    with pytest.raises(ValueError, match="no longer matches"):
        export.refresh_recorded_export(payload, fixture, golden, priors)
