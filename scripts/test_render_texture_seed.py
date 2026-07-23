"""Regression test for paper-texture determinism in render_synthetic_receipts.

The paper-texture seed used to be ``crc32(basename(output_path))``, coupling
the render to an incidental input: an evaluator (glyph_review / the line
scorecard) re-renders the SAME receipt to a fresh debug path each run
(``det_1.png``, ``det_2.png`` ...), so each run got a different paper texture,
which shifted the thresholded glyph-height measurements and made the scorecard
gates bounce run-to-run on identical inputs.

The seed is now derived from the receipt CONTENT, so a given receipt renders
the same texture regardless of where it is written, while distinct receipts
still get distinct textures.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_synthetic_receipts as rsr  # noqa: E402


def _receipt():
    return {
        "merchant_name": "In-N-Out Burger",
        "words": [
            {
                "line_id": 2,
                "word_id": 1,
                "text": "DOUBLE-DOUBLE",
                "bbox": [10.0, 20.0, 120.0, 45.0],
            },
            {
                "line_id": 1,
                "word_id": 3,
                "text": "TOTAL",
                "bbox": [10.5, 4.25, 60.0, 30.0],
            },
        ],
    }


def test_seed_is_stable_for_identical_content():
    assert rsr._content_texture_seed(_receipt()) == rsr._content_texture_seed(
        _receipt()
    )


def test_seed_is_independent_of_word_order():
    r = _receipt()
    shuffled = {
        "merchant_name": r["merchant_name"],
        "words": list(reversed(r["words"])),
    }
    assert rsr._content_texture_seed(r) == rsr._content_texture_seed(shuffled)


def test_seed_changes_with_content():
    r = _receipt()
    other = _receipt()
    other["words"][0]["text"] = "CHEESEBURGER"
    assert rsr._content_texture_seed(r) != rsr._content_texture_seed(other)

    other2 = _receipt()
    other2["merchant_name"] = "Target"
    assert rsr._content_texture_seed(r) != rsr._content_texture_seed(other2)


def test_seed_preserves_source_identity_for_local_render_repairs():
    source = _receipt()
    repaired = _receipt()
    repaired["words"][0]["_texture_seed_text"] = "DOUBLE-DOUBLE"
    repaired["words"][0]["text"] = "DOUBLE DOUBLE"

    assert rsr._content_texture_seed(source) == rsr._content_texture_seed(
        repaired
    )


def test_seed_handles_empty_and_missing_fields():
    # Must not raise on sparse dicts (barcodes-only / logo-only receipts).
    assert isinstance(rsr._content_texture_seed({}), int)
    assert isinstance(rsr._content_texture_seed({"merchant_name": "X"}), int)
