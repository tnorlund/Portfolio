"""CLI flags for floor, top-up, and cost-capped captures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.similarity_harness.capture_golden import (
    _default_receipts,
    _load_manifest,
    main,
)
from scripts.similarity_harness.common import load_fixture


@pytest.mark.unit
def test_limit_below_floor_requires_allow_under_floor(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="pass --allow-under-floor"):
        main(
            [
                "--offline-bootstrap",
                "--limit",
                "5",
                "--out",
                str(tmp_path / "golden.json"),
            ]
        )


@pytest.mark.unit
def test_limit_caps_receipts_processed(tmp_path: Path) -> None:
    out = tmp_path / "golden.json"

    assert (
        main(["--offline-bootstrap", "--limit", "45", "--out", str(out)]) == 0
    )

    assert len(load_fixture(out)["receipts"]) == 45


@pytest.mark.unit
def test_allow_under_floor_permits_small_capture(tmp_path: Path) -> None:
    out = tmp_path / "golden.json"

    assert (
        main(
            [
                "--offline-bootstrap",
                "--limit",
                "35",
                "--allow-under-floor",
                "--out",
                str(out),
            ]
        )
        == 0
    )

    fixture = load_fixture(out, minimum_receipts=35)
    assert len(fixture["receipts"]) == 35


@pytest.mark.unit
def test_min_receipts_floor_is_configurable(tmp_path: Path) -> None:
    out = tmp_path / "golden.json"

    assert (
        main(
            [
                "--offline-bootstrap",
                "--min-receipts",
                "100",
                "--out",
                str(out),
            ]
        )
        == 1
    )

    assert not out.exists()


@pytest.mark.unit
def test_extra_receipts_top_up_and_dedup(tmp_path: Path) -> None:
    existing = _default_receipts()[0]
    extra_path = tmp_path / "extra.json"
    extra_path.write_text(
        json.dumps(
            [
                {"image_id": "extra-image", "receipt_id": 1},
                {
                    "image_id": existing["image_id"],
                    "receipt_id": existing["receipt_id"],
                },
            ]
        ),
        encoding="utf-8",
    )

    receipts = _load_manifest(None, extra_path=extra_path)

    assert len(receipts) == len(_default_receipts()) + 1
    added = [value for value in receipts if value["image_id"] == "extra-image"]
    assert added == [
        {
            "cohort": "extra",
            "image_id": "extra-image",
            "merchant_name": "",
            "receipt_id": 1,
        }
    ]

    out = tmp_path / "golden.json"
    assert (
        main(
            [
                "--offline-bootstrap",
                "--extra-receipts",
                str(extra_path),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    fixture = load_fixture(out)
    assert len(fixture["receipts"]) == len(receipts)
