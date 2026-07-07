from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from receipt_logo.receipt_fixture import inspect_receipt_fixture
from receipt_logo.vectorize import VectorizeOptions, vectorize_logo


def test_vectorize_logo_emits_path_layers(tmp_path: Path) -> None:
    source = tmp_path / "logo.png"
    image = Image.new("RGBA", (24, 16), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((2, 3, 10, 12), fill=(10, 120, 20, 255))
    draw.rectangle((13, 4, 21, 11), fill=(100, 190, 70, 255))
    image.save(source)

    result = vectorize_logo(
        source,
        VectorizeOptions(max_colors=2, simplify_tolerance=0.0),
    )

    assert result.width == 24
    assert result.height == 16
    assert len(result.layers) == 2
    assert "<path" in result.svg
    assert sum(layer.path_count for layer in result.layers) == 2


def test_inspect_receipt_fixture_subsequence(tmp_path: Path) -> None:
    fixture = tmp_path / "receipt.json"
    fixture.write_text(
        """
        {
          "merchant_name": "Sprouts Farmers Market",
          "tokens": ["TOTAL", "SPROUTS", "FARMERS", "MARKET", "CA"],
          "bboxes": [[0,0,1,1], [10,20,40,30], [12,31,32,41], [34,31,54,41], [0,0,1,1]]
        }
        """,
        encoding="utf-8",
    )

    match = inspect_receipt_fixture(fixture)

    assert match is not None
    assert match.bounds == (10, 20, 54, 41)
    assert match.tokens == ("SPROUTS", "FARMERS", "MARKET")
