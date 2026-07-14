"""Known-answer / property tests for glyphstudio.degrade (I1 chain)."""

import numpy as np
import pytest

from glyphstudio.degrade import PARAM_BOUNDS, PARAM_ORDER, degrade


def _mid_params(**over):
    p = {k: 0.5 * (lo + hi) for k, (lo, hi) in PARAM_BOUNDS.items()}
    p.update(over)
    return p


def _neutral(**over):
    """Chain configured as close to identity as its bounds allow."""
    p = {
        "jitter_sigma": 0.0, "jitter_corr": 1.0,
        "gain_sx": 0.05, "gain_sy": 0.05, "gain_gamma": 1.0,
        "trc_mid": 0.5, "trc_slope": 40.0, "d_max": 1.0,
        "mottle_sigma": 0.0, "mottle_scale": 1.0,
        "k_a0": 0.0, "k_decay": 1.0, "k_b0": 0.0, "k_eta": 0.0,
        "psf_sigma": 0.2, "sensor_sigma": 0.0,
        "white_point": 255.0,
    }
    p.update(over)
    return p


def _glyph_image():
    """A hard-edged synthetic 'glyph' page: black bar on light paper."""
    g = np.full((120, 90), 246.0)
    g[40:70, 20:70] = 0.0
    return g


def test_param_order_matches_bounds():
    assert PARAM_ORDER == list(PARAM_BOUNDS)


def test_missing_param_raises():
    p = _mid_params()
    p.pop("psf_sigma")
    with pytest.raises(ValueError, match="psf_sigma"):
        degrade(_glyph_image(), p)


def test_deterministic_for_params_and_seed():
    g = _glyph_image()
    p = _mid_params()
    a = degrade(g, p, seed=7)
    b = degrade(g, p, seed=7)
    assert np.array_equal(a, b)


def test_seed_changes_noise_draw():
    g = _glyph_image()
    p = _mid_params(mottle_sigma=0.4, sensor_sigma=6.0)
    a = degrade(g, p, seed=0)
    b = degrade(g, p, seed=1)
    assert not np.array_equal(a, b)


def test_output_range_and_shape():
    g = _glyph_image()
    out = degrade(g, _mid_params(), seed=0)
    assert out.shape == g.shape
    assert out.min() >= 0.0 and out.max() <= 255.0


def test_near_neutral_chain_preserves_geometry():
    """With spread/noise off, ink stays ink and paper stays paper."""
    g = _glyph_image()
    out = degrade(g, _neutral(), seed=0)
    assert out[55, 45] < 64.0  # stroke core still dark
    assert out[10, 10] > 200.0  # paper still light


def test_d_max_sets_ink_floor():
    """d_max < 1 lifts the darkest attainable level off the black floor
    (the 'render ink 6x too dark' fix)."""
    g = _glyph_image()
    out = degrade(g, _neutral(d_max=0.72), seed=0)
    core = out[45:65, 30:60]
    assert core.min() >= 255.0 * (1.0 - 0.72) - 1.0


def test_dot_gain_adds_ink():
    g = _glyph_image()
    thin = degrade(g, _neutral(), seed=0)
    fat = degrade(g, _neutral(gain_sx=1.5, gain_sy=1.5, gain_gamma=2.0,
                              trc_mid=0.2, trc_slope=8.0), seed=0)
    assert (255.0 - fat).sum() > (255.0 - thin).sum()


def test_psf_softens_edges():
    g = _glyph_image()
    crisp = degrade(g, _neutral(), seed=0)
    soft = degrade(g, _neutral(psf_sigma=2.0), seed=0)

    def midtone_frac(im):
        return ((im >= 64.0) & (im <= 192.0)).mean()

    assert midtone_frac(soft) > midtone_frac(crisp)


def test_white_point_clips_paper():
    g = _glyph_image()  # paper at 246
    # gentle TRC (slope 2) so paper texture survives to the levels stage
    unclipped = degrade(g, _neutral(trc_slope=2.0, white_point=255.0), seed=0)
    clipped = degrade(g, _neutral(trc_slope=2.0, white_point=245.0), seed=0)
    assert (unclipped >= 254.5).mean() < 0.01
    assert (clipped >= 254.5).mean() > 0.5


def test_mottle_raises_stroke_cov():
    g = _glyph_image()
    p_flat = _neutral(d_max=0.72)
    p_mot = _neutral(d_max=0.72, mottle_sigma=0.35, mottle_scale=1.5)
    core = (slice(45, 65), slice(30, 60))
    flat = degrade(g, p_flat, seed=0)[core]
    mot = degrade(g, p_mot, seed=0)[core]
    assert mot.std() > flat.std() + 1.0


def test_kanungo_dropout_lightens_edges():
    g = _glyph_image()
    base = degrade(g, _neutral(d_max=1.0), seed=0)
    flip = degrade(g, _neutral(d_max=1.0, k_a0=0.9, k_decay=0.3), seed=0)
    # dropout only removes ink -> image can only get lighter
    assert flip.sum() > base.sum()


def test_values_clamped_to_bounds():
    g = _glyph_image()
    p = _mid_params(sensor_sigma=1e9, white_point=-5.0)
    out = degrade(g, p, seed=0)  # must not explode
    assert np.isfinite(out).all()
