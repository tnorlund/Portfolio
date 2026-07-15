"""Regression tests for the JSON-only typeface registry compiler."""

import numpy as np
import pytest

from scripts.build_typeface_registry import _anti_copy


def _copied_atlases() -> dict[str, dict[str, np.ndarray]]:
    glyph = np.ones((32, 32), dtype=bool)
    chars = "ABCDEF"
    return {
        "left": {char: glyph.copy() for char in chars},
        "right": {char: glyph.copy() for char in chars},
    }


def test_anti_copy_rejects_unproven_duplicate_atlas() -> None:
    with pytest.raises(RuntimeError, match="anti-copy gate failed"):
        _anti_copy(_copied_atlases())


def test_anti_copy_audits_distinct_published_rom_sources() -> None:
    atlases = {
        f"ROM:{name}": glyphs
        for name, glyphs in _copied_atlases().items()
    }

    gate = _anti_copy(
        atlases,
        {"ROM:left": "source-a", "ROM:right": "source-b"},
    )

    assert gate["status"] == "PASS"
    assert gate["source_verified_rom_overlaps"] == [
        {
            "left": "ROM:left",
            "right": "ROM:right",
            "identical_substantial_glyphs": 6,
            "left_substantial_glyphs": 6,
            "left_source_sha256": "source-a",
            "right_source_sha256": "source-b",
            "reason": "independently published sibling printer-ROM specimens",
        }
    ]
