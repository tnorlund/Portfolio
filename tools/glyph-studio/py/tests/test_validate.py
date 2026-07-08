"""Unit tests for glyphstudio.validate -- the render-free claim checker.

Hermetic: reuses a tiny synthetic atlas so the saturation / monotonicity /
linearity checks run without the vault or a receipt render.
"""

import numpy as np
import pytest

from glyphstudio import validate


@pytest.fixture()
def font(tmp_path):
    """Solid cap 'A', ringed cap 'B', sparse digit '0' (all erodable)."""
    from receipt_agent.agents.label_evaluator.rendering.bitmap_font import (
        BitmapFont,
    )

    solid = np.ones((40, 40), np.uint8)
    ring = np.ones((40, 40), np.uint8)
    ring[10:30, 10:30] = 0
    sparse = np.zeros((40, 40), np.uint8)
    sparse[18:22, :] = 1
    sparse[:, 18:22] = 1
    p = tmp_path / "syn.glyphs.npz"
    np.savez_compressed(
        p, c65=solid, c66=ring, c48=sparse, o65=0, o66=0, o48=0
    )
    return BitmapFont(str(p))


def test_atlas_corpus_text_is_every_glyph_once(font):
    # keys are chars; sorted -> '0','A','B'
    assert validate.atlas_corpus_text(font) == "0AB"


def test_saturation_thin_finds_the_plateau():
    # falls 0.5 -> 0.4 -> 0.3, then flat: saturates at thin 0.3.
    curve = [(0.0, 0.5), (0.1, 0.4), (0.2, 0.3), (0.3, 0.3), (0.4, 0.3)]
    assert validate.saturation_thin(curve, tol=1e-3) == 0.2
    # never flattens -> last thin
    falling = [(0.0, 0.5), (0.1, 0.4), (0.2, 0.3)]
    assert validate.saturation_thin(falling) == 0.2


def test_validate_atlas_passes_all_claims(font):
    report = validate.validate_atlas(font, cap_px=34)
    assert report["coverage"] == 1.0
    assert report["monotone_thin"] is True
    assert report["monotone_weight"] is True
    assert report["eroded"] is True
    assert report["thickened"] is True
    assert report["cap_linear"] is True
    assert report["ok"] is True
    # cap height doubles with cap_px (M2 linearity)
    assert report["cap_height_2x"] == pytest.approx(
        2 * report["cap_height_px"], rel=0.06
    )


def test_validate_atlas_reports_saturation_within_range(font):
    report = validate.validate_atlas(font, cap_px=34)
    assert 0.0 <= report["saturation_thin"] <= 0.6
