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


def test_solve_recovers_an_achievable_target(font):
    # A density that a plateau achieves must be recovered exactly (argmin over
    # period plateaus; the returned thin lands on that plateau).
    target = calibrate.text_ink_density(font, "AB0", 40, 0.2)
    thin, achieved = calibrate.solve_thin_for_density(font, "AB0", 40, target)
    assert 0.0 <= thin <= calibrate.SATURATION_THIN
    assert achieved == pytest.approx(target, abs=1e-6)


def test_solve_denser_than_font_returns_least_erosion(font):
    # Target denser than the un-eroded font -> closest is thin 0.0.
    d0 = calibrate.text_ink_density(font, "AB", 40, 0.0)
    thin, achieved = calibrate.solve_thin_for_density(font, "AB", 40, d0 + 0.5)
    assert thin == 0.0
    assert achieved == pytest.approx(d0)


def test_solve_sparser_than_font_returns_max_erosion(font):
    # Target sparser than max erosion -> closest is the most-eroded plateau.
    d_max = min(d for _, d in calibrate.thin_response_curve(font, "AB", 40))
    thin, achieved = calibrate.solve_thin_for_density(font, "AB", 40, 0.0)
    assert achieved == pytest.approx(d_max)
    assert thin >= 0.4  # a period-2 plateau (saturated erosion)


def test_solve_is_robust_to_non_monotone_plateaus(font):
    # Any target -> the returned density is genuinely the closest achievable
    # over the enumerated plateaus (no monotonicity assumption).
    curve = calibrate.thin_response_curve(
        font, "AB0", 40, thins=calibrate._SOLVE_THINS
    )
    achievable = [d for _, d in curve]
    for target in (0.15, 0.3, 0.45):
        _, achieved = calibrate.solve_thin_for_density(font, "AB0", 40, target)
        assert achieved == pytest.approx(
            min(achievable, key=lambda d: abs(d - target))
        )


def test_effective_condense_gates_on_flag():
    # condense_glyphs OFF -> mask not narrowed (factor 1.0), even if condense<1;
    # ON -> the profile's condense passes through.
    assert calibrate.effective_condense(0.7, False) == 1.0
    assert calibrate.effective_condense(0.7, True) == pytest.approx(0.7)
    assert calibrate.effective_condense(1.0, True) == pytest.approx(1.0)


def test_solve_thins_reach_slight_erosion():
    # The plateau grid must reach well below 0.05 so a merchant needing a hair
    # of erosion isn't forced onto 0.0 or a coarse plateau.
    assert min(t for t in calibrate._SOLVE_THINS if t > 0) < 0.02
    assert 0.0 in calibrate._SOLVE_THINS


def test_condense_narrows_the_measured_mask(font):
    # condense_glyphs resizes the mask narrower along x (NEAREST) after
    # thinning, exactly as the renderer does before pasting -- the measurer
    # must count those pixels, so the mask width shrinks by ~condense.
    full = calibrate._rendered_glyph(font, "A", 40, 0.0, condense=1.0)
    cond = calibrate._rendered_glyph(font, "A", 40, 0.0, condense=0.7)
    assert cond.shape[1] == max(1, round(full.shape[1] * 0.7))
    assert cond.shape[0] == full.shape[0]  # height unchanged (x-only)


def test_glyph_coverage(font):
    assert calibrate.text_glyph_coverage(font, "AB0") == 1.0
    assert calibrate.text_glyph_coverage(font, "ABz") == pytest.approx(2 / 3)
    assert calibrate.text_glyph_coverage(font, "   ") == 1.0


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


# --- scorecard-faithful word filtering + per-word aggregation ---


def test_scorecard_words_drops_short_and_numeric_captions():
    words = [
        {"text": "AB"},  # kept
        {"text": "A"},  # dropped: < 2 non-space chars
        {"text": "  "},  # dropped: empty after strip
        {"text": "0123456789012345"},  # dropped: 16-digit numeric caption
        {"text": "AB0"},  # kept (only 1 digit)
    ]
    assert calibrate.scorecard_words(words) == ["AB", "AB0"]


def test_is_long_numeric_caption():
    assert calibrate.is_long_numeric_caption("01234567890123")  # 14 digits
    assert calibrate.is_long_numeric_caption("X0123456789 0123X")  # 14d/16 ok
    assert not calibrate.is_long_numeric_caption("0123456789")  # only 10
    assert not calibrate.is_long_numeric_caption("TOTAL 12.99")  # letters
    assert not calibrate.is_long_numeric_caption(
        "(012) 345-678901 [23]"
    )  # 14 digits but only 14/19 < 0.75 non-space -> not a caption


def test_median_word_density_is_not_dominated_by_one_long_token(font):
    # A char-weighted corpus mean lets one long dense token dominate; the
    # per-word median does not. 'A' (solid) is dense, '0' (sparse cross) light.
    words = ["AAAAAAAAAA", "0", "0"]  # one long dense word, two light words
    corpus = calibrate.text_ink_density(font, "".join(words), 40, 0.0)
    med = calibrate.median_word_density(font, words, 40, 0.0)
    # median sits on a light word; corpus mean is pulled up by the long 'A' run.
    assert med < corpus


def test_median_word_density_none_without_atlas_glyphs(font):
    assert calibrate.median_word_density(font, ["zz", "yy"], 40, 0.0) is None


def test_solve_thin_for_word_density_recovers_target(font):
    words = ["AB0", "AA0"]
    target = calibrate.median_word_density(font, words, 40, 0.2)
    thin, achieved = calibrate.solve_thin_for_word_density(
        font, words, 40, target
    )
    assert 0.0 <= thin <= calibrate.SATURATION_THIN
    assert achieved == pytest.approx(target, abs=1e-6)


def test_solve_thin_for_word_density_rejects_empty(font):
    with pytest.raises(ValueError):
        calibrate.solve_thin_for_word_density(font, ["zz"], 40, 0.2)


# --- M3: weight (stroke dilation) + joint weight/thin solve ---


def test_dilate_ink_grows_one_pixel_to_eight_neighbours():
    m = np.zeros((5, 5), bool)
    m[2, 2] = True
    d1 = calibrate.dilate_ink(m, 1)
    assert d1.sum() == 9  # center + 8 neighbours
    assert calibrate.dilate_ink(m, 0).sum() == 1  # identity
    assert calibrate.dilate_ink(m, 2).sum() == 25  # full 5x5


def test_weight_iters_raises_density(font):
    # The ring 'B' and sparse '0' have room to thicken; more weight -> more ink.
    d0 = calibrate.text_ink_density(font, "B0", 40, 0.0, weight_iters=0)
    d1 = calibrate.text_ink_density(font, "B0", 40, 0.0, weight_iters=1)
    d2 = calibrate.text_ink_density(font, "B0", 40, 0.0, weight_iters=2)
    assert d1 > d0
    assert d2 >= d1


def test_weight_iters_zero_is_unchanged(font):
    # Default weight_iters=0 must reproduce the pre-M3 measurement exactly.
    plain = calibrate.text_ink_density(font, "B0", 40, 0.2)
    zero = calibrate.text_ink_density(font, "B0", 40, 0.2, weight_iters=0)
    assert plain == zero


def _synthetic_density():
    # density_at(w, thin) = 0.30 + 0.10*w - 0.50*thin: monotone up in weight,
    # down in thin -- the shape the joint solver assumes.
    return lambda w, thin: 0.30 + 0.10 * w - 0.50 * thin


def test_solve_weight_and_thin_picks_least_weight_then_fine_tunes():
    dens = _synthetic_density()
    # un-eroded d0: w0=.30 w1=.40 w2=.50 w3=.60. target .45 reachable by w2,w3;
    # least is w2, then erode: t=0.1 gives .45 exactly.
    w, thin, ach = calibrate.solve_weight_and_thin(
        dens,
        0.45,
        weight_iters_options=(0, 1, 2, 3),
        thins=(0.0, 0.1, 0.2, 0.3),
    )
    assert w == 2
    assert thin == pytest.approx(0.1)
    assert ach == pytest.approx(0.45)


def test_solve_weight_and_thin_target_too_dense_uses_heaviest():
    dens = _synthetic_density()
    # target .99 exceeds every un-eroded density -> heaviest weight, least thin.
    w, thin, ach = calibrate.solve_weight_and_thin(
        dens,
        0.99,
        weight_iters_options=(0, 1, 2, 3),
        thins=(0.0, 0.1, 0.2, 0.3),
    )
    assert w == 3
    assert thin == pytest.approx(0.0)
    assert ach == pytest.approx(0.60)


def test_solve_weight_and_thin_lightest_already_dense_erodes_down():
    dens = _synthetic_density()
    # target .10 is below every un-eroded density -> least weight w0, erode to
    # the closest achievable (.15 at t=0.3 on this grid).
    w, thin, ach = calibrate.solve_weight_and_thin(
        dens,
        0.10,
        weight_iters_options=(0, 1, 2, 3),
        thins=(0.0, 0.1, 0.2, 0.3),
    )
    assert w == 0
    assert thin == pytest.approx(0.3)
    assert ach == pytest.approx(0.15)


def test_solve_weight_and_thin_raises_without_measurable_density():
    with pytest.raises(ValueError):
        calibrate.solve_weight_and_thin(lambda w, t: None, 0.3)


def test_weight_density_fn_solves_toward_target(font):
    words = ["B0", "AB0"]
    density_at = calibrate.weight_density_fn(font, words, 40)
    # A target between the un-eroded density and a lightly-eroded one is
    # reachable; the solve should land close and return valid knobs.
    target = calibrate.median_word_density(
        font, words, 40, 0.1, weight_iters=1
    )
    w, thin, ach = calibrate.solve_weight_and_thin(
        density_at, target, weight_iters_options=(0, 1, 2)
    )
    assert w in (0, 1, 2)
    assert 0.0 <= thin <= calibrate.SATURATION_THIN
    assert ach == pytest.approx(target, abs=0.05)


# --- M2: cap height ---


def test_cap_glyph_height_linear_in_cap_px(font):
    h40 = calibrate.cap_glyph_height(font, 40, 0.0)
    h80 = calibrate.cap_glyph_height(font, 80, 0.0)
    assert h40 is not None and h80 is not None
    assert h80 == pytest.approx(2 * h40, rel=0.05)


def test_cap_glyph_height_none_without_cap_refs(tmp_path):
    from receipt_agent.agents.label_evaluator.rendering.bitmap_font import (
        BitmapFont,
    )

    digit = np.ones((20, 20), np.uint8)
    p = tmp_path / "digits.glyphs.npz"
    np.savez_compressed(p, c48=digit, o48=0)  # only '0', no cap refs
    f = BitmapFont(str(p))
    assert calibrate.cap_glyph_height(f, 20, 0.0) is None


def test_solve_cap_ratio_hits_unit_h_ratio_when_unclamped(font):
    # slope=1 (40px cap glyphs, cap_h=40); real cap 30, ocr word box 40
    # -> ratio 0.75 (in band), projected h_ratio 1.0.
    ratio, h_ratio = calibrate.solve_cap_ratio(
        font, real_cap_height_px=30.0, median_ocr_word_height_px=40.0
    )
    assert ratio == pytest.approx(0.75, abs=1e-3)
    assert h_ratio == pytest.approx(1.0, abs=1e-3)


def test_solve_cap_ratio_clamps_to_band(font):
    # real cap == ocr word box -> unclamped ratio 1.0 -> clamped to 0.95,
    # so the projected h_ratio can no longer reach 1.0.
    ratio, h_ratio = calibrate.solve_cap_ratio(
        font, real_cap_height_px=40.0, median_ocr_word_height_px=40.0
    )
    assert ratio == pytest.approx(calibrate.CAP_RATIO_MAX)
    assert h_ratio == pytest.approx(0.95, abs=1e-3)


def test_solve_cap_ratio_rejects_bad_inputs(font):
    with pytest.raises(ValueError):
        calibrate.solve_cap_ratio(font, 30.0, 0.0)


def test_solve_cap_ratio_small_text_floor_binds(font):
    # slope=1; real cap 30, ocr word box 40 -> unclamped cap_px 30. A large
    # base_cap floors cap_px above 30, so the renderer stamps taller than the
    # linear solve promised and projected h_ratio rises above 1.0.
    _, h_ratio = calibrate.solve_cap_ratio(
        font,
        real_cap_height_px=30.0,
        median_ocr_word_height_px=40.0,
        font_px=50.0,
        bitmap_cap_ratio=0.8,  # base_cap=40 -> floor round(40*0.9)=36 > 30
    )
    # floored cap_px 36 -> synth 36 vs real 30
    assert h_ratio == pytest.approx(36.0 / 30.0, abs=1e-3)


def test_solve_cap_ratio_max_font_ceiling_binds(font):
    # Unclamped cap_px 30, but max_font_px caps it at 24 -> renderer stamps
    # shorter, projected h_ratio drops below 1.0.
    _, h_ratio = calibrate.solve_cap_ratio(
        font,
        real_cap_height_px=30.0,
        median_ocr_word_height_px=40.0,
        max_font_px=24.0,
    )
    assert h_ratio == pytest.approx(24.0 / 30.0, abs=1e-3)
