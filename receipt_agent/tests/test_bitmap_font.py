import numpy as np

from receipt_agent.agents.label_evaluator.rendering.bitmap_font import (
    BitmapFont,
)


def test_baseline_variation_is_opt_in_and_keeps_glyph_shape(tmp_path):
    path = tmp_path / "atlas.npz"
    np.savez(
        path,
        c65=np.ones((10, 6), dtype=np.uint8),
        o65=np.asarray(0, dtype=np.int16),
    )

    plain = BitmapFont(str(path))
    varied = BitmapFont(str(path), baseline_variation=1)
    plain_glyph, plain_height, plain_offset = plain.glyph("A", 10)
    varied_glyph, varied_height, varied_offset = varied.glyph("A", 10)

    assert plain_height == varied_height
    assert plain_offset == 0
    assert varied_offset == 1
    assert np.array_equal(np.asarray(plain_glyph), np.asarray(varied_glyph))
