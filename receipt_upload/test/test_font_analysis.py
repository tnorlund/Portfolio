"""Tests for receipt font-style embedding and clustering."""

from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from receipt_upload.font_analysis import analyze_receipt_fonts


def _box(x: float, y: float, width: float, height: float) -> dict[str, float]:
    return {"x": x, "y": y, "width": width, "height": height}


def _word_with_letters(
    *,
    text: str,
    line_id: int,
    word_id: int,
    x: float,
    y: float,
    glyph_width: float,
    height: float,
    image_id: str = "image-1",
    receipt_id: int = 1,
) -> tuple[SimpleNamespace, list[SimpleNamespace]]:
    word_width = glyph_width * len(text)
    word = SimpleNamespace(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box=_box(x, y, word_width, height),
        confidence=0.96,
        angle_radians=0.0,
    )
    letters = []
    for index, character in enumerate(text, start=1):
        letter_x = x + (index - 1) * glyph_width
        letters.append(
            SimpleNamespace(
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=line_id,
                word_id=word_id,
                letter_id=index,
                text=character,
                bounding_box=_box(letter_x, y, glyph_width, height),
                confidence=0.96,
                angle_radians=0.0,
            )
        )
    return word, letters


def _line(
    *,
    line_id: int,
    text: str,
    x: float,
    y: float,
    width: float,
    height: float,
    image_id: str = "image-1",
    receipt_id: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        text=text,
        bounding_box=_box(x, y, width, height),
        confidence=0.96,
        angle_radians=0.0,
    )


def _two_style_receipt() -> tuple[
    list[SimpleNamespace],
    list[SimpleNamespace],
    list[SimpleNamespace],
]:
    lines = [
        _line(
            line_id=1,
            text="STORE TOTAL",
            x=0.1,
            y=0.82,
            width=0.4,
            height=0.06,
        ),
        _line(
            line_id=2,
            text="milk bread",
            x=0.1,
            y=0.62,
            width=0.25,
            height=0.026,
        ),
        _line(
            line_id=3,
            text="apple banana",
            x=0.1,
            y=0.57,
            width=0.3,
            height=0.026,
        ),
    ]

    words = []
    letters = []
    for args in (
        {
            "text": "STORE",
            "line_id": 1,
            "word_id": 1,
            "x": 0.1,
            "y": 0.82,
            "glyph_width": 0.026,
            "height": 0.052,
        },
        {
            "text": "TOTAL",
            "line_id": 1,
            "word_id": 2,
            "x": 0.26,
            "y": 0.82,
            "glyph_width": 0.026,
            "height": 0.052,
        },
        {
            "text": "milk",
            "line_id": 2,
            "word_id": 1,
            "x": 0.1,
            "y": 0.62,
            "glyph_width": 0.012,
            "height": 0.022,
        },
        {
            "text": "bread",
            "line_id": 2,
            "word_id": 2,
            "x": 0.17,
            "y": 0.62,
            "glyph_width": 0.012,
            "height": 0.022,
        },
        {
            "text": "apple",
            "line_id": 3,
            "word_id": 1,
            "x": 0.1,
            "y": 0.57,
            "glyph_width": 0.012,
            "height": 0.022,
        },
        {
            "text": "banana",
            "line_id": 3,
            "word_id": 2,
            "x": 0.17,
            "y": 0.57,
            "glyph_width": 0.012,
            "height": 0.022,
        },
    ):
        word, word_letters = _word_with_letters(**args)
        words.append(word)
        letters.extend(word_letters)

    return lines, words, letters


def _draw_word_style(
    draw: ImageDraw.ImageDraw,
    *,
    image_size: tuple[int, int],
    box: dict[str, float],
    stroke_width: int,
) -> None:
    image_width, image_height = image_size
    left = int(box["x"] * image_width)
    right = int((box["x"] + box["width"]) * image_width)
    top = int((1.0 - box["y"] - box["height"]) * image_height)
    bottom = int((1.0 - box["y"]) * image_height)
    glyph_width = max((right - left) // 4, 1)
    for glyph_index in range(4):
        glyph_left = left + glyph_index * glyph_width + 3
        glyph_right = left + (glyph_index + 1) * glyph_width - 3
        draw.rectangle(
            [glyph_left, top + 3, glyph_right, bottom - 3],
            outline="black",
            width=stroke_width,
        )
        draw.line(
            [
                (glyph_left + glyph_right) // 2,
                top + 4,
                (glyph_left + glyph_right) // 2,
                bottom - 4,
            ],
            fill="black",
            width=stroke_width,
        )


def _same_geometry_pixel_receipt() -> tuple[
    Image.Image,
    list[SimpleNamespace],
    list[SimpleNamespace],
    list[SimpleNamespace],
]:
    image = Image.new("RGB", (480, 220), "white")
    draw = ImageDraw.Draw(image)
    lines = [
        _line(
            line_id=1,
            text="THIN LINE",
            x=0.1,
            y=0.68,
            width=0.55,
            height=0.12,
        ),
        _line(
            line_id=2,
            text="THICK LINE",
            x=0.1,
            y=0.42,
            width=0.55,
            height=0.12,
        ),
    ]

    words = []
    letters = []
    for args in (
        {
            "text": "thin",
            "line_id": 1,
            "word_id": 1,
            "x": 0.12,
            "y": 0.69,
            "glyph_width": 0.045,
            "height": 0.09,
            "stroke_width": 1,
        },
        {
            "text": "fine",
            "line_id": 1,
            "word_id": 2,
            "x": 0.34,
            "y": 0.69,
            "glyph_width": 0.045,
            "height": 0.09,
            "stroke_width": 1,
        },
        {
            "text": "bold",
            "line_id": 2,
            "word_id": 1,
            "x": 0.12,
            "y": 0.43,
            "glyph_width": 0.045,
            "height": 0.09,
            "stroke_width": 6,
        },
        {
            "text": "dark",
            "line_id": 2,
            "word_id": 2,
            "x": 0.34,
            "y": 0.43,
            "glyph_width": 0.045,
            "height": 0.09,
            "stroke_width": 6,
        },
    ):
        stroke_width = args.pop("stroke_width")
        word, word_letters = _word_with_letters(**args)
        _draw_word_style(
            draw,
            image_size=image.size,
            box=word.bounding_box,
            stroke_width=stroke_width,
        )
        words.append(word)
        letters.extend(word_letters)

    return image, lines, words, letters


@pytest.mark.unit
def test_analyze_receipt_fonts_clusters_distinct_styles():
    lines, words, letters = _two_style_receipt()

    analysis = analyze_receipt_fonts(
        letters,
        words=words,
        lines=lines,
        promote_singletons_min_letters=None,
    )

    assert len(analysis.clusters) == 2

    samples = {sample.text: sample for sample in analysis.samples}
    store_cluster = analysis.cluster_for_sample(samples["STORE"].sample_id)
    total_cluster = analysis.cluster_for_sample(samples["TOTAL"].sample_id)
    milk_cluster = analysis.cluster_for_sample(samples["milk"].sample_id)
    bread_cluster = analysis.cluster_for_sample(samples["bread"].sample_id)

    assert store_cluster == total_cluster
    assert milk_cluster == bread_cluster
    assert store_cluster != milk_cluster
    assert not analysis.noise_sample_ids

    clusters = {cluster.cluster_id: cluster for cluster in analysis.clusters}
    assert clusters[store_cluster].label.startswith("large")
    assert (
        clusters[store_cluster].metrics["font_size_ratio"]
        > clusters[milk_cluster].metrics["font_size_ratio"]
    )


@pytest.mark.unit
def test_similar_samples_prefers_same_font_cluster():
    lines, words, letters = _two_style_receipt()
    analysis = analyze_receipt_fonts(
        letters,
        words=words,
        lines=lines,
        promote_singletons_min_letters=None,
    )

    samples = {sample.text: sample for sample in analysis.samples}
    matches = analysis.similar_samples(samples["STORE"].sample_id, top_k=2)

    assert matches[0].sample_id == samples["TOTAL"].sample_id
    assert matches[0].cluster_id == analysis.cluster_for_sample(
        samples["STORE"].sample_id
    )


@pytest.mark.unit
def test_short_letter_groups_are_filtered_from_font_samples():
    lines, words, letters = _two_style_receipt()
    word, word_letters = _word_with_letters(
        text="X",
        line_id=4,
        word_id=1,
        x=0.1,
        y=0.5,
        glyph_width=0.02,
        height=0.04,
    )
    lines.append(
        _line(
            line_id=4,
            text="X",
            x=0.1,
            y=0.5,
            width=0.02,
            height=0.04,
        )
    )
    words.append(word)
    letters.extend(word_letters)

    analysis = analyze_receipt_fonts(
        letters,
        words=words,
        lines=lines,
        promote_singletons_min_letters=None,
    )

    assert "X" not in {sample.text for sample in analysis.samples}


@pytest.mark.unit
def test_raw_image_pixels_split_same_geometry_font_styles():
    image, lines, words, letters = _same_geometry_pixel_receipt()

    geometry_only = analyze_receipt_fonts(
        letters,
        words=words,
        lines=lines,
        eps=0.75,
        promote_singletons_min_letters=None,
    )
    pixel_analysis = analyze_receipt_fonts(
        letters,
        words=words,
        lines=lines,
        raw_image=image,
        eps=1.2,
        promote_singletons_min_letters=None,
    )

    assert len(geometry_only.clusters) == 1
    assert len(pixel_analysis.clusters) == 2

    samples = {sample.text: sample for sample in pixel_analysis.samples}
    thin_cluster = pixel_analysis.cluster_for_sample(samples["thin"].sample_id)
    fine_cluster = pixel_analysis.cluster_for_sample(samples["fine"].sample_id)
    bold_cluster = pixel_analysis.cluster_for_sample(samples["bold"].sample_id)
    dark_cluster = pixel_analysis.cluster_for_sample(samples["dark"].sample_id)

    assert thin_cluster == fine_cluster
    assert bold_cluster == dark_cluster
    assert thin_cluster != bold_cluster

    clusters = {
        cluster.cluster_id: cluster for cluster in pixel_analysis.clusters
    }
    assert (
        clusters[bold_cluster].metrics["pixel_ink_ratio"]
        > clusters[thin_cluster].metrics["pixel_ink_ratio"]
    )
