"""Letter-crop embeddings for receipt font/style clustering.

This module embeds individual OCR letter crops using visual features rather
than semantic text embeddings. OCR's character guess is used to remove glyph
identity from the style vector, so clustering has a better chance of grouping
visual font/style differences instead of just grouping "A" separately from
"7".
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import atan2, pi
from statistics import mean, median
from typing import Any, Mapping, Sequence

from PIL import Image as PILImage
from PIL import ImageFilter, ImageOps
from receipt_upload.font_analysis import (
    _EPSILON,
    _box,
    _centroid,
    _coerce_image,
    _dbscan,
    _float,
    _get,
    _horizontal_transition_rate,
    _image_crop_bounds,
    _int,
    _mask_bbox,
    _mask_density,
    _otsu_threshold,
    _text,
    _vertical_transition_rate,
)

BoundingBox = dict[str, float]
LineKey = tuple[str | None, int | None, int]

_GLYPH_SIZE = 16
_PROJECTION_BINS = 8
_HOG_BINS = 8
_ROW_PROJECTION_NAMES = tuple(
    f"row_projection_{index}" for index in range(_PROJECTION_BINS)
)
_COLUMN_PROJECTION_NAMES = tuple(
    f"column_projection_{index}" for index in range(_PROJECTION_BINS)
)
_HOG_FEATURE_NAMES = tuple(
    f"hog_orientation_{index}" for index in range(_HOG_BINS)
)
_STROKE_FEATURE_NAMES = (
    "stroke_width_mean",
    "stroke_width_std",
    "stroke_width_max",
    "stroke_width_mean_norm",
    "stroke_width_max_norm",
)
_CONTOUR_FEATURE_NAMES = (
    "top_profile_mean",
    "top_profile_std",
    "bottom_profile_mean",
    "bottom_profile_std",
    "left_profile_mean",
    "left_profile_std",
    "right_profile_mean",
    "right_profile_std",
    "vertical_symmetry",
    "horizontal_symmetry",
)
_COMPONENT_FEATURE_NAMES = (
    "component_count",
    "largest_component_ratio",
    "hole_count",
    "hole_area_ratio",
)
_STYLE_METRIC_NAMES = (
    "box_width",
    "box_height",
    "box_aspect",
    "ink_ratio",
    "edge_density",
    "horizontal_transition_rate",
    "vertical_transition_rate",
    "content_width_ratio",
    "content_height_ratio",
    "content_aspect",
    "top_ink_ratio",
    "middle_ink_ratio",
    "bottom_ink_ratio",
    "left_ink_ratio",
    "center_ink_ratio",
    "right_ink_ratio",
    "confidence",
) + (
    *_ROW_PROJECTION_NAMES,
    *_COLUMN_PROJECTION_NAMES,
    *_HOG_FEATURE_NAMES,
    *_STROKE_FEATURE_NAMES,
    *_CONTOUR_FEATURE_NAMES,
    *_COMPONENT_FEATURE_NAMES,
)
_ABSOLUTE_STYLE_NAMES = (
    "box_width",
    "box_height",
    "box_aspect",
    "ink_ratio",
    "edge_density",
    "horizontal_transition_rate",
    "vertical_transition_rate",
    "content_aspect",
    "confidence",
) + (
    "stroke_width_mean_norm",
    "stroke_width_max_norm",
    "component_count",
    "largest_component_ratio",
    "hole_count",
    "hole_area_ratio",
    "vertical_symmetry",
    "horizontal_symmetry",
)
_LINE_LETTER_AGGREGATE_SOURCE_NAMES = (
    "box_x",
    "box_y",
    "box_center_x",
    "box_center_y",
    *_STYLE_METRIC_NAMES,
)
_LINE_AGGREGATE_STATS = ("mean", "median", "std")
_LINE_BASE_METRIC_NAMES = (
    "letter_count",
    "word_count",
    "line_box_x",
    "line_box_y",
    "line_box_width",
    "line_box_height",
    "line_box_aspect",
    "line_center_x",
    "line_center_y",
    "letter_area_ratio",
    "median_gap",
    "mean_gap",
    "std_gap",
    "median_intra_word_gap",
    "median_inter_word_gap",
    "median_advance",
    "gap_to_height_ratio",
    "advance_to_height_ratio",
    "alpha_ratio",
    "uppercase_ratio",
    "digit_ratio",
    "punctuation_ratio",
    "assigned_letter_cluster_ratio",
    "noise_letter_cluster_ratio",
    "dominant_letter_cluster_ratio",
    "distinct_letter_cluster_ratio",
)
_LINE_AGGREGATE_METRIC_NAMES = tuple(
    f"{stat}_{name}"
    for name in _LINE_LETTER_AGGREGATE_SOURCE_NAMES
    for stat in _LINE_AGGREGATE_STATS
)
_LINE_METRIC_NAMES = _LINE_BASE_METRIC_NAMES + _LINE_AGGREGATE_METRIC_NAMES


@dataclass(frozen=True)
class LetterImageSample:
    """A single OCR letter crop embedded as a visual style vector."""

    sample_id: str
    image_id: str | None
    receipt_id: int | None
    line_id: int
    word_id: int
    letter_id: int
    text: str
    normalized_char: str
    vector: tuple[float, ...]
    style_vector: tuple[float, ...]
    metrics: dict[str, float]


@dataclass(frozen=True)
class LetterStyleCluster:
    """A discovered visual style cluster over letter crops."""

    cluster_id: int
    sample_ids: tuple[str, ...]
    sample_count: int
    line_ids: tuple[int, ...]
    normalized_chars: tuple[str, ...]
    text_examples: tuple[str, ...]
    centroid: tuple[float, ...]
    metrics: dict[str, float]
    label: str


@dataclass(frozen=True)
class LetterFontAnalysis:
    """Letter-level font/style clustering result."""

    samples: tuple[LetterImageSample, ...]
    clusters: tuple[LetterStyleCluster, ...]
    noise_sample_ids: tuple[str, ...]
    assignments: dict[str, int]

    def cluster_for_sample(self, sample_id: str) -> int | None:
        cluster_id = self.assignments.get(sample_id)
        if cluster_id is None or cluster_id < 0:
            return None
        return cluster_id


@dataclass(frozen=True)
class LineFontSample:
    """A line-level font/style signature aggregated from OCR letter crops."""

    sample_id: str
    image_id: str | None
    receipt_id: int | None
    line_id: int
    text: str
    section: str | None
    letter_sample_ids: tuple[str, ...]
    vector: tuple[float, ...]
    metrics: dict[str, float]


@dataclass(frozen=True)
class LineFontCluster:
    """A discovered visual style cluster over receipt lines."""

    cluster_id: int
    sample_ids: tuple[str, ...]
    sample_count: int
    line_ids: tuple[int, ...]
    sections: tuple[str, ...]
    text_examples: tuple[str, ...]
    centroid: tuple[float, ...]
    metrics: dict[str, float]
    label: str


@dataclass(frozen=True)
class LineFontAnalysis:
    """Line-level font/style clustering result."""

    samples: tuple[LineFontSample, ...]
    clusters: tuple[LineFontCluster, ...]
    noise_sample_ids: tuple[str, ...]
    assignments: dict[str, int]

    def cluster_for_sample(self, sample_id: str) -> int | None:
        cluster_id = self.assignments.get(sample_id)
        if cluster_id is None or cluster_id < 0:
            return None
        return cluster_id


def build_letter_image_samples(
    letters: Sequence[Any],
    *,
    raw_image: Any,
    image_y_origin: str = "bottom_left",
    crop_padding: float = 0.24,
    glyph_size: int = _GLYPH_SIZE,
    min_confidence: float = 0.35,
) -> tuple[LetterImageSample, ...]:
    """Build one visual embedding per OCR letter crop.

    The returned ``style_vector`` is character-centered: each glyph's visual
    embedding is centered against other OCR-identical glyphs before absolute
    style metrics are appended. This keeps the OCR guess useful without making
    the clustering mostly about glyph identity.
    """
    image = _coerce_image(raw_image)
    records: list[dict[str, Any]] = []

    for letter in letters:
        char = _text(letter)
        if len(char) != 1 or not char.strip():
            continue

        confidence = _float(_get(letter, "confidence", 1.0), default=1.0)
        if confidence < min_confidence:
            continue

        box = _box(letter)
        if box is None:
            continue

        crop_features = _extract_letter_crop_features(
            image,
            box,
            y_origin=image_y_origin,
            padding_ratio=crop_padding,
            glyph_size=glyph_size,
        )
        if crop_features is None:
            continue

        metrics = {
            **crop_features["metrics"],
            "box_x": box["x"],
            "box_y": box["y"],
            "box_width": box["width"],
            "box_height": box["height"],
            "box_center_x": box["x"] + box["width"] / 2,
            "box_center_y": box["y"] + box["height"] / 2,
            "box_aspect": box["width"] / max(box["height"], _EPSILON),
            "confidence": confidence,
        }
        base_vector = tuple(crop_features["glyph_vector"]) + tuple(
            metrics[name] for name in _STYLE_METRIC_NAMES
        )
        normalized_vector = _l2_normalize(base_vector)

        records.append(
            {
                "image_id": _get(letter, "image_id"),
                "receipt_id": _optional_int(_get(letter, "receipt_id")),
                "line_id": _int(_get(letter, "line_id"), default=-1),
                "word_id": _int(_get(letter, "word_id"), default=-1),
                "letter_id": _int(_get(letter, "letter_id"), default=-1),
                "text": char,
                "normalized_char": _normalize_char(char),
                "vector": normalized_vector,
                "metrics": metrics,
            }
        )

    return _with_style_vectors(records)


def cluster_letter_styles(
    samples: Sequence[LetterImageSample],
    *,
    eps: float = 0.78,
    min_samples: int = 5,
    promote_singletons_min_samples: int | None = None,
) -> LetterFontAnalysis:
    """Cluster character-centered letter style vectors."""
    if not samples:
        return LetterFontAnalysis((), (), (), {})

    labels = _cluster_vectors(
        [sample.style_vector for sample in samples],
        eps=eps,
        min_samples=min_samples,
    )
    if promote_singletons_min_samples is not None:
        labels = _promote_large_noise_groups(
            labels,
            samples,
            min_group_size=promote_singletons_min_samples,
        )
    return _build_letter_analysis(samples, labels)


def build_line_font_samples(
    letter_samples: Sequence[LetterImageSample],
    *,
    lines: Sequence[Any] | None = None,
    words: Sequence[Any] | None = None,
    letter_assignments: Mapping[str, int] | None = None,
    section_by_line: Mapping[Any, str] | None = None,
    min_letters_per_line: int = 3,
) -> tuple[LineFontSample, ...]:
    """Aggregate letter crop features into one font/style signature per line."""
    if not letter_samples:
        return ()

    line_by_key, words_by_key = _line_context_maps(lines, words)
    groups: dict[LineKey, list[LetterImageSample]] = defaultdict(list)
    for sample in letter_samples:
        groups[_line_key_from_sample(sample)].append(sample)

    line_samples = []
    for key in sorted(groups, key=_line_key_sort_value):
        samples = sorted(groups[key], key=_letter_sort_value)
        if len(samples) < min_letters_per_line:
            continue

        line = line_by_key.get(key)
        text = _line_text_for_key(key, line, words_by_key, samples)
        section = _lookup_line_mapping(section_by_line, key, key[2])
        metrics = _line_metrics(
            samples,
            line=line,
            words=words_by_key.get(key, ()),
            letter_assignments=letter_assignments,
        )
        style_centroid = _centroid([sample.style_vector for sample in samples])
        style_spread = _componentwise_std(
            [sample.style_vector for sample in samples],
            style_centroid,
        )
        vector = (
            tuple(metrics[name] for name in _LINE_METRIC_NAMES)
            + style_centroid
            + style_spread
        )
        line_samples.append(
            LineFontSample(
                sample_id=_line_sample_id(key),
                image_id=key[0],
                receipt_id=key[1],
                line_id=key[2],
                text=text,
                section=section,
                letter_sample_ids=tuple(
                    sample.sample_id for sample in samples
                ),
                vector=vector,
                metrics=metrics,
            )
        )

    return tuple(line_samples)


def cluster_line_font_styles(
    samples: Sequence[LineFontSample],
    *,
    eps: float = 0.58,
    min_samples: int = 2,
) -> LineFontAnalysis:
    """Cluster line-level font/style signatures."""
    if not samples:
        return LineFontAnalysis((), (), (), {})

    prepared_vectors = _robust_standardized_l2_vectors(
        [sample.vector for sample in samples]
    )
    labels = _cluster_vectors(
        prepared_vectors,
        eps=eps,
        min_samples=min_samples,
    )
    return _build_line_analysis(samples, labels, prepared_vectors)


def upsert_letter_samples_to_chroma(
    samples: Sequence[LetterImageSample],
    *,
    persist_directory: str,
    collection_name: str = "letter_font_glyphs",
    extra_metadata_by_id: Mapping[str, Mapping[str, object]] | None = None,
    batch_size: int = 1000,
    reset_collection: bool = False,
) -> int:
    """Persist letter style vectors into a separate local Chroma collection.

    Existing rows are preserved unless ``reset_collection`` is explicitly set.
    """
    from receipt_chroma import ChromaClient

    extra_metadata_by_id = extra_metadata_by_id or {}
    with ChromaClient(
        persist_directory=persist_directory,
        mode="write",
        metadata_only=True,
    ) as client:
        collection = client.get_collection(
            collection_name,
            create_if_missing=True,
            metadata={
                "description": "Receipt OCR letter crop style embeddings"
            },
        )
        if reset_collection and collection.count():
            existing = collection.get(include=["metadatas"])
            existing_ids = list(existing.get("ids") or [])
            for start in range(0, len(existing_ids), batch_size):
                collection.delete(ids=existing_ids[start : start + batch_size])

        for start in range(0, len(samples), batch_size):
            batch = samples[start : start + batch_size]
            client.upsert(
                collection_name=collection_name,
                ids=[sample.sample_id for sample in batch],
                embeddings=[list(sample.style_vector) for sample in batch],
                documents=[sample.text for sample in batch],
                metadatas=[
                    {
                        **_sample_metadata(sample),
                        **dict(extra_metadata_by_id.get(sample.sample_id, {})),
                    }
                    for sample in batch
                ],
            )

        return client.count(collection_name)


def query_similar_letters(
    *,
    persist_directory: str,
    query_sample: LetterImageSample,
    collection_name: str = "letter_font_glyphs",
    n_results: int = 8,
    same_character_only: bool = True,
) -> dict[str, Any]:
    """Query Chroma for similar letter style vectors."""
    from receipt_chroma import ChromaClient

    where = None
    if same_character_only:
        where = {"normalized_char": query_sample.normalized_char}

    with ChromaClient(
        persist_directory=persist_directory,
        mode="read",
    ) as client:
        return client.query(
            collection_name=collection_name,
            query_embeddings=[list(query_sample.style_vector)],
            n_results=n_results,
            where=where,
            include=["metadatas", "documents", "distances"],
        )


def _extract_letter_crop_features(
    image: PILImage.Image,
    box: BoundingBox,
    *,
    y_origin: str,
    padding_ratio: float,
    glyph_size: int,
) -> dict[str, Any] | None:
    bounds = _image_crop_bounds(
        box,
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
        return None

    content_bbox = _mask_bbox(mask, width, height)
    if content_bbox is None:
        return None

    left, top, right, bottom = content_bbox
    content_width = max(right - left + 1, 1)
    content_height = max(bottom - top + 1, 1)
    edge_values = list(gray.filter(ImageFilter.FIND_EDGES).tobytes())

    metrics = {
        "ink_ratio": ink_pixels / total_pixels,
        "edge_density": (
            sum(1 for value in edge_values if value >= 32) / total_pixels
        ),
        "horizontal_transition_rate": _horizontal_transition_rate(
            mask, width, height
        ),
        "vertical_transition_rate": _vertical_transition_rate(
            mask, width, height
        ),
        "content_width_ratio": content_width / width,
        "content_height_ratio": content_height / height,
        "content_aspect": content_width / content_height,
        "top_ink_ratio": _mask_density(
            mask, width, height, 0, 0, width, height // 3
        ),
        "middle_ink_ratio": _mask_density(
            mask, width, height, 0, height // 3, width, (height * 2) // 3
        ),
        "bottom_ink_ratio": _mask_density(
            mask, width, height, 0, (height * 2) // 3, width, height
        ),
        "left_ink_ratio": _mask_density(
            mask, width, height, 0, 0, width // 3, height
        ),
        "center_ink_ratio": _mask_density(
            mask, width, height, width // 3, 0, (width * 2) // 3, height
        ),
        "right_ink_ratio": _mask_density(
            mask, width, height, (width * 2) // 3, 0, width, height
        ),
    }
    metrics.update(
        _projection_metrics(
            mask,
            width=width,
            height=height,
            bins=_PROJECTION_BINS,
        )
    )
    metrics.update(
        _hog_orientation_metrics(
            gray,
            width=width,
            height=height,
            bins=_HOG_BINS,
        )
    )
    metrics.update(
        _stroke_width_metrics(
            mask,
            width=width,
            height=height,
            content_bbox=content_bbox,
        )
    )
    metrics.update(
        _contour_profile_metrics(
            mask,
            width=width,
            height=height,
            content_bbox=content_bbox,
        )
    )
    metrics.update(
        _component_metrics(
            mask,
            width=width,
            height=height,
            content_bbox=content_bbox,
        )
    )
    glyph_vector = _normalized_glyph_vector(
        mask,
        width=width,
        height=height,
        content_bbox=content_bbox,
        glyph_size=glyph_size,
    )
    return {"glyph_vector": glyph_vector, "metrics": metrics}


def _projection_metrics(
    mask: Sequence[int], *, width: int, height: int, bins: int
) -> dict[str, float]:
    metrics = {}
    for bin_index in range(bins):
        top = (height * bin_index) // bins
        bottom = (height * (bin_index + 1)) // bins
        left = (width * bin_index) // bins
        right = (width * (bin_index + 1)) // bins
        metrics[f"row_projection_{bin_index}"] = _mask_density(
            mask, width, height, 0, top, width, bottom
        )
        metrics[f"column_projection_{bin_index}"] = _mask_density(
            mask, width, height, left, 0, right, height
        )
    return metrics


def _hog_orientation_metrics(
    gray: PILImage.Image, *, width: int, height: int, bins: int
) -> dict[str, float]:
    values = list(gray.tobytes())
    histogram = [0.0 for _ in range(bins)]
    if width < 3 or height < 3:
        return {f"hog_orientation_{index}": 0.0 for index in range(bins)}

    for y in range(1, height - 1):
        offset = y * width
        for x in range(1, width - 1):
            gx = values[offset + x + 1] - values[offset + x - 1]
            gy = values[offset + width + x] - values[offset - width + x]
            magnitude = (gx * gx + gy * gy) ** 0.5
            if magnitude <= _EPSILON:
                continue
            angle = atan2(gy, gx) % pi
            bin_index = min(int((angle / pi) * bins), bins - 1)
            histogram[bin_index] += magnitude

    total = sum(histogram)
    if total <= _EPSILON:
        return {f"hog_orientation_{index}": 0.0 for index in range(bins)}
    return {
        f"hog_orientation_{index}": value / total
        for index, value in enumerate(histogram)
    }


def _stroke_width_metrics(
    mask: Sequence[int],
    *,
    width: int,
    height: int,
    content_bbox: tuple[int, int, int, int],
) -> dict[str, float]:
    left, top, right, bottom = content_bbox
    horizontal_runs = _ink_run_lengths(mask, width=width, height=height)
    vertical_runs = _ink_run_lengths(
        mask, width=width, height=height, transpose=True
    )
    local_widths = []
    for y in range(top, bottom + 1):
        row_offset = y * width
        for x in range(left, right + 1):
            index = row_offset + x
            if not mask[index]:
                continue
            local_widths.append(
                min(horizontal_runs[index], vertical_runs[index])
            )

    if not local_widths:
        return {
            "stroke_width_mean": 0.0,
            "stroke_width_std": 0.0,
            "stroke_width_max": 0.0,
            "stroke_width_mean_norm": 0.0,
            "stroke_width_max_norm": 0.0,
        }

    width_mean = mean(local_widths)
    width_std = _std(local_widths, width_mean)
    width_max = max(local_widths)
    content_height = max(bottom - top + 1, 1)
    return {
        "stroke_width_mean": width_mean,
        "stroke_width_std": width_std,
        "stroke_width_max": float(width_max),
        "stroke_width_mean_norm": width_mean / content_height,
        "stroke_width_max_norm": width_max / content_height,
    }


def _ink_run_lengths(
    mask: Sequence[int],
    *,
    width: int,
    height: int,
    transpose: bool = False,
) -> list[int]:
    runs = [0 for _ in mask]
    outer = width if transpose else height
    inner = height if transpose else width
    for outer_index in range(outer):
        start: int | None = None
        for inner_index in range(inner + 1):
            if inner_index < inner:
                x = outer_index if transpose else inner_index
                y = inner_index if transpose else outer_index
                index = y * width + x
                is_ink = bool(mask[index])
            else:
                index = -1
                is_ink = False

            if is_ink and start is None:
                start = inner_index
            elif not is_ink and start is not None:
                run_length = inner_index - start
                for run_index in range(start, inner_index):
                    x = outer_index if transpose else run_index
                    y = run_index if transpose else outer_index
                    runs[y * width + x] = run_length
                start = None
    return runs


def _contour_profile_metrics(
    mask: Sequence[int],
    *,
    width: int,
    height: int,
    content_bbox: tuple[int, int, int, int],
) -> dict[str, float]:
    del height
    left, top, right, bottom = content_bbox
    content_width = max(right - left + 1, 1)
    content_height = max(bottom - top + 1, 1)
    top_profile = []
    bottom_profile = []
    for x in range(left, right + 1):
        ys = [y for y in range(top, bottom + 1) if mask[y * width + x]]
        if not ys:
            continue
        top_profile.append((min(ys) - top) / content_height)
        bottom_profile.append((bottom - max(ys)) / content_height)

    left_profile = []
    right_profile = []
    for y in range(top, bottom + 1):
        xs = [x for x in range(left, right + 1) if mask[y * width + x]]
        if not xs:
            continue
        left_profile.append((min(xs) - left) / content_width)
        right_profile.append((right - max(xs)) / content_width)

    top_mean, top_std = _mean_std(top_profile)
    bottom_mean, bottom_std = _mean_std(bottom_profile)
    left_mean, left_std = _mean_std(left_profile)
    right_mean, right_std = _mean_std(right_profile)
    return {
        "top_profile_mean": top_mean,
        "top_profile_std": top_std,
        "bottom_profile_mean": bottom_mean,
        "bottom_profile_std": bottom_std,
        "left_profile_mean": left_mean,
        "left_profile_std": left_std,
        "right_profile_mean": right_mean,
        "right_profile_std": right_std,
        "vertical_symmetry": _binary_symmetry(
            mask,
            width=width,
            content_bbox=content_bbox,
            axis="vertical",
        ),
        "horizontal_symmetry": _binary_symmetry(
            mask,
            width=width,
            content_bbox=content_bbox,
            axis="horizontal",
        ),
    }


def _binary_symmetry(
    mask: Sequence[int],
    *,
    width: int,
    content_bbox: tuple[int, int, int, int],
    axis: str,
) -> float:
    left, top, right, bottom = content_bbox
    differences = 0
    comparisons = 0
    if axis == "vertical":
        for y in range(top, bottom + 1):
            for x in range(left, right + 1):
                mirrored_x = right - (x - left)
                differences += (
                    mask[y * width + x] != mask[y * width + mirrored_x]
                )
                comparisons += 1
    elif axis == "horizontal":
        for y in range(top, bottom + 1):
            mirrored_y = bottom - (y - top)
            for x in range(left, right + 1):
                differences += (
                    mask[y * width + x] != mask[mirrored_y * width + x]
                )
                comparisons += 1
    else:
        return 0.0
    if comparisons == 0:
        return 0.0
    return 1.0 - (differences / comparisons)


def _component_metrics(
    mask: Sequence[int],
    *,
    width: int,
    height: int,
    content_bbox: tuple[int, int, int, int],
) -> dict[str, float]:
    del height
    left, top, right, bottom = content_bbox
    content_area = max((right - left + 1) * (bottom - top + 1), 1)
    component_sizes = _connected_component_sizes(
        mask,
        width=width,
        content_bbox=content_bbox,
        target=1,
        connectivity=8,
    )
    hole_sizes = _hole_sizes(mask, width=width, content_bbox=content_bbox)
    return {
        "component_count": float(len(component_sizes)),
        "largest_component_ratio": (
            max(component_sizes) / sum(component_sizes)
            if component_sizes
            else 0.0
        ),
        "hole_count": float(len(hole_sizes)),
        "hole_area_ratio": sum(hole_sizes) / content_area,
    }


def _connected_component_sizes(
    mask: Sequence[int],
    *,
    width: int,
    content_bbox: tuple[int, int, int, int],
    target: int,
    connectivity: int,
) -> list[int]:
    left, top, right, bottom = content_bbox
    visited: set[tuple[int, int]] = set()
    sizes = []
    if connectivity == 8:
        neighbors = (
            (-1, -1),
            (0, -1),
            (1, -1),
            (-1, 0),
            (1, 0),
            (-1, 1),
            (0, 1),
            (1, 1),
        )
    else:
        neighbors = ((0, -1), (-1, 0), (1, 0), (0, 1))

    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            if (x, y) in visited or mask[y * width + x] != target:
                continue
            stack = [(x, y)]
            visited.add((x, y))
            size = 0
            while stack:
                cx, cy = stack.pop()
                size += 1
                for dx, dy in neighbors:
                    nx = cx + dx
                    ny = cy + dy
                    if (
                        nx < left
                        or nx > right
                        or ny < top
                        or ny > bottom
                        or (nx, ny) in visited
                        or mask[ny * width + nx] != target
                    ):
                        continue
                    visited.add((nx, ny))
                    stack.append((nx, ny))
            sizes.append(size)
    return sizes


def _hole_sizes(
    mask: Sequence[int],
    *,
    width: int,
    content_bbox: tuple[int, int, int, int],
) -> list[int]:
    left, top, right, bottom = content_bbox
    visited: set[tuple[int, int]] = set()
    holes = []
    neighbors = ((0, -1), (-1, 0), (1, 0), (0, 1))

    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            if (x, y) in visited or mask[y * width + x]:
                continue
            stack = [(x, y)]
            visited.add((x, y))
            size = 0
            touches_border = False
            while stack:
                cx, cy = stack.pop()
                size += 1
                touches_border = touches_border or (
                    cx in (left, right) or cy in (top, bottom)
                )
                for dx, dy in neighbors:
                    nx = cx + dx
                    ny = cy + dy
                    if (
                        nx < left
                        or nx > right
                        or ny < top
                        or ny > bottom
                        or (nx, ny) in visited
                        or mask[ny * width + nx]
                    ):
                        continue
                    visited.add((nx, ny))
                    stack.append((nx, ny))
            if not touches_border:
                holes.append(size)
    return holes


def _normalized_glyph_vector(
    mask: Sequence[int],
    *,
    width: int,
    height: int,
    content_bbox: tuple[int, int, int, int],
    glyph_size: int,
) -> tuple[float, ...]:
    values = bytes(255 if value else 0 for value in mask)
    mask_image = PILImage.frombytes("L", (width, height), values)
    left, top, right, bottom = content_bbox
    content = mask_image.crop((left, top, right + 1, bottom + 1))
    side = max(content.width, content.height, 1)
    square = PILImage.new("L", (side, side), 0)
    offset = ((side - content.width) // 2, (side - content.height) // 2)
    square.paste(content, offset)
    resized = square.resize((glyph_size, glyph_size), PILImage.Resampling.BOX)
    return tuple(pixel / 255.0 for pixel in resized.tobytes())


def _with_style_vectors(
    records: Sequence[dict[str, Any]],
) -> tuple[LetterImageSample, ...]:
    if not records:
        return ()

    vectors_by_char: dict[str, list[tuple[float, ...]]] = {}
    for record in records:
        vectors_by_char.setdefault(record["normalized_char"], []).append(
            record["vector"]
        )
    char_centers = {
        char: _centroid(vectors)
        for char, vectors in vectors_by_char.items()
        if len(vectors) >= 3
    }
    global_center = _centroid([record["vector"] for record in records])

    samples = []
    for record in records:
        center = char_centers.get(record["normalized_char"], global_center)
        residual = tuple(
            value - center[index]
            for index, value in enumerate(record["vector"])
        )
        absolute_metrics = tuple(
            record["metrics"][name] for name in _ABSOLUTE_STYLE_NAMES
        )
        style_vector = _l2_normalize(residual + absolute_metrics)
        sample_id = _letter_sample_id(record)
        samples.append(
            LetterImageSample(
                sample_id=sample_id,
                image_id=record["image_id"],
                receipt_id=record["receipt_id"],
                line_id=record["line_id"],
                word_id=record["word_id"],
                letter_id=record["letter_id"],
                text=record["text"],
                normalized_char=record["normalized_char"],
                vector=record["vector"],
                style_vector=style_vector,
                metrics=record["metrics"],
            )
        )
    return tuple(samples)


def _build_letter_analysis(
    samples: Sequence[LetterImageSample],
    labels: Sequence[int],
) -> LetterFontAnalysis:
    grouped: dict[int, list[LetterImageSample]] = {}
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
    clusters = tuple(
        _make_letter_cluster(relabel[old_label], grouped[old_label])
        for old_label in ordered_labels
    )
    return LetterFontAnalysis(
        samples=tuple(samples),
        clusters=clusters,
        noise_sample_ids=tuple(noise),
        assignments=assignments,
    )


def _build_line_analysis(
    samples: Sequence[LineFontSample],
    labels: Sequence[int],
    prepared_vectors: Sequence[Sequence[float]],
) -> LineFontAnalysis:
    grouped: dict[int, list[tuple[int, LineFontSample]]] = {}
    noise = []
    for index, (sample, label) in enumerate(zip(samples, labels)):
        if label > 0:
            grouped.setdefault(label, []).append((index, sample))
        else:
            noise.append(sample.sample_id)

    ordered_labels = sorted(
        grouped,
        key=lambda label: min(index for index, _ in grouped[label]),
    )
    relabel = {
        old_label: new_label
        for new_label, old_label in enumerate(ordered_labels, start=1)
    }
    assignments = {
        sample.sample_id: relabel[label] if label > 0 else -1
        for sample, label in zip(samples, labels)
    }
    clusters = tuple(
        _make_line_cluster(
            relabel[old_label],
            [sample for _, sample in grouped[old_label]],
            [prepared_vectors[index] for index, _ in grouped[old_label]],
        )
        for old_label in ordered_labels
    )
    return LineFontAnalysis(
        samples=tuple(samples),
        clusters=clusters,
        noise_sample_ids=tuple(noise),
        assignments=assignments,
    )


def _cluster_vectors(
    vectors: Sequence[Sequence[float]], *, eps: float, min_samples: int
) -> list[int]:
    try:
        from sklearn.cluster import DBSCAN

        raw_labels = DBSCAN(eps=eps, min_samples=min_samples).fit_predict(
            list(vectors)
        )
        return [
            int(label) + 1 if int(label) >= 0 else -1 for label in raw_labels
        ]
    except Exception:
        return _dbscan(vectors, eps=eps, min_samples=min_samples)


def _make_letter_cluster(
    cluster_id: int,
    samples: Sequence[LetterImageSample],
) -> LetterStyleCluster:
    metrics = _average_metrics(samples)
    return LetterStyleCluster(
        cluster_id=cluster_id,
        sample_ids=tuple(sample.sample_id for sample in samples),
        sample_count=len(samples),
        line_ids=tuple(sorted({sample.line_id for sample in samples})),
        normalized_chars=tuple(
            char
            for char, _ in _top_counts(
                sample.normalized_char for sample in samples
            )
        ),
        text_examples=tuple(sample.text for sample in samples[:8]),
        centroid=_centroid([sample.style_vector for sample in samples]),
        metrics=metrics,
        label=_describe_letter_cluster(metrics),
    )


def _make_line_cluster(
    cluster_id: int,
    samples: Sequence[LineFontSample],
    vectors: Sequence[Sequence[float]],
) -> LineFontCluster:
    metrics = _average_metric_dicts([sample.metrics for sample in samples])
    sections = tuple(
        section
        for section, _ in _top_counts(
            [sample.section or "unknown" for sample in samples]
        )
    )
    return LineFontCluster(
        cluster_id=cluster_id,
        sample_ids=tuple(sample.sample_id for sample in samples),
        sample_count=len(samples),
        line_ids=tuple(sorted({sample.line_id for sample in samples})),
        sections=sections,
        text_examples=tuple(sample.text for sample in samples[:8]),
        centroid=_centroid(vectors),
        metrics=metrics,
        label=_describe_line_cluster(metrics),
    )


def _average_metrics(
    samples: Sequence[LetterImageSample],
) -> dict[str, float]:
    metric_names = sorted(
        {
            metric_name
            for sample in samples
            for metric_name in sample.metrics.keys()
        }
    )
    return {
        name: mean(
            sample.metrics[name]
            for sample in samples
            if name in sample.metrics
        )
        for name in metric_names
    }


def _average_metric_dicts(
    rows: Sequence[Mapping[str, float]],
) -> dict[str, float]:
    metric_names = sorted({name for row in rows for name in row.keys()})
    return {
        name: mean(row[name] for row in rows if name in row)
        for name in metric_names
    }


def _line_context_maps(
    lines: Sequence[Any] | None,
    words: Sequence[Any] | None,
) -> tuple[dict[LineKey, Any], dict[LineKey, list[Any]]]:
    line_by_key = {}
    for line in lines or ():
        line_by_key[_line_key_from_object(line)] = line

    words_by_key: dict[LineKey, list[Any]] = defaultdict(list)
    for word in words or ():
        words_by_key[_line_key_from_object(word)].append(word)
    for values in words_by_key.values():
        values.sort(key=lambda word: _int(_get(word, "word_id"), default=0))
    return line_by_key, words_by_key


def _line_metrics(
    samples: Sequence[LetterImageSample],
    *,
    line: Any | None,
    words: Sequence[Any],
    letter_assignments: Mapping[str, int] | None,
) -> dict[str, float]:
    line_box = _line_box(line, samples)
    median_height = _median_metric(samples, "box_height")
    spacing = _spacing_metrics(samples, median_height=median_height)
    char_mix = _character_mix_metrics(samples)
    cluster_mix = _letter_cluster_mix_metrics(samples, letter_assignments)
    word_count = len(words) or len({sample.word_id for sample in samples})
    letter_area = sum(
        sample.metrics.get("box_width", 0.0)
        * sample.metrics.get("box_height", 0.0)
        for sample in samples
    )
    line_area = max(line_box["width"] * line_box["height"], _EPSILON)

    metrics = {
        "letter_count": float(len(samples)),
        "word_count": float(word_count),
        "line_box_x": line_box["x"],
        "line_box_y": line_box["y"],
        "line_box_width": line_box["width"],
        "line_box_height": line_box["height"],
        "line_box_aspect": line_box["width"]
        / max(line_box["height"], _EPSILON),
        "line_center_x": line_box["x"] + line_box["width"] / 2,
        "line_center_y": line_box["y"] + line_box["height"] / 2,
        "letter_area_ratio": letter_area / line_area,
        **spacing,
        **char_mix,
        **cluster_mix,
    }
    metrics.update(_aggregated_letter_metrics(samples))
    return metrics


def _aggregated_letter_metrics(
    samples: Sequence[LetterImageSample],
) -> dict[str, float]:
    metrics = {}
    for name in _LINE_LETTER_AGGREGATE_SOURCE_NAMES:
        values = [sample.metrics.get(name, 0.0) for sample in samples]
        value_mean, value_std = _mean_std(values)
        metrics[f"mean_{name}"] = value_mean
        metrics[f"median_{name}"] = median(values) if values else 0.0
        metrics[f"std_{name}"] = value_std
    return metrics


def _line_box(
    line: Any | None, samples: Sequence[LetterImageSample]
) -> BoundingBox:
    box = _box(line) if line is not None else None
    if box is not None:
        return box

    left = min(sample.metrics.get("box_x", 0.0) for sample in samples)
    right = max(
        sample.metrics.get("box_x", 0.0) + sample.metrics.get("box_width", 0.0)
        for sample in samples
    )
    bottom = min(sample.metrics.get("box_y", 0.0) for sample in samples)
    top = max(
        sample.metrics.get("box_y", 0.0)
        + sample.metrics.get("box_height", 0.0)
        for sample in samples
    )
    return {
        "x": left,
        "y": bottom,
        "width": max(right - left, _EPSILON),
        "height": max(top - bottom, _EPSILON),
    }


def _spacing_metrics(
    samples: Sequence[LetterImageSample],
    *,
    median_height: float,
) -> dict[str, float]:
    ordered = sorted(samples, key=_letter_sort_value)
    gaps = []
    intra_word_gaps = []
    inter_word_gaps = []
    advances = []
    for current, following in zip(ordered, ordered[1:]):
        current_right = current.metrics.get(
            "box_x", 0.0
        ) + current.metrics.get("box_width", 0.0)
        gap = max(following.metrics.get("box_x", 0.0) - current_right, 0.0)
        advance = max(
            following.metrics.get("box_center_x", 0.0)
            - current.metrics.get("box_center_x", 0.0),
            0.0,
        )
        gaps.append(gap)
        advances.append(advance)
        if current.word_id == following.word_id:
            intra_word_gaps.append(gap)
        else:
            inter_word_gaps.append(gap)

    median_gap = median(gaps) if gaps else 0.0
    median_advance = median(advances) if advances else 0.0
    gap_mean, gap_std = _mean_std(gaps)
    return {
        "median_gap": median_gap,
        "mean_gap": gap_mean,
        "std_gap": gap_std,
        "median_intra_word_gap": (
            median(intra_word_gaps) if intra_word_gaps else 0.0
        ),
        "median_inter_word_gap": (
            median(inter_word_gaps) if inter_word_gaps else 0.0
        ),
        "median_advance": median_advance,
        "gap_to_height_ratio": median_gap / max(median_height, _EPSILON),
        "advance_to_height_ratio": median_advance
        / max(median_height, _EPSILON),
    }


def _character_mix_metrics(
    samples: Sequence[LetterImageSample],
) -> dict[str, float]:
    count = max(len(samples), 1)
    chars = [sample.text for sample in samples]
    alpha = sum(1 for char in chars if char.isalpha())
    uppercase = sum(1 for char in chars if char.isalpha() and char.isupper())
    digits = sum(1 for char in chars if char.isdigit())
    punctuation = sum(1 for char in chars if not char.isalnum())
    return {
        "alpha_ratio": alpha / count,
        "uppercase_ratio": uppercase / count,
        "digit_ratio": digits / count,
        "punctuation_ratio": punctuation / count,
    }


def _letter_cluster_mix_metrics(
    samples: Sequence[LetterImageSample],
    letter_assignments: Mapping[str, int] | None,
) -> dict[str, float]:
    if not letter_assignments:
        return {
            "assigned_letter_cluster_ratio": 0.0,
            "noise_letter_cluster_ratio": 1.0,
            "dominant_letter_cluster_ratio": 0.0,
            "distinct_letter_cluster_ratio": 0.0,
        }

    cluster_ids = [
        int(letter_assignments.get(sample.sample_id, -1)) for sample in samples
    ]
    assigned = [cluster_id for cluster_id in cluster_ids if cluster_id > 0]
    count = max(len(cluster_ids), 1)
    assigned_count = len(assigned)
    dominant_count = Counter(assigned).most_common(1)[0][1] if assigned else 0
    return {
        "assigned_letter_cluster_ratio": assigned_count / count,
        "noise_letter_cluster_ratio": (count - assigned_count) / count,
        "dominant_letter_cluster_ratio": dominant_count / count,
        "distinct_letter_cluster_ratio": len(set(assigned)) / count,
    }


def _line_text_for_key(
    key: LineKey,
    line: Any | None,
    words_by_key: Mapping[LineKey, Sequence[Any]],
    samples: Sequence[LetterImageSample],
) -> str:
    del key
    if line is not None:
        text = _text(line).strip()
        if text:
            return text
    words = words_by_key.get(_line_key_from_sample(samples[0]), ())
    word_text = " ".join(_text(word) for word in words).strip()
    if word_text:
        return word_text
    return "".join(
        sample.text for sample in sorted(samples, key=_letter_sort_value)
    )


def _lookup_line_mapping(
    mapping: Mapping[Any, str] | None,
    key: LineKey,
    line_id: int,
) -> str | None:
    if not mapping:
        return None
    if key in mapping:
        return mapping[key]
    key_text = _line_key_to_string(key)
    if key_text in mapping:
        return mapping[key_text]
    if line_id in mapping:
        return mapping[line_id]
    return None


def _line_key_from_sample(sample: LetterImageSample) -> LineKey:
    return (sample.image_id, sample.receipt_id, sample.line_id)


def _line_key_from_object(value: Any) -> LineKey:
    image_id = _get(value, "image_id")
    return (
        str(image_id) if image_id is not None else None,
        _optional_int(_get(value, "receipt_id")),
        _int(_get(value, "line_id"), default=-1),
    )


def _line_key_sort_value(key: LineKey) -> tuple[str, int, int]:
    return (key[0] or "", key[1] if key[1] is not None else -1, key[2])


def _line_sample_id(key: LineKey) -> str:
    parts = []
    if key[0]:
        parts.append(f"image={key[0]}")
    if key[1] is not None:
        parts.append(f"receipt={key[1]}")
    parts.append(f"line={key[2]}")
    return ":".join(parts)


def _line_key_to_string(key: LineKey) -> str:
    return _line_sample_id(key)


def _letter_sort_value(
    sample: LetterImageSample,
) -> tuple[float, int, int, int]:
    return (
        sample.metrics.get("box_x", 0.0),
        sample.word_id,
        sample.letter_id,
        sample.line_id,
    )


def _median_metric(samples: Sequence[LetterImageSample], name: str) -> float:
    values = [sample.metrics.get(name, 0.0) for sample in samples]
    return median(values) if values else 0.0


def _componentwise_std(
    vectors: Sequence[Sequence[float]],
    center: Sequence[float],
) -> tuple[float, ...]:
    if not vectors:
        return ()
    return tuple(
        _std([vector[index] for vector in vectors], center[index])
        for index in range(len(center))
    )


def _robust_standardized_l2_vectors(
    vectors: Sequence[Sequence[float]],
) -> list[tuple[float, ...]]:
    if not vectors:
        return []

    columns = list(zip(*vectors))
    centers = [median(column) for column in columns]
    scales = []
    for column, center in zip(columns, centers):
        deviations = [abs(value - center) for value in column]
        scale = median(deviations) * 1.4826
        scales.append(scale if scale > _EPSILON else 1.0)

    standardized = []
    for vector in vectors:
        standardized.append(
            _l2_normalize(
                tuple(
                    (value - centers[index]) / scales[index]
                    for index, value in enumerate(vector)
                )
            )
        )
    return standardized


def _describe_letter_cluster(metrics: Mapping[str, float]) -> str:
    height = metrics.get("box_height", 0.0)
    aspect = metrics.get("box_aspect", 0.0)
    ink = metrics.get("ink_ratio", 0.0)
    edge = metrics.get("edge_density", 0.0)

    if height >= 0.035:
        size = "large"
    elif height <= 0.014:
        size = "small"
    else:
        size = "regular"

    if aspect <= 0.45:
        width = "condensed"
    elif aspect >= 0.85:
        width = "wide"
    else:
        width = "normal"

    if ink >= 0.24 or edge >= 0.32:
        weight = "dark"
    elif ink <= 0.09:
        weight = "light"
    else:
        weight = "medium"

    return f"{size} {width} {weight}"


def _describe_line_cluster(metrics: Mapping[str, float]) -> str:
    height = metrics.get(
        "median_box_height", metrics.get("line_box_height", 0.0)
    )
    aspect = metrics.get("mean_box_aspect", 0.0)
    ink = metrics.get("mean_ink_ratio", 0.0)
    spacing = metrics.get("gap_to_height_ratio", 0.0)

    if height >= 0.035:
        size = "large-line"
    elif height <= 0.014:
        size = "small-line"
    else:
        size = "regular-line"

    if aspect <= 0.45:
        width = "condensed"
    elif aspect >= 0.85:
        width = "wide"
    else:
        width = "normal"

    if ink >= 0.24:
        weight = "dark"
    elif ink <= 0.09:
        weight = "light"
    else:
        weight = "medium"

    if spacing >= 0.75:
        cadence = "loose"
    elif spacing <= 0.18:
        cadence = "tight"
    else:
        cadence = "regular"

    return f"{size} {width} {weight} {cadence}"


def _promote_large_noise_groups(
    labels: Sequence[int],
    samples: Sequence[LetterImageSample],
    *,
    min_group_size: int,
) -> list[int]:
    next_cluster = max([label for label in labels if label > 0], default=0) + 1
    promoted = list(labels)
    noise_by_line: dict[int, list[int]] = {}
    for index, (label, sample) in enumerate(zip(labels, samples)):
        if label > 0:
            continue
        noise_by_line.setdefault(sample.line_id, []).append(index)

    for indexes in noise_by_line.values():
        if len(indexes) < min_group_size:
            continue
        for index in indexes:
            promoted[index] = next_cluster
        next_cluster += 1
    return promoted


def _sample_metadata(sample: LetterImageSample) -> dict[str, object]:
    metadata: dict[str, object] = {
        "ocr_char": sample.text,
        "normalized_char": sample.normalized_char,
        "line_id": sample.line_id,
        "word_id": sample.word_id,
        "letter_id": sample.letter_id,
        "confidence": sample.metrics.get("confidence", 0.0),
        "ink_ratio": sample.metrics.get("ink_ratio", 0.0),
        "box_height": sample.metrics.get("box_height", 0.0),
        "box_width": sample.metrics.get("box_width", 0.0),
    }
    if sample.image_id is not None:
        metadata["image_id"] = sample.image_id
    if sample.receipt_id is not None:
        metadata["receipt_id"] = sample.receipt_id
    return metadata


def _top_counts(values: Sequence[str]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _letter_sample_id(record: Mapping[str, Any]) -> str:
    parts = []
    if record.get("image_id"):
        parts.append(f"image={record['image_id']}")
    if record.get("receipt_id") is not None:
        parts.append(f"receipt={record['receipt_id']}")
    parts.append(f"line={record['line_id']}")
    parts.append(f"word={record['word_id']}")
    parts.append(f"letter={record['letter_id']}")
    return ":".join(parts)


def _normalize_char(char: str) -> str:
    return char.strip()


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return _int(value)


def _l2_normalize(values: Sequence[float]) -> tuple[float, ...]:
    norm = sum(value * value for value in values) ** 0.5
    if norm <= _EPSILON:
        return tuple(0.0 for _ in values)
    return tuple(value / norm for value in values)


def _mean_std(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    value_mean = mean(values)
    return value_mean, _std(values, value_mean)


def _std(values: Sequence[float], value_mean: float) -> float:
    if len(values) < 2:
        return 0.0
    variance = sum((value - value_mean) ** 2 for value in values) / len(values)
    return variance**0.5
