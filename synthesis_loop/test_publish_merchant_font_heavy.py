"""Finding D: the heavy face variant must scale per-glyph weight overrides.

``glyphstudio.schema.merged_params`` applies a glyph's ``overrides.weight`` as a
REPLACEMENT of the font-level weight, not a delta. So building the heavy face by
scaling only ``font.json`` params.weight leaves any overridden glyph at its body
weight -- a mixed-weight heavy face. ``_heavy_variant_dir`` must scale the glyph
overrides by the same ratio.
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import publish_merchant_font as pmf  # noqa: E402


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def test_heavy_variant_scales_glyph_weight_overrides():
    with tempfile.TemporaryDirectory() as root:
        font_dir = os.path.join(root, "font")
        os.makedirs(font_dir)
        _write(
            os.path.join(font_dir, "font.json"),
            {"params": {"weight": 1.0, "dot": 30}},
        )
        # One glyph with a weight override, one without, one with a non-weight
        # override that must be left untouched.
        _write(
            os.path.join(font_dir, "glyphs", "u0046.json"),
            {"codepoint": 70, "overrides": {"weight": 1.2}, "strokes": []},
        )
        _write(
            os.path.join(font_dir, "glyphs", "u0041.json"),
            {"codepoint": 65, "strokes": []},
        )
        _write(
            os.path.join(font_dir, "glyphs", "u003a.json"),
            {
                "codepoint": 58,
                "overrides": {"weight": 0.88, "baselineNudgePx": 2},
                "strokes": [],
            },
        )

        with tempfile.TemporaryDirectory() as tmp:
            hdir = pmf._heavy_variant_dir(font_dir, tmp)

            font = json.load(
                open(os.path.join(hdir, "font.json"), encoding="utf-8")
            )
            assert font["params"]["weight"] == round(1.0 * pmf.HEAVY_RATIO, 4)

            f_glyph = json.load(
                open(
                    os.path.join(hdir, "glyphs", "u0046.json"),
                    encoding="utf-8",
                )
            )
            assert f_glyph["overrides"]["weight"] == round(
                1.2 * pmf.HEAVY_RATIO, 4
            )

            colon = json.load(
                open(
                    os.path.join(hdir, "glyphs", "u003a.json"),
                    encoding="utf-8",
                )
            )
            assert colon["overrides"]["weight"] == round(
                0.88 * pmf.HEAVY_RATIO, 4
            )
            # Non-weight overrides are untouched.
            assert colon["overrides"]["baselineNudgePx"] == 2

            # Glyph without overrides is unchanged.
            a_glyph = json.load(
                open(
                    os.path.join(hdir, "glyphs", "u0041.json"),
                    encoding="utf-8",
                )
            )
            assert "overrides" not in a_glyph

        # The source font dir is never mutated.
        src_font = json.load(
            open(os.path.join(font_dir, "font.json"), encoding="utf-8")
        )
        assert src_font["params"]["weight"] == 1.0
        src_glyph = json.load(
            open(
                os.path.join(font_dir, "glyphs", "u0046.json"),
                encoding="utf-8",
            )
        )
        assert src_glyph["overrides"]["weight"] == 1.2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
