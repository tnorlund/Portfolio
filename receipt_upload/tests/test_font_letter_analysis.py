"""Tests for letter-crop font/style embeddings."""

from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from receipt_upload.font_letter_analysis import (
    LetterImageSample,
    build_letter_image_samples,
    build_line_font_samples,
    cluster_letter_styles,
    cluster_line_font_styles,
    upsert_letter_samples_to_chroma,
)


def _box(x: float, y: float, width: float, height: float) -> dict[str, float]:
    return {"x": x, "y": y, "width": width, "height": height}


def _draw_a(
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
    middle = (left + right) // 2
    crossbar_y = (top + bottom) // 2
    draw.line(
        [middle, top + 2, left + 2, bottom - 2],
        fill="black",
        width=stroke_width,
    )
    draw.line(
        [middle, top + 2, right - 2, bottom - 2],
        fill="black",
        width=stroke_width,
    )
    draw.line(
        [left + 7, crossbar_y, right - 7, crossbar_y],
        fill="black",
        width=stroke_width,
    )


def _same_character_two_weight_image() -> tuple[Image.Image, list[object]]:
    image = Image.new("RGB", (800, 220), "white")
    draw = ImageDraw.Draw(image)
    letters = []
    letter_id = 1

    for line_id, y, stroke_width in ((1, 0.62, 1), (2, 0.28, 6)):
        for word_id in range(1, 11):
            box = _box(0.08 + (word_id - 1) * 0.08, y, 0.035, 0.16)
            _draw_a(
                draw,
                image_size=image.size,
                box=box,
                stroke_width=stroke_width,
            )
            letters.append(
                SimpleNamespace(
                    image_id="image-1",
                    receipt_id=1,
                    line_id=line_id,
                    word_id=word_id,
                    letter_id=letter_id,
                    text="A",
                    bounding_box=box,
                    confidence=0.99,
                    angle_radians=0.0,
                )
            )
            letter_id += 1

    return image, letters


def _same_character_four_line_weight_image() -> (
    tuple[Image.Image, list[object]]
):
    image = Image.new("RGB", (800, 440), "white")
    draw = ImageDraw.Draw(image)
    letters = []
    letter_id = 1

    rows = (
        (1, 0.78, 1),
        (2, 0.56, 1),
        (3, 0.34, 6),
        (4, 0.12, 6),
    )
    for line_id, y, stroke_width in rows:
        for word_id in range(1, 11):
            box = _box(0.08 + (word_id - 1) * 0.08, y, 0.035, 0.10)
            _draw_a(
                draw,
                image_size=image.size,
                box=box,
                stroke_width=stroke_width,
            )
            letters.append(
                SimpleNamespace(
                    image_id="image-1",
                    receipt_id=1,
                    line_id=line_id,
                    word_id=word_id,
                    letter_id=letter_id,
                    text="A",
                    bounding_box=box,
                    confidence=0.99,
                    angle_radians=0.0,
                )
            )
            letter_id += 1

    return image, letters


@pytest.mark.unit
def test_letter_style_clustering_separates_same_ocr_char_by_weight():
    image, letters = _same_character_two_weight_image()

    samples = build_letter_image_samples(letters, raw_image=image)
    analysis = cluster_letter_styles(samples, eps=0.35, min_samples=2)

    assert len(samples) == 20
    assert len(analysis.clusters) == 2
    assert not analysis.noise_sample_ids

    line_clusters = {}
    for sample in samples:
        line_clusters.setdefault(sample.line_id, set()).add(
            analysis.cluster_for_sample(sample.sample_id)
        )

    assert len(line_clusters[1]) == 1
    assert len(line_clusters[2]) == 1
    assert line_clusters[1] != line_clusters[2]

    thin = samples[0]
    thick = samples[-1]
    assert len(thin.metrics) >= 60
    assert len(thin.vector) > 300
    assert len(thin.style_vector) > len(thin.vector)
    assert "hog_orientation_0" in thin.metrics
    assert "row_projection_3" in thin.metrics
    assert "column_projection_3" in thin.metrics
    assert "top_profile_mean" in thin.metrics
    assert "component_count" in thin.metrics
    assert "hole_count" in thin.metrics
    assert (
        thick.metrics["stroke_width_mean_norm"]
        > thin.metrics["stroke_width_mean_norm"]
    )


@pytest.mark.unit
def test_line_style_clustering_aggregates_letter_features_by_line():
    image, letters = _same_character_four_line_weight_image()

    samples = build_letter_image_samples(letters, raw_image=image)
    letter_analysis = cluster_letter_styles(samples, eps=0.35, min_samples=2)
    line_samples = build_line_font_samples(
        samples,
        letter_assignments=letter_analysis.assignments,
    )
    line_analysis = cluster_line_font_styles(
        line_samples,
        eps=0.75,
        min_samples=2,
    )

    assert len(line_samples) == 4
    assert len(line_analysis.clusters) == 2
    assert not line_analysis.noise_sample_ids

    cluster_by_line = {
        sample.line_id: line_analysis.cluster_for_sample(sample.sample_id)
        for sample in line_samples
    }
    assert cluster_by_line[1] == cluster_by_line[2]
    assert cluster_by_line[3] == cluster_by_line[4]
    assert cluster_by_line[1] != cluster_by_line[3]

    thin_line = line_samples[0]
    thick_line = line_samples[-1]
    assert len(thin_line.metrics) >= 200
    assert len(thin_line.vector) > 800
    assert "median_box_height" in thin_line.metrics
    assert "mean_stroke_width_mean_norm" in thin_line.metrics
    assert "dominant_letter_cluster_ratio" in thin_line.metrics
    assert "uppercase_ratio" in thin_line.metrics
    assert (
        thick_line.metrics["mean_stroke_width_mean_norm"]
        > thin_line.metrics["mean_stroke_width_mean_norm"]
    )


@pytest.mark.unit
def test_letter_embeddings_skip_low_confidence_letters():
    image, letters = _same_character_two_weight_image()
    letters[0].confidence = 0.1

    samples = build_letter_image_samples(
        letters,
        raw_image=image,
        min_confidence=0.35,
    )

    assert len(samples) == 19
    assert all(sample.metrics["confidence"] >= 0.35 for sample in samples)


@pytest.mark.unit
def test_chroma_upsert_preserves_existing_rows_by_default(
    tmp_path, monkeypatch
):
    class FakeCollection:
        def __init__(self) -> None:
            self.ids = ["existing"]
            self.deleted: list[str] = []

        def count(self) -> int:
            return len(self.ids)

        def get(self, include=None):
            return {"ids": list(self.ids)}

        def delete(self, ids):
            self.deleted.extend(ids)
            self.ids = [value for value in self.ids if value not in ids]

    class FakeClient:
        collection = FakeCollection()

        def __init__(self, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            pass

        def get_collection(self, *args, **kwargs):
            return self.collection

        def upsert(self, *, ids, **kwargs) -> None:
            self.collection.ids.extend(ids)

        def count(self, collection_name: str) -> int:
            return self.collection.count()

    import receipt_chroma

    monkeypatch.setattr(receipt_chroma, "ChromaClient", FakeClient)
    sample = LetterImageSample(
        sample_id="new",
        image_id="image-1",
        receipt_id=1,
        line_id=1,
        word_id=1,
        letter_id=1,
        text="A",
        normalized_char="A",
        vector=(0.1, 0.2),
        style_vector=(0.1, 0.2),
        metrics={"confidence": 0.99},
    )

    count = upsert_letter_samples_to_chroma(
        [sample],
        persist_directory=str(tmp_path),
    )

    assert count == 2
    assert FakeClient.collection.ids == ["existing", "new"]
    assert FakeClient.collection.deleted == []
