"""Regression tests for label cleanup helper scripts."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = PROJECT_ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def _word(image_id, receipt_id, line_id, word_id, text, y):
    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "line_id": line_id,
        "word_id": word_id,
        "text": text,
        "bounding_box": {"y": y},
    }


def _label(image_id, receipt_id, line_id, word_id, label):
    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "line_id": line_id,
        "word_id": word_id,
        "label": label,
        "validation_status": "VALID",
    }


def test_audit_footer_anchors_reconciled_duplicate_to_lowest_grand_total():
    module = _load_script("audit_footer_labels.py")
    image_id = "image-1"
    receipt_id = 1
    payload = {
        "merchant_name": "Test Market",
        "receipt_words": [
            _word(image_id, receipt_id, 1, 1, "APPLES", 0.60),
            _word(image_id, receipt_id, 2, 1, "10.00", 0.20),
            _word(image_id, receipt_id, 3, 1, "9.00", 0.22),
            _word(image_id, receipt_id, 4, 1, "1.00", 0.21),
            # Duplicate matching total above the item area. This must not become
            # the footer boundary, or the APPLES label would be flipped.
            _word(image_id, receipt_id, 5, 1, "10.00", 0.70),
        ],
        "receipt_word_labels": [
            _label(image_id, receipt_id, 1, 1, "PRODUCT_NAME"),
            _label(image_id, receipt_id, 2, 1, "GRAND_TOTAL"),
            _label(image_id, receipt_id, 3, 1, "SUBTOTAL"),
            _label(image_id, receipt_id, 4, 1, "TAX"),
            _label(image_id, receipt_id, 5, 1, "GRAND_TOTAL"),
        ],
    }

    result = module.audit(payload)

    assert result["auto_safe"] == []
    assert result["summary"]["auto_safe_by_pattern"] == {}


def test_apply_label_flips_removes_embedded_word_labels(tmp_path):
    export_path = tmp_path / "merchant.json"
    flips_path = tmp_path / "flips.json"
    flips_path.write_text(
        json.dumps(
            {
                "auto_safe": [
                    {
                        "image_id": "image-1",
                        "receipt_id": 1,
                        "line_id": 2,
                        "word_id": 3,
                        "label": "WEBSITE",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    export_path.write_text(
        json.dumps(
            {
                "receipt_word_labels": [
                    {
                        "image_id": "image-1",
                        "receipt_id": 1,
                        "line_id": 2,
                        "word_id": 3,
                        "label": "WEBSITE",
                        "validation_status": "VALID",
                    }
                ],
                "receipt_words": [
                    {
                        "image_id": "image-1",
                        "receipt_id": 1,
                        "line_id": 2,
                        "word_id": 3,
                        "labels": ["WEBSITE", "STORE_NUMBER"],
                    }
                ],
                "receipts": [
                    {
                        "image_id": "image-1",
                        "receipt_id": 1,
                        "lines": [
                            {
                                "line_id": 2,
                                "words": [
                                    {
                                        "word_id": 3,
                                        "labels": ["WEBSITE", "STORE_NUMBER"],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "apply_label_flips.py"),
            "--flips",
            str(flips_path),
            "--export",
            str(export_path),
        ],
        check=True,
    )

    patched = json.loads(export_path.read_text(encoding="utf-8"))
    assert patched["receipt_word_labels"][0]["validation_status"] == "INVALID"
    assert patched["receipt_words"][0]["labels"] == ["STORE_NUMBER"]
    assert patched["receipts"][0]["lines"][0]["words"][0]["labels"] == [
        "STORE_NUMBER"
    ]


def test_standalone_composer_rewrites_unlabeled_amount_on_labeled_summary_line():
    module = _load_script("compose_synthetic_receipts.py")
    receipt = {
        "lines": [
            {
                "words": [
                    {"text": "SUBTOTAL", "bbox": [500, 600, 590, 620], "labels": ["SUBTOTAL"]},
                    {"text": "8.00", "bbox": [830, 600, 885, 620], "labels": []},
                ]
            },
            {
                "words": [
                    {"text": "TOTAL", "bbox": [500, 570, 570, 590], "labels": ["GRAND_TOTAL"]},
                    {"text": "8.00", "bbox": [830, 570, 885, 590], "labels": []},
                ]
            },
        ]
    }

    module._set_totals(
        receipt,
        module.Decimal("12.34"),
        module.Decimal("12.34"),
        module.Decimal("8.00"),
        tax=module.Decimal("0.00"),
    )

    assert receipt["lines"][0]["words"][1]["text"] == "12.34"
    assert receipt["lines"][1]["words"][1]["text"] == "12.34"


def test_mac_mini_smoke_prompt_guards_noop_write():
    source = (PROJECT_ROOT / "scripts" / "mac_mini_mcp_smoke_test.sh").read_text(
        encoding="utf-8"
    )

    assert "include_image_metrics true" in source
    assert "base_receipt_image.lookup_status" in source
    assert "dark_pixel_density_ratio" in source
    assert "SMOKE_TEST read=PASS_OR_FAIL metrics=PASS_OR_FAIL write=PASS_OR_FAIL" in source
    assert "SMOKE_TEST read=PASS metrics=PASS write=PASS" in source
    assert "status_filter INVALID" in source
    assert "If it does not, do NOT call update_word_label" in source
    assert "already INVALID" in source
