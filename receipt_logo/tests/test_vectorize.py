from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from receipt_logo.exceptions import (
    EmptyLogoError,
    InvalidAssetSlugError,
    LogoAssetWriteError,
    LogoSourceError,
    LogoVectorizationError,
    PaletteExtractionError,
)
from receipt_logo.receipt_fixture import inspect_receipt_fixture
from receipt_logo.vectorize import (
    VectorizeOptions,
    vectorize_logo,
    write_vector_asset,
)


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


def test_vectorize_logo_wraps_unreadable_source(tmp_path: Path) -> None:
    source = tmp_path / "missing.png"

    with pytest.raises(LogoSourceError) as raised:
        vectorize_logo(source)

    assert str(raised.value) == f"Unable to read logo source {source}"
    assert isinstance(raised.value.__cause__, FileNotFoundError)


def test_vectorize_logo_rejects_fully_transparent_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "transparent.png"
    Image.new("RGBA", (4, 3), (0, 0, 0, 0)).save(source)

    with pytest.raises(EmptyLogoError) as raised:
        vectorize_logo(source, VectorizeOptions(alpha_threshold=20))

    assert str(raised.value) == (
        f"Logo source {source} has no pixels above alpha threshold 20"
    )
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    "error_type",
    [EmptyLogoError, PaletteExtractionError],
)
def test_vectorization_validation_errors_retain_value_error(
    error_type,
) -> None:
    error = error_type("invalid logo")

    assert isinstance(error, LogoVectorizationError)
    assert isinstance(error, ValueError)


def test_write_vector_asset_rejects_unusable_slug(tmp_path: Path) -> None:
    source = tmp_path / "logo.png"
    Image.new("RGBA", (3, 3), (10, 20, 30, 255)).save(source)
    result = vectorize_logo(
        source,
        VectorizeOptions(min_contour_area=0, simplify_tolerance=0),
    )

    with pytest.raises(InvalidAssetSlugError) as raised:
        write_vector_asset(result, tmp_path / "out", "...")

    assert str(raised.value) == "Unusable logo asset slug: '...'"
    assert raised.value.__cause__ is None


def test_write_vector_asset_wraps_filesystem_failure(tmp_path: Path) -> None:
    source = tmp_path / "logo.png"
    Image.new("RGBA", (3, 3), (10, 20, 30, 255)).save(source)
    result = vectorize_logo(
        source,
        VectorizeOptions(min_contour_area=0, simplify_tolerance=0),
    )
    output = tmp_path / "blocked"
    output.write_text("not a directory", encoding="utf-8")

    with pytest.raises(LogoAssetWriteError) as raised:
        write_vector_asset(result, output, "merchant")

    assert str(raised.value) == (
        f"Unable to write logo assets for slug 'merchant' to {output}"
    )
    assert isinstance(raised.value.__cause__, FileExistsError)


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
