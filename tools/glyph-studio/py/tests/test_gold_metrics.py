"""Known-answer tests for the gold-standard metric library.

Every metric is exercised on a synthetic fixture whose correct value is
computable by hand, so a wiring regression in ``gold_metrics`` fails here
before it corrupts a scorecard.
"""

import numpy as np
import pytest

from glyphstudio import gold_metrics as gm


# --- L0 --------------------------------------------------------------------


def test_ink_ratio_identical_is_one():
    img = np.full((20, 20), 200.0)
    img[5:10, 5:10] = 0.0
    assert gm.ink_ratio(img, img) == pytest.approx(1.0)


def test_ink_ratio_double_ink():
    real = np.full((10, 10), 255.0)
    real[0, 0] = 255.0 - 10.0  # ink sum 10
    render = np.full((10, 10), 255.0)
    render[0, 0] = 255.0 - 20.0  # ink sum 20
    assert gm.ink_ratio(real, render) == pytest.approx(2.0)


def test_normalized_fill_median():
    m1 = np.zeros((4, 4), bool)
    m1[:2] = True  # fill 0.5
    m2 = np.zeros((4, 4), bool)
    m2[0] = True  # fill 0.25
    m3 = np.ones((4, 4), bool)  # fill 1.0
    assert gm.normalized_fill([m1, m2, m3]) == pytest.approx(0.5)


def test_cap_height_and_pitch_bands():
    # three inked rows-bands of height 4, spaced 10 px center-to-center
    g = np.full((40, 20), 255.0)
    for top in (2, 12, 22):
        g[top:top + 4, :] = 0.0
    assert gm.cap_height_px(g, 0, 40) == pytest.approx(4.0)
    assert gm.line_pitch_px(g, 0, 40) == pytest.approx(10.0)


def test_line_pitch_autocorr_and_duty():
    # periodic rows: pitch 10, each line 4 rows inked -> duty ~0.4
    g = np.full((200, 20), 255.0)
    for c in range(0, 200, 10):
        g[c:c + 4, :] = 0.0
    assert gm.line_pitch_autocorr(g, 0, 200, lo=5, hi=30) == pytest.approx(10.0, abs=0.2)
    duty = gm.interline_duty(g, 0, 200, frac=0.5)
    assert 0.3 < duty < 0.5


def test_char_advance_autocorr():
    # vertical ink stripes every 6 px across many rows -> advance 6
    g = np.full((30, 120), 255.0)
    for c in range(0, 120, 6):
        g[:, c:c + 2] = 0.0
    assert gm.char_advance_autocorr(g, 0, 30, lo=3, hi=15) == pytest.approx(6.0, abs=0.2)


# --- L2 --------------------------------------------------------------------


def test_chamfer_zero_on_identical():
    m = np.zeros((10, 10), bool)
    m[3:7, 3:7] = True
    assert gm.chamfer_distance(m, m) == pytest.approx(0.0)


def test_chamfer_shift_by_one():
    a = np.zeros((10, 10), bool)
    a[5, 3:7] = True
    b = np.zeros((10, 10), bool)
    b[6, 3:7] = True  # shifted down 1 row
    # every A pixel is distance 1 from B and vice-versa
    assert gm.chamfer_distance(a, b) == pytest.approx(1.0)


def test_boundary_iou_identical_is_one():
    m = np.zeros((16, 16), bool)
    m[4:12, 4:12] = True
    assert gm.boundary_iou(m, m, band=2) == pytest.approx(1.0)


def test_boundary_iou_disjoint_is_zero():
    a = np.zeros((16, 16), bool)
    a[1:4, 1:4] = True
    b = np.zeros((16, 16), bool)
    b[12:15, 12:15] = True
    assert gm.boundary_iou(a, b, band=1) == pytest.approx(0.0)


def test_advance_width_px():
    # only the >=4-char alnum token counts: 40px / 5 chars = 8
    boxes = [(40.0, "HELLO"), (10.0, "hi"), (99.0, "a.b")]
    assert gm.advance_width_px(boxes) == pytest.approx(8.0)


def test_right_edge_std_and_caption_ratio():
    assert gm.right_edge_std([100.0, 100.0, 100.0]) == pytest.approx(0.0)
    assert gm.right_edge_std([98.0, 102.0]) == pytest.approx(2.0)
    assert gm.caption_height_ratio(30.0, 50.0) == pytest.approx(0.6)


# --- L3 --------------------------------------------------------------------


def test_stroke_core_stats_uniform():
    g = np.full((20, 20), 255.0)
    g[5:15, 5:15] = 100.0  # a solid block, ink core
    mask = g < 200
    s = gm.stroke_core_stats(g, mask, erode=1)
    assert s["median_L"] == pytest.approx(100.0)
    assert s["std"] == pytest.approx(0.0)
    assert s["cov"] == pytest.approx(0.0)


def test_edge_transition_frac():
    g = np.array([[0.0, 128.0, 255.0, 128.0]])
    # two of four pixels lie in [64,192]
    assert gm.edge_transition_frac(g) == pytest.approx(0.5)


def test_paper_clip_frac():
    g = np.array([[255.0, 255.0, 200.0, 100.0]])
    assert gm.paper_clip_frac(g) == pytest.approx(0.5)


def test_tone_emd_shift():
    # two delta histograms 100 gray-levels apart -> EMD ~100
    real = np.full((50, 50), 50.0)
    render = np.full((50, 50), 150.0)
    assert gm.tone_emd(real, render) == pytest.approx(100.0, abs=3.0)


def test_kanungo_pattern_hist_all_background():
    b = np.zeros((5, 5), bool)
    h = gm.kanungo_pattern_hist(b)
    assert h.sum() == pytest.approx(1.0)
    assert h[0] == pytest.approx(1.0)  # code 0 = all-background 3x3


def test_kanungo_two_sample_same_dist_fails_to_reject():
    rng = np.random.default_rng(1)
    crops = [rng.random((16, 16)) < 0.3 for _ in range(12)]
    a = crops[:6]
    b = crops[6:]
    res = gm.kanungo_two_sample(a, b, n_perm=100)
    # drawn from the same process -> should not reject (p not tiny)
    assert res["p_value"] > 0.05


def test_kanungo_two_sample_different_dist_rejects():
    rng = np.random.default_rng(2)
    a = [rng.random((16, 16)) < 0.1 for _ in range(8)]   # sparse
    b = [rng.random((16, 16)) < 0.6 for _ in range(8)]   # dense
    res = gm.kanungo_two_sample(a, b, n_perm=100)
    assert res["distance"] > 0.2
    assert res["p_value"] < 0.05


# --- L4 --------------------------------------------------------------------


def test_ms_ssim_identical_is_one():
    rng = np.random.default_rng(3)
    img = rng.random((64, 64)) * 255.0
    assert gm.ms_ssim(img, img) == pytest.approx(1.0, abs=1e-6)


def test_ms_ssim_ordering():
    rng = np.random.default_rng(4)
    img = rng.random((64, 64)) * 255.0
    near = img + rng.normal(0, 5, img.shape)
    far = rng.random((64, 64)) * 255.0
    assert gm.ms_ssim(img, near) > gm.ms_ssim(img, far)


def test_ocr_error_sets_parity():
    gt = ["THE", "QUICK", "BROWN", "FOX"]
    # real mis-reads QUICK; render mis-reads QUICK (shared) AND FOX (render-only)
    def lines(tokens):
        return [{"text": t, "x": 0.0, "y": float(i * 30), "h": 10.0}
                for i, t in enumerate(tokens)]
    real = lines(["THE", "QUISK", "BROWN", "FOX"])
    render = lines(["THE", "QUISK", "BROWN", "F0X"])
    res = gm.ocr_error_sets(gt, real, render)
    assert "QUICK" in res["shared"]
    assert "FOX" in res["render_only"]
    assert res["jaccard"] == pytest.approx(0.5)


def test_char_error_rate():
    assert gm.char_error_rate("HELLO", "HELLO") == pytest.approx(0.0)
    # one substitution in a 5-char ref -> 0.2
    assert gm.char_error_rate("HELLO", "HELLP") == pytest.approx(0.2)
    # one deletion in a 4-char ref -> 0.25
    assert gm.char_error_rate("ABCD", "ABD") == pytest.approx(0.25)


def test_ms_ssim_no_nan_on_anticorrelated():
    # anti-correlated images drive SSIM negative; must not return NaN
    a = np.indices((32, 32)).sum(0) * 4.0
    b = 255.0 - a
    v = gm.ms_ssim(a, b)
    assert np.isfinite(v)


def test_glcm_contrast_flat_is_zero():
    g = np.full((20, 20), 100.0)
    assert gm.glcm_contrast(g) == pytest.approx(0.0)


def test_glcm_contrast_checkerboard_positive():
    g = np.indices((20, 20)).sum(0) % 2 * 255.0
    assert gm.glcm_contrast(g) > 0.0


def test_spacing_jitter_uniform_is_zero():
    lines = [[0.0, 10.0, 20.0, 30.0]]
    assert gm.spacing_jitter(lines) == pytest.approx(0.0)


def test_spacing_jitter_positive_on_variation():
    lines = [[0.0, 10.0, 25.0, 30.0]]
    assert gm.spacing_jitter(lines) > 0.0


# --- alignment -------------------------------------------------------------


def test_ocr_anchors_and_warp_identity():
    lines = [{"text": f"WORD{i:02d}", "x": 0.0, "y": float(i * 50), "h": 10.0}
             for i in range(6)]
    anchors = gm.ocr_anchors(lines, lines)
    assert len(anchors) == 6
    # real_cy == render_cy on identical inputs
    for rcy, fcy, sim in anchors:
        assert rcy == pytest.approx(fcy)
        assert sim == pytest.approx(1.0)
    g = np.random.default_rng(5).random((300, 40)) * 255.0
    warped, resid = gm.piecewise_row_warp(g, anchors, 300)
    assert resid == pytest.approx(0.0, abs=1e-6)
    assert np.allclose(warped, g, atol=1e-6)
