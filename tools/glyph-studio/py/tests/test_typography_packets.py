"""Typography packet publication tests (pure and offline)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import numpy as np
import pytest
import typography_packets as tp
from glyphstudio.packets import (
    build_manifest,
    canonical_json,
    judge_input,
    manifest_filename,
    verify_manifest,
    write_manifest,
)

_SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"


def _packet() -> dict:
    return {
        "packet_id": "00000000-0000-4000-8000-000000000001_7_L000",
        "image_id": "00000000-0000-4000-8000-000000000001",
        "receipt_id": 7,
        "line_index": 0,
        "line_ids": [10],
        "merchant": "Merchant",
        "slug": "merchant",
        "text": "HELLO",
        "features": {
            "cap_px": 12.0,
            "stroke_med": 2.0,
            "density_med": 0.25,
            "n_letters": 1,
            "contamination": 0.0,
            "underline": False,
            "reverse_video": 0,
            "slant_deg": 0.0,
            "tier": "normal",
        },
        "receipt_context": {
            "body_cap_px": 12.0,
            "body_stroke_px": 2.0,
            "image_size": [100, 200],
            "ocr_overlap_score": 0,
        },
        "crops": {
            "line": {
                "path": "packets/00000000-0000-4000-8000-000000000001_7_L000/line.png",
                "sha256": "a" * 64,
                "width": 30,
                "height": 12,
            },
            "letters": [
                {
                    "seq": 0,
                    "char": "H",
                    "line_id": 10,
                    "path": "packets/00000000-0000-4000-8000-000000000001_7_L000/letters/000_u0048.png",
                    "sha256": "b" * 64,
                    "width": 6,
                    "height": 10,
                }
            ],
        },
    }


def _params() -> dict:
    return {
        "receipts": 1,
        "max_lines": 25,
        "min_letters": 1,
        "max_overlaps": 2,
        "contamination_max": 0.2,
        "table": "Table",
        "merchants": ["Merchant:merchant"],
    }


def _schema(name: str) -> dict:
    return json.loads((_SCHEMAS / name).read_text(encoding="utf-8"))


def test_canonical_json_is_stable_and_rejects_nan():
    assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})
    with pytest.raises(ValueError):
        canonical_json({"bad": float("nan")})


def test_build_manifest_hashes_identity_and_crop_bytes_only():
    first = _packet()
    second = copy.deepcopy(first)
    second.update(
        {
            "packet_id": "00000000-0000-4000-8000-000000000002_8_L001",
            "image_id": "00000000-0000-4000-8000-000000000002",
            "receipt_id": 8,
            "line_index": 1,
        }
    )
    second["crops"]["line"]["sha256"] = "c" * 64
    second["crops"]["letters"][0]["sha256"] = "d" * 64

    forward = build_manifest([first, second], _params())
    reverse = build_manifest([second, first], _params())
    assert forward["content_hash"] == reverse["content_hash"]
    assert canonical_json(forward) == canonical_json(reverse)

    crop_changed = copy.deepcopy(first)
    crop_changed["crops"]["letters"][0]["sha256"] = "e" * 64
    assert build_manifest([crop_changed], _params())["content_hash"] != (
        build_manifest([first], _params())["content_hash"]
    )

    # Feature values are governed by MANIFEST_VERSION, not the content hash.
    feature_changed = copy.deepcopy(first)
    feature_changed["features"]["density_med"] = 0.99
    assert build_manifest([feature_changed], _params())["content_hash"] == (
        build_manifest([first], _params())["content_hash"]
    )


def test_write_manifest_is_immutable_and_updates_pointer(tmp_path):
    manifest = build_manifest([_packet()], _params())
    first = write_manifest(manifest, str(tmp_path))
    second = write_manifest(manifest, str(tmp_path))
    assert first == second
    assert len(list(tmp_path.glob("manifest_*.json"))) == 1
    pointer = (tmp_path / "MANIFEST").read_text()
    assert pointer == manifest_filename(manifest) + "\n"

    Path(first).write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="immutable publication differs"):
        write_manifest(manifest, str(tmp_path))


def test_judge_input_projection_and_blindness():
    packet = _packet()
    projected = judge_input(packet)
    assert set(projected) == {
        "schema_version",
        "packet_id",
        "line_text",
        "line_crop",
        "letter_crops",
        "context",
    }
    jsonschema.validate(
        projected, _schema("typography_judge_input.schema.json")
    )

    smuggled = copy.deepcopy(packet)
    smuggled["text"] = {"merchant": "expected answer leak"}
    with pytest.raises(ValueError, match="merchant"):
        judge_input(smuggled)


def _good_verdict() -> dict:
    return {
        "schema_version": "tj-out-1",
        "packet_id": "00000000-0000-4000-8000-000000000001_7_L000",
        "judge": "codex",
        "manifest_content_hash": "0" * 64,
        "abstain": False,
        "abstain_reason": None,
        "typeface": {
            "family_class": "mono",
            "monospace": True,
            "italic": False,
            "weight": "normal",
            "description": "Compact receipt face",
        },
        "tier": "normal",
        "underline": False,
        "reverse_video": False,
        "slant_deg": 0.0,
        "confidence": {
            "typeface": 0.9,
            "tier": 0.8,
            "underline": 0.9,
            "reverse_video": 0.95,
            "slant": 0.7,
        },
        "notes": "",
    }


def test_verdict_schema_accepts_good_and_rejects_bad_verdicts():
    schema = _schema("typography_judge_verdict.schema.json")
    jsonschema.validate(_good_verdict(), schema)

    bad_confidence = _good_verdict()
    bad_confidence["confidence"]["tier"] = 1.01
    missing_packet = _good_verdict()
    del missing_packet["packet_id"]
    bad_abstain = _good_verdict()
    bad_abstain.update(
        {
            "abstain": True,
            "abstain_reason": None,
            "typeface": None,
            "tier": None,
            "confidence": None,
        }
    )
    extra = _good_verdict()
    extra["merchant"] = "leak"
    # abstain=true must NOT carry positive verdicts (adjudication pollution)
    abstain_with_verdict = _good_verdict()
    abstain_with_verdict.update(
        {"abstain": True, "abstain_reason": "blurred beyond reading"}
    )
    # non-abstain verdicts must answer every probe attribute explicitly
    missing_underline = _good_verdict()
    del missing_underline["underline"]
    no_judge = _good_verdict()
    del no_judge["judge"]
    no_pin = _good_verdict()
    del no_pin["manifest_content_hash"]
    for verdict in (
        bad_confidence,
        missing_packet,
        bad_abstain,
        extra,
        abstain_with_verdict,
        missing_underline,
        no_judge,
        no_pin,
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(verdict, schema)

    # a clean abstain (nulls everywhere, reason given) is valid
    clean_abstain = _good_verdict()
    clean_abstain.update(
        {
            "abstain": True,
            "abstain_reason": "contaminated crop",
            "typeface": None,
            "tier": None,
            "underline": None,
            "reverse_video": None,
            "slant_deg": None,
            "confidence": None,
        }
    )
    jsonschema.validate(clean_abstain, schema)


def _synthetic_line(index: int, cap_px: float = 10.0) -> dict:
    ink = np.arange(48, dtype=np.uint8).reshape(6, 8)
    letter = np.arange(20, dtype=np.uint8).reshape(4, 5)
    return {
        "index": index,
        "text": f"LINE {index}",
        "line_ids": [100 + index],
        "cap_px": cap_px,
        "stroke_med": 2.0,
        "density_med": 0.25,
        "n_letters": 1,
        "contamination": 0.0,
        "underline": False,
        "reverse_video": 0,
        "slant_deg": 0.0,
        "line_crop": ink,
        "letters": [
            {
                "char": "A",
                "line_id": 100 + index,
                "crop": letter,
                "box": (1.0, 1.0, 6.0, 5.0),
            }
        ],
    }


class _FakeClient:
    def get_receipt_places_by_merchant(self, merchant_name, limit):
        del merchant_name, limit
        return [
            SimpleNamespace(
                image_id="00000000-0000-4000-8000-000000000001", receipt_id=7
            )
        ], None


def _args(out_dir: Path, max_lines: int = 25) -> SimpleNamespace:
    return SimpleNamespace(
        receipts=1,
        max_lines=max_lines,
        min_letters=1,
        max_overlaps=2,
        contamination=0.2,
        max_crop_cap_ratio=3.0,
        out_dir=str(out_dir),
        table="Table",
    )


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_cli_writer_is_end_to_end_deterministic(tmp_path, monkeypatch):
    def fake_extract(client, merchant, image_id, receipt_id, max_overlaps):
        del client, merchant, max_overlaps
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "image_size": [100, 200],
            "ocr_overlap_score": 0,
            "vetted_out": False,
            "lines": [_synthetic_line(0)],
        }

    monkeypatch.setattr(tp, "_extract_receipt", fake_extract)
    first = tmp_path / "first"
    second = tmp_path / "second"
    manifest1, _ = tp.generate_packets(
        _FakeClient(), ["Merchant:merchant"], _args(first)
    )
    manifest2, _ = tp.generate_packets(
        _FakeClient(), ["Merchant:merchant"], _args(second)
    )
    assert canonical_json(manifest1) == canonical_json(manifest2)
    assert _tree_bytes(first) == _tree_bytes(second)
    jsonschema.validate(
        manifest1, _schema("typography_packet_manifest.schema.json")
    )


def test_verify_manifest_detects_truncated_crop(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tp,
        "_extract_receipt",
        lambda client, merchant, image_id, receipt_id, max_overlaps: {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "image_size": [100, 200],
            "ocr_overlap_score": 0,
            "vetted_out": False,
            "lines": [_synthetic_line(0)],
        },
    )
    manifest, _ = tp.generate_packets(
        _FakeClient(), ["Merchant:merchant"], _args(tmp_path)
    )
    assert verify_manifest(manifest, str(tmp_path)) == []
    crop = tmp_path / manifest["packets"][0]["crops"]["line"]["path"]
    crop.write_bytes(crop.read_bytes()[:8])
    problems = verify_manifest(manifest, str(tmp_path))
    assert any("sha256 mismatch" in problem for problem in problems)


def test_tiers_are_assigned_before_packet_cap(tmp_path, monkeypatch):
    def fake_extract(client, merchant, image_id, receipt_id, max_overlaps):
        del client, merchant, max_overlaps
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "image_size": [100, 200],
            "ocr_overlap_score": 0,
            "vetted_out": False,
            "lines": [
                _synthetic_line(0, cap_px=10.0),
                _synthetic_line(1, cap_px=30.0),
            ],
        }

    monkeypatch.setattr(tp, "_extract_receipt", fake_extract)
    manifest, _ = tp.generate_packets(
        _FakeClient(), ["Merchant:merchant"], _args(tmp_path, max_lines=1)
    )
    assert len(manifest["packets"]) == 1
    packet = manifest["packets"][0]
    assert packet["line_index"] == 0
    # Median(10, 30) proves the out-of-cap large line joined the reference.
    assert packet["receipt_context"]["body_cap_px"] == 20.0
    assert packet["features"]["tier"] == "normal"
