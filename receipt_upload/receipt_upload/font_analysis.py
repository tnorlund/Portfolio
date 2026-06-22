"""Infer receipt font-style groups from OCR geometry and raw image crops.

The OCR pipeline already segments letters, but those entities currently carry
geometry rather than image crops. This module turns OCR geometry, and
optionally raw receipt image pixels, into compact style embeddings and clusters
the embeddings to find font-like groups.
"""

from __future__ import annotations

from io import BytesIO
from dataclasses import dataclass
from math import cos, log, sin, sqrt
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence

import boto3
from PIL import Image as PILImage
from PIL import ImageFilter, ImageOps

BoundingBox = dict[str, float]

_EPSILON = 1e-9
_GEOMETRY_FEATURE_WEIGHTS = (
    2.0,  # font size relative to the receipt
    1.7,  # glyph advance relative to the receipt
    1.4,  # glyph aspect/condensing
    1.0,  # word height relative to line height
    0.8,  # vertical alignment inside the line
    0.7,  # glyph width consistency
    0.5,  # glyph height consistency
    0.4,  # uppercase ratio
    0.35,  # digit ratio
    0.25,  # punctuation ratio
    0.3,  # OCR confidence
    0.25,  # text angle sine
    0.25,  # text angle cosine
)
_PIXEL_FEATURE_NAMES = (
    "pixel_ink_ratio",
    "pixel_edge_density",
    "pixel_horizontal_transition_rate",
    "pixel_vertical_transition_rate",
    "pixel_content_width_ratio",
    "pixel_content_height_ratio",
    "pixel_content_aspect",
    "pixel_top_ink_ratio",
    "pixel_middle_ink_ratio",
    "pixel_bottom_ink_ratio",
    "pixel_left_ink_ratio",
    "pixel_center_ink_ratio",
    "pixel_right_ink_ratio",
    "pixel_row_bin_0",
    "pixel_row_bin_1",
    "pixel_row_bin_2",
    "pixel_row_bin_3",
    "pixel_row_bin_4",
    "pixel_row_bin_5",
    "pixel_row_bin_6",
    "pixel_row_bin_7",
)
_PIXEL_FEATURE_WEIGHTS = tuple(0.35 for _ in _PIXEL_FEATURE_NAMES)


@dataclass(frozen=True)
class FontStyleSample:
    """A word-level style sample embedded from segmented letters."""

    sample_id: str
    image_id: str | None
    receipt_id: int | None
    line_id: int
    word_id: int | None
    text: str
    letter_count: int
    vector: tuple[float, ...]
    metrics: dict[str, float]


@dataclass(frozen=True)
class FontCluster:
    """A discovered font-like group within one receipt/image."""

    cluster_id: int
    sample_ids: tuple[str, ...]
    line_ids: tuple[int, ...]
    word_ids: tuple[tuple[int, int], ...]
    text_examples: tuple[str, ...]
    sample_count: int
    letter_count: int
    centroid: tuple[float, ...]
    metrics: dict[str, float]
    label: str


@dataclass(frozen=True)
class FontSimilarityMatch:
    """Nearest-neighbor result for embedding similarity search."""

    sample_id: str
    score: float
    cluster_id: int | None


@dataclass(frozen=True)
class ReceiptFontAnalysis:
    """Font clustering result with helper methods for similarity search."""

    samples: tuple[FontStyleSample, ...]
    clusters: tuple[FontCluster, ...]
    noise_sample_ids: tuple[str, ...]
    assignments: dict[str, int]

    def cluster_for_sample(self, sample_id: str) -> int | None:
        """Return the cluster id for a sample, or None when it is noise."""
        cluster_id = self.assignments.get(sample_id)
        if cluster_id is None or cluster_id < 0:
            return None
        return cluster_id

    def similar_samples(
        self, sample_id: str, top_k: int = 5
    ) -> tuple[FontSimilarityMatch, ...]:
        """Find samples with the closest font-style embeddings."""
        by_id = {sample.sample_id: sample for sample in self.samples}
        query = by_id.get(sample_id)
        if query is None:
            raise ValueError(f"Unknown font sample: {sample_id}")

        candidates = [
            sample for sample in self.samples if sample.sample_id != sample_id
        ]
        return find_similar_font_samples(
            query,
            candidates,
            assignments=self.assignments,
            top_k=top_k,
        )


def analyze_receipt_fonts(
    letters: Sequence[Any],
    *,
    words: Sequence[Any] | None = None,
    lines: Sequence[Any] | None = None,
    raw_image: Any | None = None,
    image_y_origin: str = "bottom_left",
    crop_padding: float = 0.08,
    eps: float = 2.2,
    min_samples: int = 2,
    min_letters_per_sample: int = 2,
    promote_singletons_min_letters: int | None = 8,
) -> ReceiptFontAnalysis:
    """Embed OCR letter groups and cluster them into receipt font styles.

    Args:
        letters: OCR Letter or ReceiptLetter entities.
        words: Optional Word or ReceiptWord entities for word boxes/text.
        lines: Optional Line or ReceiptLine entities for line context.
        raw_image: Optional Pillow image, image path, or image bytes for pixel
            crop features from the original raw receipt.
        image_y_origin: Coordinate convention for normalized OCR boxes.
            Vision-style OCR uses ``bottom_left``.
        crop_padding: Fractional padding added around each OCR word crop.
        eps: DBSCAN radius in standardized embedding space.
        min_samples: Minimum neighboring samples to form a dense cluster.
        min_letters_per_sample: Drop very short words from clustering.
        promote_singletons_min_letters: Promote long noise samples into
            singleton clusters, useful for unique receipt headers.

    Returns:
        ReceiptFontAnalysis with samples, clusters, noise, and assignments.
    """
    samples = build_font_style_samples(
        letters,
        words=words,
        lines=lines,
        raw_image=raw_image,
        image_y_origin=image_y_origin,
        crop_padding=crop_padding,
        min_letters_per_sample=min_letters_per_sample,
    )
    if not samples:
        return ReceiptFontAnalysis((), (), (), {})

    labels = _dbscan(
        [sample.vector for sample in samples],
        eps=eps,
        min_samples=min_samples,
    )

    labels = _promote_singleton_samples(
        labels,
        samples,
        promote_singletons_min_letters=promote_singletons_min_letters,
    )
    return _build_analysis(samples, labels)


def build_font_style_samples(
    letters: Sequence[Any],
    *,
    words: Sequence[Any] | None = None,
    lines: Sequence[Any] | None = None,
    raw_image: Any | None = None,
    image_y_origin: str = "bottom_left",
    crop_padding: float = 0.08,
    min_letters_per_sample: int = 2,
) -> tuple[FontStyleSample, ...]:
    """Build word-level embeddings from segmented OCR letters and image crops."""
    line_index = _index_by_line(lines or ())
    word_index = _index_by_word(words or ())
    letters_by_word = _group_letters_by_word(letters)
    image = _coerce_image(raw_image) if raw_image is not None else None

    measurements: list[dict[str, Any]] = []
    for (line_id, word_id), grouped_letters in sorted(letters_by_word.items()):
        clean_letters = [
            letter
            for letter in sorted(grouped_letters, key=_letter_sort_key)
            if _text(letter).strip()
        ]
        if len(clean_letters) < min_letters_per_sample:
            continue

        word = word_index.get((line_id, word_id))
        line = line_index.get(line_id)
        word_box = _box(word) if word is not None else None
        if word_box is None:
            word_box = _union_boxes(
                box
                for box in (_box(letter) for letter in clean_letters)
                if box is not None
            )
        if word_box is None:
            continue

        line_box = _box(line) if line is not None else None
        text = str(_get(word, "text", "")) if word is not None else ""
        if not text:
            text = "".join(_text(letter) for letter in clean_letters)
        text = text.strip()
        if not text:
            continue

        letter_boxes = [
            box
            for box in (_box(letter) for letter in clean_letters)
            if box is not None
        ]
        pixel_metrics = None
        if image is not None:
            pixel_metrics = _extract_word_crop_metrics(
                image,
                word_box,
                y_origin=image_y_origin,
                padding_ratio=crop_padding,
            )
        measurements.append(
            _measure_sample(
                text=text,
                line_id=line_id,
                word_id=word_id,
                word_box=word_box,
                line_box=line_box,
                letters=clean_letters,
                letter_boxes=letter_boxes,
                pixel_metrics=pixel_metrics,
            )
        )

    if not measurements:
        return ()

    raw_rows, weights = _raw_feature_rows(measurements)
    vectors = _robust_standardize(raw_rows, weights=weights)

    samples = []
    for measured, vector in zip(measurements, vectors):
        sample_id = _sample_id(
            image_id=measured["image_id"],
            receipt_id=measured["receipt_id"],
            line_id=measured["line_id"],
            word_id=measured["word_id"],
        )
        samples.append(
            FontStyleSample(
                sample_id=sample_id,
                image_id=measured["image_id"],
                receipt_id=measured["receipt_id"],
                line_id=measured["line_id"],
                word_id=measured["word_id"],
                text=measured["text"],
                letter_count=measured["letter_count"],
                vector=tuple(vector),
                metrics=measured["metrics"],
            )
        )
    return tuple(samples)


def load_raw_image_from_s3(
    image: Any,
    *,
    s3_client: Any | None = None,
) -> PILImage.Image:
    """Download and open the raw S3 image referenced by a Dynamo Image entity."""
    bucket = _get(image, "raw_s3_bucket")
    key = _get(image, "raw_s3_key")
    if not bucket or not key:
        raise ValueError("image must include raw_s3_bucket and raw_s3_key")

    client = s3_client or boto3.client("s3")
    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()
    opened = PILImage.open(BytesIO(body))
    return opened.convert("RGB")


def analyze_dynamo_image_fonts(
    table_name: str,
    image_id: str,
    *,
    receipt_id: int | None = None,
    region: str = "us-east-1",
    prefer_receipt_ocr: bool = False,
    s3_client: Any | None = None,
    eps: float = 2.2,
    min_samples: int = 2,
    min_letters_per_sample: int = 2,
    promote_singletons_min_letters: int | None = 8,
) -> ReceiptFontAnalysis:
    """Load OCR details and raw image from Dynamo/S3, then analyze fonts."""
    from receipt_dynamo.data.dynamo_client import DynamoClient

    client = DynamoClient(table_name=table_name, region=region)
    details = client.get_image_details(image_id)
    if not details.images:
        raise ValueError(f"Image {image_id} not found")

    selected_receipt = None
    if prefer_receipt_ocr and details.receipts:
        if receipt_id is None:
            selected_receipt = sorted(
                details.receipts, key=lambda receipt: receipt.receipt_id
            )[0]
        else:
            selected_receipt = next(
                (
                    receipt
                    for receipt in details.receipts
                    if receipt.receipt_id == receipt_id
                ),
                None,
            )
            if selected_receipt is None:
                raise ValueError(
                    f"Receipt {receipt_id} not found for image {image_id}"
                )

    if selected_receipt is not None:
        raw_image = load_raw_image_from_s3(
            selected_receipt, s3_client=s3_client
        )
        selected_receipt_id = selected_receipt.receipt_id
        lines = [
            line
            for line in details.receipt_lines
            if line.receipt_id == selected_receipt_id
        ]
        words = [
            word
            for word in details.receipt_words
            if word.receipt_id == selected_receipt_id
        ]
        letters = [
            letter
            for letter in details.receipt_letters
            if letter.receipt_id == selected_receipt_id
        ]
    else:
        raw_image = load_raw_image_from_s3(
            details.images[0], s3_client=s3_client
        )
        lines = details.lines
        words = details.words
        letters = details.letters

    return analyze_receipt_fonts(
        letters,
        words=words,
        lines=lines,
        raw_image=raw_image,
        eps=eps,
        min_samples=min_samples,
        min_letters_per_sample=min_letters_per_sample,
        promote_singletons_min_letters=promote_singletons_min_letters,
    )


def find_similar_font_samples(
    query: FontStyleSample,
    candidates: Sequence[FontStyleSample],
    *,
    assignments: Mapping[str, int] | None = None,
    top_k: int = 5,
) -> tuple[FontSimilarityMatch, ...]:
    """Return nearest samples by cosine similarity over style embeddings."""
    scored = []
    for candidate in candidates:
        score = _cosine_similarity(query.vector, candidate.vector)
        cluster_id = None
        if assignments is not None:
            assigned = assignments.get(candidate.sample_id)
            cluster_id = (
                assigned if assigned is not None and assigned > 0 else None
            )
        scored.append(
            FontSimilarityMatch(
                sample_id=candidate.sample_id,
                score=score,
                cluster_id=cluster_id,
            )
        )
    scored.sort(key=lambda match: match.score, reverse=True)
    return tuple(scored[: max(0, top_k)])


def _measure_sample(
    *,
    text: str,
    line_id: int,
    word_id: int,
    word_box: BoundingBox,
    line_box: BoundingBox | None,
    letters: Sequence[Any],
    letter_boxes: Sequence[BoundingBox],
    pixel_metrics: Mapping[str, float] | None,
) -> dict[str, Any]:
    width = max(word_box["width"], _EPSILON)
    height = max(word_box["height"], _EPSILON)
    letter_count = max(len(letters), 1)
    width_per_char = width / letter_count
    line_height = (
        max(line_box["height"], _EPSILON) if line_box is not None else height
    )
    line_center_y = (
        line_box["y"] + line_height / 2.0
        if line_box is not None
        else word_box["y"] + height / 2.0
    )
    word_center_y = word_box["y"] + height / 2.0

    letter_widths = [max(box["width"], _EPSILON) for box in letter_boxes]
    letter_heights = [max(box["height"], _EPSILON) for box in letter_boxes]
    confidences = [
        _float(_get(letter, "confidence", 1.0), default=1.0)
        for letter in letters
    ]
    angles = [
        _float(_get(letter, "angle_radians", 0.0), default=0.0)
        for letter in letters
    ]
    characters = [_text(letter) for letter in letters]

    metrics = {
        "height": height,
        "width": width,
        "area": width * height,
        "width_per_char": width_per_char,
        "aspect_per_char": width_per_char / height,
        "height_to_line": height / line_height,
        "baseline_offset": (word_center_y - line_center_y) / line_height,
        "glyph_width_cv": _coefficient_of_variation(letter_widths),
        "glyph_height_cv": _coefficient_of_variation(letter_heights),
        "uppercase_ratio": _ratio(characters, str.isupper),
        "digit_ratio": _ratio(characters, str.isdigit),
        "punctuation_ratio": _ratio(
            characters, lambda char: not char.isalnum()
        ),
        "confidence": mean(confidences) if confidences else 1.0,
        "angle_radians": mean(angles) if angles else 0.0,
    }
    if pixel_metrics is not None:
        metrics.update(pixel_metrics)

    first = letters[0]
    return {
        "image_id": _get(first, "image_id"),
        "receipt_id": _get(first, "receipt_id"),
        "line_id": line_id,
        "word_id": word_id,
        "text": text,
        "letter_count": len(letters),
        "metrics": metrics,
    }


def _raw_feature_rows(
    measurements: Sequence[dict[str, Any]],
) -> tuple[list[tuple[float, ...]], tuple[float, ...]]:
    heights = [item["metrics"]["height"] for item in measurements]
    advances = [item["metrics"]["width_per_char"] for item in measurements]
    aspects = [item["metrics"]["aspect_per_char"] for item in measurements]
    median_height = _median(heights)
    median_advance = _median(advances)
    median_aspect = _median(aspects)

    include_pixel_features = any(
        "pixel_ink_ratio" in item["metrics"] for item in measurements
    )
    rows = []
    for item in measurements:
        metrics = item["metrics"]
        font_size_ratio = metrics["height"] / max(median_height, _EPSILON)
        advance_ratio = metrics["width_per_char"] / max(
            median_advance, _EPSILON
        )
        aspect_ratio = metrics["aspect_per_char"] / max(
            median_aspect, _EPSILON
        )

        metrics["font_size_ratio"] = font_size_ratio
        metrics["advance_ratio"] = advance_ratio
        metrics["aspect_ratio"] = aspect_ratio

        angle = metrics["angle_radians"]
        row = (
            log(max(font_size_ratio, _EPSILON)),
            log(max(advance_ratio, _EPSILON)),
            log(max(aspect_ratio, _EPSILON)),
            metrics["height_to_line"],
            metrics["baseline_offset"],
            metrics["glyph_width_cv"],
            metrics["glyph_height_cv"],
            metrics["uppercase_ratio"],
            metrics["digit_ratio"],
            metrics["punctuation_ratio"],
            metrics["confidence"],
            sin(angle),
            cos(angle),
        )
        if include_pixel_features:
            row = row + tuple(
                metrics.get(feature_name, 0.0)
                for feature_name in _PIXEL_FEATURE_NAMES
            )
        rows.append(row)
    weights = _GEOMETRY_FEATURE_WEIGHTS
    if include_pixel_features:
        weights = weights + _PIXEL_FEATURE_WEIGHTS
    return rows, weights


def _robust_standardize(
    rows: Sequence[Sequence[float]], *, weights: Sequence[float]
) -> list[tuple[float, ...]]:
    if not rows:
        return []

    column_count = len(rows[0])
    columns = [[row[index] for row in rows] for index in range(column_count)]
    centers = [_median(column) for column in columns]
    scales = [_mad(column, center) for column, center in zip(columns, centers)]

    vectors = []
    for row in rows:
        vector = []
        for index, value in enumerate(row):
            scale = scales[index] if scales[index] > _EPSILON else 1.0
            weight = weights[index] if index < len(weights) else 1.0
            vector.append(((value - centers[index]) / scale) * weight)
        vectors.append(tuple(vector))
    return vectors


def _dbscan(
    vectors: Sequence[Sequence[float]], *, eps: float, min_samples: int
) -> list[int]:
    if not vectors:
        return []
    if min_samples <= 1:
        return [index + 1 for index in range(len(vectors))]

    visited = [False for _ in vectors]
    labels = [0 for _ in vectors]
    cluster_id = 0

    def neighbors(point_index: int) -> list[int]:
        return [
            index
            for index, candidate in enumerate(vectors)
            if _euclidean(vectors[point_index], candidate) <= eps
        ]

    for point_index in range(len(vectors)):
        if visited[point_index]:
            continue
        visited[point_index] = True
        point_neighbors = neighbors(point_index)
        if len(point_neighbors) < min_samples:
            labels[point_index] = -1
            continue

        cluster_id += 1
        labels[point_index] = cluster_id
        seeds = [idx for idx in point_neighbors if idx != point_index]
        while seeds:
            seed = seeds.pop(0)
            if not visited[seed]:
                visited[seed] = True
                seed_neighbors = neighbors(seed)
                if len(seed_neighbors) >= min_samples:
                    for neighbor in seed_neighbors:
                        if neighbor not in seeds:
                            seeds.append(neighbor)
            if labels[seed] <= 0:
                labels[seed] = cluster_id

    return labels


def _promote_singleton_samples(
    labels: Sequence[int],
    samples: Sequence[FontStyleSample],
    *,
    promote_singletons_min_letters: int | None,
) -> list[int]:
    next_cluster = max([label for label in labels if label > 0], default=0) + 1
    promoted = list(labels)
    if promote_singletons_min_letters is None:
        return promoted

    for index, sample in enumerate(samples):
        if promoted[index] > 0:
            continue
        if sample.letter_count < promote_singletons_min_letters:
            continue
        promoted[index] = next_cluster
        next_cluster += 1
    return promoted


def _build_analysis(
    samples: Sequence[FontStyleSample], labels: Sequence[int]
) -> ReceiptFontAnalysis:
    grouped: dict[int, list[FontStyleSample]] = {}
    noise = []
    for sample, label in zip(samples, labels):
        if label > 0:
            grouped.setdefault(label, []).append(sample)
        else:
            noise.append(sample.sample_id)

    ordered_labels = sorted(
        grouped,
        key=lambda label: min(
            samples.index(sample) for sample in grouped[label]
        ),
    )
    relabel = {
        old_label: new_label
        for new_label, old_label in enumerate(ordered_labels, start=1)
    }
    assignments = {
        sample.sample_id: relabel[label] if label > 0 else -1
        for sample, label in zip(samples, labels)
    }

    clusters = []
    for old_label in ordered_labels:
        cluster_samples = grouped[old_label]
        cluster_id = relabel[old_label]
        clusters.append(_make_cluster(cluster_id, cluster_samples))

    return ReceiptFontAnalysis(
        samples=tuple(samples),
        clusters=tuple(clusters),
        noise_sample_ids=tuple(noise),
        assignments=assignments,
    )


def _make_cluster(
    cluster_id: int, samples: Sequence[FontStyleSample]
) -> FontCluster:
    sample_ids = tuple(sample.sample_id for sample in samples)
    line_ids = tuple(sorted({sample.line_id for sample in samples}))
    word_ids = tuple(
        sorted(
            {
                (sample.line_id, sample.word_id)
                for sample in samples
                if sample.word_id is not None
            }
        )
    )
    letter_count = sum(sample.letter_count for sample in samples)
    centroid = _centroid([sample.vector for sample in samples])
    metrics = _average_metrics(samples)

    return FontCluster(
        cluster_id=cluster_id,
        sample_ids=sample_ids,
        line_ids=line_ids,
        word_ids=word_ids,
        text_examples=tuple(sample.text for sample in samples[:5]),
        sample_count=len(samples),
        letter_count=letter_count,
        centroid=centroid,
        metrics=metrics,
        label=_describe_cluster(metrics),
    )


def _average_metrics(samples: Sequence[FontStyleSample]) -> dict[str, float]:
    metric_names = sorted(
        {
            metric_name
            for sample in samples
            for metric_name in sample.metrics.keys()
        }
    )
    averaged = {}
    for name in metric_names:
        values = [
            sample.metrics[name]
            for sample in samples
            if name in sample.metrics
        ]
        if values:
            averaged[name] = mean(values)
    return averaged


def _describe_cluster(metrics: Mapping[str, float]) -> str:
    size_ratio = metrics.get("font_size_ratio", 1.0)
    aspect = metrics.get("aspect_per_char", 0.6)
    uppercase = metrics.get("uppercase_ratio", 0.0)
    digit = metrics.get("digit_ratio", 0.0)
    punctuation = metrics.get("punctuation_ratio", 0.0)

    if size_ratio >= 1.25:
        size = "large"
    elif size_ratio <= 0.85:
        size = "small"
    else:
        size = "regular"

    if aspect <= 0.45:
        width = "condensed"
    elif aspect >= 0.8:
        width = "wide"
    else:
        width = "normal"

    if digit >= 0.7:
        text_style = "numeric"
    elif uppercase >= 0.7:
        text_style = "uppercase"
    elif punctuation >= 0.4:
        text_style = "punctuated"
    else:
        text_style = "mixed"

    return f"{size} {width} {text_style}"


def _coerce_image(raw_image: Any) -> PILImage.Image:
    if isinstance(raw_image, PILImage.Image):
        return raw_image.convert("RGB")
    if isinstance(raw_image, (bytes, bytearray)):
        return PILImage.open(BytesIO(raw_image)).convert("RGB")
    if hasattr(raw_image, "read"):
        return PILImage.open(raw_image).convert("RGB")
    return PILImage.open(raw_image).convert("RGB")


def _extract_word_crop_metrics(
    image: PILImage.Image,
    word_box: BoundingBox,
    *,
    y_origin: str,
    padding_ratio: float,
) -> dict[str, float] | None:
    bounds = _image_crop_bounds(
        word_box,
        image_size=image.size,
        y_origin=y_origin,
        padding_ratio=padding_ratio,
    )
    if bounds is None:
        return None

    crop = image.crop(bounds)
    if crop.width < 3 or crop.height < 3:
        return None

    gray = ImageOps.autocontrast(ImageOps.grayscale(crop))
    width, height = gray.size
    pixels = list(gray.tobytes())
    if not pixels:
        return None

    threshold = _otsu_threshold(pixels)
    dark_mask = [1 if pixel <= threshold else 0 for pixel in pixels]
    light_mask = [1 if pixel > threshold else 0 for pixel in pixels]
    mask = dark_mask if sum(dark_mask) <= sum(light_mask) else light_mask
    ink_pixels = sum(mask)
    total_pixels = len(mask)
    if ink_pixels == 0:
        return _empty_pixel_metrics()

    content_bbox = _mask_bbox(mask, width, height)
    if content_bbox is None:
        return _empty_pixel_metrics()

    left, top, right, bottom = content_bbox
    content_width = max(right - left + 1, 1)
    content_height = max(bottom - top + 1, 1)
    edge_values = list(gray.filter(ImageFilter.FIND_EDGES).tobytes())

    metrics = {
        "pixel_ink_ratio": ink_pixels / total_pixels,
        "pixel_edge_density": (
            sum(1 for value in edge_values if value >= 32) / total_pixels
        ),
        "pixel_horizontal_transition_rate": _horizontal_transition_rate(
            mask, width, height
        ),
        "pixel_vertical_transition_rate": _vertical_transition_rate(
            mask, width, height
        ),
        "pixel_content_width_ratio": content_width / width,
        "pixel_content_height_ratio": content_height / height,
        "pixel_content_aspect": content_width / content_height,
        "pixel_top_ink_ratio": _mask_density(
            mask, width, height, 0, 0, width, height // 3
        ),
        "pixel_middle_ink_ratio": _mask_density(
            mask, width, height, 0, height // 3, width, (height * 2) // 3
        ),
        "pixel_bottom_ink_ratio": _mask_density(
            mask, width, height, 0, (height * 2) // 3, width, height
        ),
        "pixel_left_ink_ratio": _mask_density(
            mask, width, height, 0, 0, width // 3, height
        ),
        "pixel_center_ink_ratio": _mask_density(
            mask, width, height, width // 3, 0, (width * 2) // 3, height
        ),
        "pixel_right_ink_ratio": _mask_density(
            mask, width, height, (width * 2) // 3, 0, width, height
        ),
    }
    metrics.update(_row_profile_metrics(mask, width, height, bins=8))
    return metrics


def _image_crop_bounds(
    box: BoundingBox,
    *,
    image_size: tuple[int, int],
    y_origin: str,
    padding_ratio: float,
) -> tuple[int, int, int, int] | None:
    image_width, image_height = image_size
    x = box["x"]
    y = box["y"]
    width = box["width"]
    height = box["height"]
    normalized = _is_normalized_box(box)

    if normalized:
        left = x * image_width
        right = (x + width) * image_width
        if y_origin == "bottom_left":
            top = (1.0 - y - height) * image_height
            bottom = (1.0 - y) * image_height
        elif y_origin == "top_left":
            top = y * image_height
            bottom = (y + height) * image_height
        else:
            raise ValueError(
                "image_y_origin must be 'bottom_left' or 'top_left'"
            )
    else:
        left = x
        right = x + width
        if y_origin == "bottom_left":
            top = image_height - y - height
            bottom = image_height - y
        elif y_origin == "top_left":
            top = y
            bottom = y + height
        else:
            raise ValueError(
                "image_y_origin must be 'bottom_left' or 'top_left'"
            )

    crop_width = max(right - left, 1.0)
    crop_height = max(bottom - top, 1.0)
    padding = max(2.0, max(crop_width, crop_height) * padding_ratio)

    left = max(0, int(left - padding))
    top = max(0, int(top - padding))
    right = min(image_width, int(right + padding + 0.999))
    bottom = min(image_height, int(bottom + padding + 0.999))
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def _is_normalized_box(box: BoundingBox) -> bool:
    return (
        -0.1 <= box["x"] <= 1.1
        and -0.1 <= box["y"] <= 1.1
        and 0.0 <= box["width"] <= 1.2
        and 0.0 <= box["height"] <= 1.2
    )


def _otsu_threshold(pixels: Sequence[int]) -> int:
    histogram = [0] * 256
    for pixel in pixels:
        histogram[max(0, min(255, int(pixel)))] += 1

    total = len(pixels)
    sum_total = sum(value * count for value, count in enumerate(histogram))
    sum_background = 0.0
    weight_background = 0
    max_variance = -1.0
    threshold = 127

    for value, count in enumerate(histogram):
        weight_background += count
        if weight_background == 0:
            continue
        weight_foreground = total - weight_background
        if weight_foreground == 0:
            break

        sum_background += value * count
        mean_background = sum_background / weight_background
        mean_foreground = (sum_total - sum_background) / weight_foreground
        variance = (
            weight_background
            * weight_foreground
            * (mean_background - mean_foreground) ** 2
        )
        if variance > max_variance:
            max_variance = variance
            threshold = value

    return threshold


def _empty_pixel_metrics() -> dict[str, float]:
    metrics = {name: 0.0 for name in _PIXEL_FEATURE_NAMES}
    return metrics


def _mask_bbox(
    mask: Sequence[int], width: int, height: int
) -> tuple[int, int, int, int] | None:
    xs = []
    ys = []
    for index, value in enumerate(mask):
        if not value:
            continue
        y, x = divmod(index, width)
        xs.append(x)
        ys.append(y)
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _mask_density(
    mask: Sequence[int],
    width: int,
    height: int,
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> float:
    left = max(0, min(width, left))
    right = max(left, min(width, right))
    top = max(0, min(height, top))
    bottom = max(top, min(height, bottom))
    area = max((right - left) * (bottom - top), 1)
    ink = 0
    for y in range(top, bottom):
        row_offset = y * width
        ink += sum(mask[row_offset + x] for x in range(left, right))
    return ink / area


def _row_profile_metrics(
    mask: Sequence[int], width: int, height: int, *, bins: int
) -> dict[str, float]:
    metrics = {}
    for bin_index in range(bins):
        top = (height * bin_index) // bins
        bottom = (height * (bin_index + 1)) // bins
        metrics[f"pixel_row_bin_{bin_index}"] = _mask_density(
            mask, width, height, 0, top, width, bottom
        )
    return metrics


def _horizontal_transition_rate(
    mask: Sequence[int], width: int, height: int
) -> float:
    if width <= 1:
        return 0.0
    transitions = 0
    for y in range(height):
        offset = y * width
        for x in range(1, width):
            if mask[offset + x] != mask[offset + x - 1]:
                transitions += 1
    return transitions / ((width - 1) * height)


def _vertical_transition_rate(
    mask: Sequence[int], width: int, height: int
) -> float:
    if height <= 1:
        return 0.0
    transitions = 0
    for y in range(1, height):
        offset = y * width
        previous_offset = (y - 1) * width
        for x in range(width):
            if mask[offset + x] != mask[previous_offset + x]:
                transitions += 1
    return transitions / (width * (height - 1))


def _group_letters_by_word(
    letters: Iterable[Any],
) -> dict[tuple[int, int], list[Any]]:
    grouped: dict[tuple[int, int], list[Any]] = {}
    for letter in letters:
        line_id = _int(_get(letter, "line_id"), default=-1)
        word_id = _int(_get(letter, "word_id"), default=-1)
        if line_id < 0 or word_id < 0:
            continue
        grouped.setdefault((line_id, word_id), []).append(letter)
    return grouped


def _index_by_line(lines: Iterable[Any]) -> dict[int, Any]:
    return {
        _int(_get(line, "line_id"), default=-1): line
        for line in lines
        if _int(_get(line, "line_id"), default=-1) >= 0
    }


def _index_by_word(words: Iterable[Any]) -> dict[tuple[int, int], Any]:
    return {
        (
            _int(_get(word, "line_id"), default=-1),
            _int(_get(word, "word_id"), default=-1),
        ): word
        for word in words
        if _int(_get(word, "line_id"), default=-1) >= 0
        and _int(_get(word, "word_id"), default=-1) >= 0
    }


def _letter_sort_key(letter: Any) -> tuple[int, float]:
    letter_id = _int(_get(letter, "letter_id"), default=0)
    box = _box(letter)
    x = box["x"] if box is not None else 0.0
    return (letter_id, x)


def _sample_id(
    *,
    image_id: str | None,
    receipt_id: int | None,
    line_id: int,
    word_id: int | None,
) -> str:
    prefix = []
    if image_id:
        prefix.append(f"image={image_id}")
    if receipt_id is not None:
        prefix.append(f"receipt={receipt_id}")
    prefix.append(f"line={line_id}")
    if word_id is not None:
        prefix.append(f"word={word_id}")
    return ":".join(prefix)


def _box(value: Any) -> BoundingBox | None:
    if value is None:
        return None

    bounding_box = _get(value, "bounding_box")
    if isinstance(bounding_box, Mapping):
        return {
            "x": _float(bounding_box.get("x")),
            "y": _float(bounding_box.get("y")),
            "width": max(_float(bounding_box.get("width")), _EPSILON),
            "height": max(_float(bounding_box.get("height")), _EPSILON),
        }

    corners = [
        _get(value, "top_left"),
        _get(value, "top_right"),
        _get(value, "bottom_left"),
        _get(value, "bottom_right"),
    ]
    points = [
        (_float(point.get("x")), _float(point.get("y")))
        for point in corners
        if isinstance(point, Mapping)
    ]
    if not points:
        return None

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x = min(xs)
    min_y = min(ys)
    return {
        "x": min_x,
        "y": min_y,
        "width": max(max(xs) - min_x, _EPSILON),
        "height": max(max(ys) - min_y, _EPSILON),
    }


def _union_boxes(boxes: Iterable[BoundingBox]) -> BoundingBox | None:
    materialized = list(boxes)
    if not materialized:
        return None

    min_x = min(box["x"] for box in materialized)
    min_y = min(box["y"] for box in materialized)
    max_x = max(box["x"] + box["width"] for box in materialized)
    max_y = max(box["y"] + box["height"] for box in materialized)
    return {
        "x": min_x,
        "y": min_y,
        "width": max(max_x - min_x, _EPSILON),
        "height": max(max_y - min_y, _EPSILON),
    }


def _ratio(characters: Sequence[str], predicate: Any) -> float:
    visible = [char for char in characters if char.strip()]
    if not visible:
        return 0.0
    return sum(1 for char in visible if predicate(char)) / len(visible)


def _coefficient_of_variation(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    value_mean = mean(values)
    if abs(value_mean) <= _EPSILON:
        return 0.0
    variance = sum((value - value_mean) ** 2 for value in values) / len(values)
    return sqrt(variance) / abs(value_mean)


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _mad(values: Sequence[float], center: float) -> float:
    if not values:
        return 1.0
    deviations = [abs(value - center) for value in values]
    return _median(deviations) * 1.4826


def _centroid(vectors: Sequence[Sequence[float]]) -> tuple[float, ...]:
    if not vectors:
        return ()
    return tuple(
        mean(vector[index] for vector in vectors)
        for index in range(len(vectors[0]))
    )


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    numerator = sum(left * right for left, right in zip(a, b))
    a_norm = sqrt(sum(value * value for value in a))
    b_norm = sqrt(sum(value * value for value in b))
    denominator = a_norm * b_norm
    if denominator <= _EPSILON:
        return 0.0
    return numerator / denominator


def _euclidean(a: Sequence[float], b: Sequence[float]) -> float:
    return sqrt(sum((left - right) ** 2 for left, right in zip(a, b)))


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _text(value: Any) -> str:
    return str(_get(value, "text", ""))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
