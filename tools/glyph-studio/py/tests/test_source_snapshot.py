"""Pinned source snapshots: format, loader, and closed-path preference."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glyphstudio.source_snapshot import (  # noqa: E402
    assert_image_bytes,
    canvas_height_for_source,
    canvas_size,
    geometry_ids,
    load_snapshot,
    refuse_live_mismatch,
    resolve_pinned_payload,
    sha256_bytes,
    snapshot_payload,
    validate_snapshot,
    write_snapshot,
)

_LABEL = "00ded398-af6f-4a49-86f7-c79ccb554e48"
_MANIFEST = "00ded398-af6f-4a49-86f7-c79ccb554e48"


def _word(text="MILK", labels=None):
    return {
        "text": text,
        "line_id": 1,
        "word_id": 1,
        "bbox": [10.0, 20.0, 30.0, 40.0],
        "labels": ["B-PRODUCT_NAME"] if labels is None else labels,
    }


def _snap(**overrides):
    source_w, source_h = 760, 2471
    doc = {
        "version": 1,
        "slug": "sprouts",
        "merchant": "Sprouts Farmers Market",
        "label_receipt": {"image_id": _LABEL, "receipt_id": 2},
        "manifest_receipt": {"image_id": _MANIFEST, "receipt_id": 1},
        "geometry_receipt": {"image_id": _MANIFEST, "receipt_id": 1},
        "canvas": {
            "w": 760,
            "h": canvas_height_for_source(source_w, source_h),
        },
        "source_size": {"width": source_w, "height": source_h},
        "image_sha256": sha256_bytes(b"scan-bytes"),
        "image_type": "SCAN",
        "words": [_word()],
        "barcodes": [],
    }
    doc.update(overrides)
    return doc


def test_snapshot_pins_words_boxes_labels_and_canvas():
    snap = validate_snapshot(_snap())
    payload = snapshot_payload(snap)
    assert payload["words"][0]["text"] == "MILK"
    assert payload["words"][0]["bbox"] == [10.0, 20.0, 30.0, 40.0]
    assert payload["words"][0]["labels"] == ["B-PRODUCT_NAME"]
    assert canvas_size(snap) == (760, 2471)
    assert payload["width"] == 760
    assert payload["height"] == 2471
    assert geometry_ids(snap) == (_MANIFEST, 1)


def test_snapshot_records_both_receipt_ids_when_they_differ():
    snap = validate_snapshot(_snap())
    assert snap["label_receipt"]["receipt_id"] == 2
    assert snap["manifest_receipt"]["receipt_id"] == 1
    assert snap["geometry_receipt"]["receipt_id"] == 1


def test_canvas_must_be_the_height_the_source_size_produces():
    snap = _snap()
    snap["canvas"] = {"w": 760, "h": 2497}
    with pytest.raises(ValueError, match="committed canvas"):
        validate_snapshot(snap)


def test_label_id_resolves_to_geometry_words_not_a_second_payload(tmp_path):
    write_snapshot(_snap(), str(tmp_path))
    by_label = resolve_pinned_payload(_LABEL, 2, str(tmp_path))
    by_manifest = resolve_pinned_payload(_MANIFEST, 1, str(tmp_path))
    assert by_label is not None and by_manifest is not None
    assert by_label["words"] == by_manifest["words"]
    assert by_label["receipt_id"] == 1
    assert resolve_pinned_payload("missing", 1, str(tmp_path)) is None


def test_refuse_live_geometry_and_scan_bytes():
    snap = _snap()
    live = snapshot_payload(snap)
    refuse_live_mismatch(snap, live)
    live["height"] = 2497
    with pytest.raises(RuntimeError, match="does not match the pinned"):
        refuse_live_mismatch(snap, live)
    assert_image_bytes(snap, b"scan-bytes")
    with pytest.raises(RuntimeError, match="live scan sha256"):
        assert_image_bytes(snap, b"other-scan")


def test_load_roundtrip(tmp_path):
    write_snapshot(_snap(), str(tmp_path))
    loaded = load_snapshot("sprouts", str(tmp_path))
    assert loaded is not None
    assert loaded["words"][0]["text"] == "MILK"
    assert load_snapshot("missing", str(tmp_path)) is None


def test_shipped_pins_match_committed_canvases():
    """Cards whose live source still produces the committed canvas.

    Sprouts, Trader Joe's, CVS, In-N-Out, and Wild Fork are absent: the dev
    row behind the committed label id is a different merchant or a source
    size that does not produce the committed canvas. Those still need a
    historical capture.
    """
    studio = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    directory = os.path.join(studio, "fixtures", "source_snapshots")
    manifest_path = os.path.join(studio, "fixtures", "pipeline_merchants.json")
    pipeline = os.path.abspath(
        os.path.join(
            studio,
            "..",
            "..",
            "portfolio",
            "public",
            "synthetic-receipts",
            "pipeline",
        )
    )
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)["merchants"]
    expected = {"costco", "vons", "target", "speedway", "wholefoods"}
    pinned = set()
    if os.path.isdir(directory):
        for name in os.listdir(directory):
            if name.endswith(".json"):
                pinned.add(name[: -len(".json")])
    assert pinned == expected
    for slug in sorted(expected):
        snap = load_snapshot(slug, directory)
        assert snap is not None
        assert snap["canvas"] == manifest[slug]["dims"]
        labels_path = os.path.join(pipeline, slug, "final.labels.json")
        with open(labels_path, encoding="utf-8") as fh:
            labels = json.load(fh)
        image_id, receipt_id = labels["receipt_key"].rsplit("#", 1)
        assert snap["label_receipt"] == {
            "image_id": image_id,
            "receipt_id": int(receipt_id),
        }
        assert snap["manifest_receipt"] == {
            "image_id": manifest[slug]["receipt"]["image_id"],
            "receipt_id": int(manifest[slug]["receipt"]["receipt_id"]),
        }
        assert snap["image_type"]
        if slug == "target":
            # Dev IAM can read the receipt row but not this image object.
            assert snap["image_sha256"] is None
        else:
            assert snap["image_sha256"]
        assert snap["words"]
        width = snap["source_size"]["width"]
        height = snap["source_size"]["height"]
        assert snap["canvas"]["h"] == canvas_height_for_source(width, height)
    vons = load_snapshot("vons", directory)
    assert vons is not None
    assert vons["label_receipt"]["receipt_id"] != (
        vons["manifest_receipt"]["receipt_id"]
    )
    assert vons["geometry_receipt"] == vons["manifest_receipt"]
