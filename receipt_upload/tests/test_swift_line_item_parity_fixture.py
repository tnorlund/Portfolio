"""Anti-drift gate for the Swift line-item decoder's parity expectations.

WHY THIS TEST EXISTS
--------------------
The Swift port of the band-block decoder (#1313) shipped with a CHECKED-IN
snapshot of the Python decoder's output and a Swift test asserting "33/33
parity" against it. Python then gained the non-product band filter (#1320),
the printed-total fallback (#1321), the three-figure reconciliation baseline
(#1324) and the zone-gap boundary extension (#1329). The snapshot never
moved, so the gate kept passing while it measured agreement with a Python
that no longer existed -- a decorative green check.

Regenerating the snapshot in CI and diffing it against the committed bytes is
the only form of this gate that cannot rot: any change to the Python decoder
either produces identical output (nothing to do) or fails here until the
expectations are regenerated, which in turn makes the Swift parity test fail
until the port is updated.

WHY IT LIVES IN THE PYTHON MATRIX
---------------------------------
`.github/workflows/swift-ci.yml` only triggers on `receipt_ocr_swift/**`, so
a change to `receipt_upload/line_items/geometry.py` -- the actual source of
drift -- does not run any Swift job at all. This test runs in the
`receipt_upload` matrix leg, which every such change triggers. The Swift
workflow ALSO runs the generator in `--check` mode (and now watches the
Python decoder paths), so both sides of the fence are guarded.

The generator itself is the single source of the expectations:
`receipt_ocr_swift/Scripts/generate_line_items_parity.py`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "receipt_ocr_swift" / "Scripts"

pytestmark = pytest.mark.skipif(
    not _SCRIPTS.exists(),
    reason="receipt_ocr_swift package not present in this checkout",
)

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _generator():
    import generate_line_items_parity as generator

    return generator


def test_swift_parity_expectations_match_the_live_python_decoder() -> None:
    """Regenerate every Swift parity artifact and diff the committed bytes.

    A failure here means the Python decoder changed. Run:

        python receipt_ocr_swift/Scripts/generate_line_items_parity.py

    then update the Swift port until its parity test passes again. Never
    hand-edit the expectation files.
    """
    generator = _generator()
    artifacts = generator.generate(
        generator.DEFAULT_OCR, generator.DEFAULT_GOLDEN
    )

    stale = []
    for path, payload in artifacts.items():
        if not path.exists():
            stale.append(f"{path.name}: MISSING")
            continue
        current = path.read_text(encoding="utf-8")
        if current != payload:
            stale.append(
                f"{path.name}: {len(current)} bytes committed vs "
                f"{len(payload)} bytes regenerated"
            )

    assert not stale, (
        "Swift line-item parity expectations are STALE against the current "
        "Python decoder:\n  "
        + "\n  ".join(stale)
        + "\nRegenerate with `python receipt_ocr_swift/Scripts/"
        "generate_line_items_parity.py` and update the Swift port."
    )


def test_swift_structure_expectations_match_the_live_python_decoder() -> None:
    """Same gate, for the end-to-end structure fixture.

    ``receipt_structure_parity_expected.json`` carries decoded ITEMS (with
    quantity and unit price) alongside the rows and sections, so a decoder
    change rots it exactly as fast as the line-item fixture -- but it was
    not covered here, and ``swift-ci.yml`` does not run on
    ``receipt_upload`` changes. The gap was found the expensive way: a
    decoder change passed this file's own anti-drift test and every
    receipt_upload test, then failed
    ``ReceiptStructurePipelineTests.goldenEndToEndParityAcrossTheWholeGoldenSet``
    in CI on three receipts. Regenerate with
    ``python receipt_ocr_swift/Scripts/generate_receipt_structure_parity.py``.
    """
    import generate_receipt_structure_parity as structure

    committed = structure.DEFAULT_OUTPUT.read_text(encoding="utf-8")
    regenerated = structure.generate()
    assert committed == regenerated, (
        "Swift structure parity expectations are STALE against the current "
        f"Python decoder: {len(committed)} bytes committed vs "
        f"{len(regenerated)} bytes regenerated. Regenerate with "
        "`python receipt_ocr_swift/Scripts/"
        "generate_receipt_structure_parity.py` and update the Swift port."
    )


def test_swift_golden_ocr_copy_is_byte_identical() -> None:
    """The Swift package's copy of the golden OCR words is not a fork."""
    generator = _generator()
    canonical = generator.DEFAULT_OCR.read_text(encoding="utf-8")
    copy = generator.SWIFT_OCR_COPY.read_text(encoding="utf-8")
    assert canonical == copy, (
        "receipt_ocr_swift's golden OCR fixture has diverged from "
        "receipt_upload/tests/fixtures/line_items_golden_ocr.json; "
        "regenerate with generate_line_items_parity.py"
    )


def test_expectations_cover_the_whole_golden_set() -> None:
    """Every canonical golden receipt is represented, none invented."""
    generator = _generator()
    canonical = json.loads(generator.DEFAULT_OCR.read_text(encoding="utf-8"))[
        "receipts"
    ]
    committed = json.loads(
        generator.DEFAULT_OUTPUT.read_text(encoding="utf-8")
    )
    assert [(r["image_id"], r["receipt_id"]) for r in canonical] == [
        (r["image_id"], r["receipt_id"]) for r in committed
    ]
    # The golden set only ever grows; a shrink means someone dropped
    # receipts rather than regenerating.
    assert len(committed) >= 35


def test_guard_cases_exercise_every_non_product_regex() -> None:
    """The synthetic guard vectors must keep covering all four guards.

    The 35-receipt golden set decodes identically with the pre-#1320
    regexes, so without these vectors nothing in CI can observe a stale
    SETTLEMENT_RE / WAS_PRICE_RE / SALE_PRICE_RE / NON_PRODUCT_NOTE_RE in
    the Swift port -- which is exactly how three of them went stale.
    """
    from receipt_upload.line_items.geometry import (
        NON_PRODUCT_NOTE_RE,
        SALE_PRICE_RE,
        SETTLEMENT_RE,
        WAS_PRICE_RE,
    )

    generator = _generator()
    cases = json.loads(generator.GUARD_OUTPUT.read_text(encoding="utf-8"))
    assert cases, "guard vectors missing"

    hit = {"settlement": 0, "was": 0, "sale": 0, "note": 0}
    import re as _re

    for case in cases:
        text = " ".join(w["text"] for w in case["words"] if w["line_id"] == 2)
        bare = _re.sub(r"\$?\d[\d.,]*", " ", text).strip()
        if SETTLEMENT_RE.match(bare):
            hit["settlement"] += 1
        if WAS_PRICE_RE.search(text):
            hit["was"] += 1
        if SALE_PRICE_RE.search(text):
            hit["sale"] += 1
        if NON_PRODUCT_NOTE_RE.search(text):
            hit["note"] += 1

    missing = [name for name, count in hit.items() if count == 0]
    assert not missing, (
        f"guard vectors no longer exercise: {missing}. Add cases to "
        "GUARD_BANDS in generate_line_items_parity.py."
    )
