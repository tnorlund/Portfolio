"""Unit tests for glyphstudio.calibrate -- the render-free ink measurer.

Hermetic: builds a tiny synthetic atlas npz (no vault, no receipts) so the
density math is checked against glyphs whose ink is known by construction.
"""

import numpy as np
import pytest
from glyphstudio import calibrate


@pytest.fixture()
def font(tmp_path):
    """A 3-glyph atlas: a solid cap block, a ringed cap, and a sparse digit."""
    from receipt_agent.agents.label_evaluator.rendering.bitmap_font import (
        BitmapFont,
    )

    # 40x40 cap glyphs (cap-ref letters so cap_h resolves), 1 digit.
    solid = np.ones((40, 40), np.uint8)  # 'A' - fully solid
    ring = np.ones((40, 40), np.uint8)  # 'B' - hollow center
    ring[10:30, 10:30] = 0
    sparse = np.zeros((40, 40), np.uint8)  # '0' - thin cross
    sparse[18:22, :] = 1
    sparse[:, 18:22] = 1
    arrays = {"c65": solid, "c66": ring, "c48": sparse}
    offsets = {"o65": 0, "o66": 0, "o48": 0}
    p = tmp_path / "syn.glyphs.npz"
    np.savez_compressed(p, **{k: v for k, v in {**arrays, **offsets}.items()})
    return BitmapFont(str(p))


def test_density_monotonic_non_increasing(font):
    curve = calibrate.thin_response_curve(
        font, "AB0", cap_px=40, thins=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5)
    )
    densities = [d for _, d in curve]
    assert len(densities) == 6
    for a, b in zip(densities, densities[1:]):
        assert b <= a + 1e-9, f"density rose with thin: {densities}"
    assert densities[-1] < densities[0], "erosion had no effect at all"


def test_saturation_flat_beyond_bound(font):
    d_sat = calibrate.text_ink_density(
        font, "AB", 40, calibrate.SATURATION_THIN
    )
    d_beyond = calibrate.text_ink_density(font, "AB", 40, 0.9)
    assert d_sat == pytest.approx(d_beyond, abs=1e-9)


def test_solve_recovers_on_curve_target(font):
    # A density achievable mid-curve must be solved to within tol.
    target = calibrate.text_ink_density(font, "AB0", 40, 0.22)
    thin, achieved = calibrate.solve_thin_for_density(
        font, "AB0", 40, target, tol=1e-3
    )
    assert 0.0 <= thin <= calibrate.SATURATION_THIN
    assert achieved == pytest.approx(target, abs=2e-3)


def test_solve_clamps_low(font):
    # Target denser than the un-eroded font -> can't add ink -> return lo.
    d0 = calibrate.text_ink_density(font, "AB", 40, 0.0)
    thin, achieved = calibrate.solve_thin_for_density(font, "AB", 40, d0 + 0.5)
    assert thin == 0.0
    assert achieved == pytest.approx(d0)


def test_solve_clamps_high(font):
    # Target sparser than max erosion -> can't erode more -> return hi.
    d_hi = calibrate.text_ink_density(
        font, "AB", 40, calibrate.SATURATION_THIN
    )
    thin, achieved = calibrate.solve_thin_for_density(
        font, "AB", 40, d_hi - 0.5
    )
    assert thin == calibrate.SATURATION_THIN
    assert achieved == pytest.approx(d_hi)


def test_frequency_weighting(font):
    # Mostly-solid text is denser than mostly-sparse text at the same thin.
    dense = calibrate.text_ink_density(font, "AAAAA0", 40, 0.0)
    sparse = calibrate.text_ink_density(font, "0000A", 40, 0.0)
    assert dense > sparse


def test_missing_glyphs_return_none(font):
    assert calibrate.text_ink_density(font, "zzz", 40, 0.0) is None


def test_corpus_text_concatenates():
    words = [{"text": "AB"}, {"text": "0"}, {"text": None}]
    assert calibrate.corpus_text(words) == "AB0"
