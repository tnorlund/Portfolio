"""Pillow-only visual evidence for receipt re-segmentation plans."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter

WordRef = tuple[int, int]

_COLORS = (
    (35, 131, 226),
    (243, 143, 46),
    (39, 174, 96),
    (151, 84, 196),
    (231, 76, 60),
    (22, 160, 133),
)
_DISCARD_COLOR = (230, 61, 151)


def _value(entity: Any, name: str, default: Any = None) -> Any:
    if isinstance(entity, Mapping):
        return entity.get(name, default)
    return getattr(entity, name, default)


def _word_ref(entity: Any) -> WordRef:
    return int(_value(entity, "line_id")), int(_value(entity, "word_id"))


def _polygon(entity: Any, image_height: int) -> list[tuple[float, float]]:
    points = []
    for name in ("top_left", "top_right", "bottom_right", "bottom_left"):
        point = _value(entity, name, {}) or {}
        points.append((float(point["x"]), image_height - float(point["y"])))
    return points


def _centroid(points: Sequence[tuple[float, float]]) -> tuple[float, float]:
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _cross(
    origin: tuple[float, float],
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    return (first[0] - origin[0]) * (second[1] - origin[1]) - (
        first[1] - origin[1]
    ) * (second[0] - origin[0])


def _convex_hull(
    points: Iterable[tuple[float, float]],
) -> list[tuple[float, float]]:
    unique = sorted(set(points))
    if len(unique) <= 2:
        return unique
    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _point_in_mask(mask: Image.Image, point: tuple[float, float]) -> bool:
    x = min(max(int(round(point[0])), 0), mask.width - 1)
    y = min(max(int(round(point[1])), 0), mask.height - 1)
    return bool(mask.getpixel((x, y)))


def _mask_coverage(
    mask: Image.Image, polygon: Sequence[tuple[float, float]]
) -> float:
    if len(polygon) < 3:
        return 0.0
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    left = max(0, int(math.floor(min(xs))))
    top = max(0, int(math.floor(min(ys))))
    right = min(mask.width, int(math.ceil(max(xs))) + 1)
    bottom = min(mask.height, int(math.ceil(max(ys))) + 1)
    if left >= right or top >= bottom:
        return 0.0
    word_mask = Image.new("1", (right - left, bottom - top), 0)
    ImageDraw.Draw(word_mask).polygon(
        [(x - left, y - top) for x, y in polygon], fill=1
    )
    denominator = sum(word_mask.getdata())
    if not denominator:
        return 0.0
    segment_crop = mask.crop((left, top, right, bottom)).convert("1")
    numerator = sum(ImageChops.logical_and(word_mask, segment_crop).getdata())
    return numerator / denominator


def _dilate_and_smooth(mask: Image.Image, padding_px: int) -> Image.Image:
    result = mask
    remaining = max(0, int(padding_px))
    while remaining:
        radius = min(remaining, 15)
        result = result.filter(ImageFilter.MaxFilter(radius * 2 + 1))
        remaining -= radius
    if padding_px:
        result = result.filter(
            ImageFilter.GaussianBlur(min(3.0, padding_px / 4))
        )
    return result.point(lambda value: 255 if value >= 96 else 0, mode="L")


def _explicit_region_points(
    region: Any, width: int, height: int
) -> list[tuple[float, float]]:
    raw_points = (
        region.get("points", ()) if isinstance(region, Mapping) else region
    )
    points = []
    for raw_point in raw_points:
        if isinstance(raw_point, Mapping):
            x = float(raw_point["x"])
            y = float(raw_point["y"])
        else:
            x, y = (float(value) for value in raw_point)
        if (
            not math.isfinite(x)
            or not math.isfinite(y)
            or not 0 <= x <= 1
            or not 0 <= y <= 1
        ):
            raise ValueError(
                "visible region points must be finite normalized coordinates"
            )
        points.append((x * width, (1.0 - y) * height))
    if len(points) < 3:
        raise ValueError("visible regions require at least three points")
    return points


def _line_hulls(
    refs: set[WordRef],
    words_by_ref: Mapping[WordRef, Any],
    image_height: int,
) -> list[tuple[int, list[tuple[float, float]]]]:
    polygons_by_line: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for ref in refs:
        polygons_by_line[ref[0]].extend(
            _polygon(words_by_ref[ref], image_height)
        )
    return [
        (line_id, _convex_hull(points))
        for line_id, points in sorted(polygons_by_line.items())
    ]


def _cluster_line_hulls(
    line_hulls: Sequence[tuple[int, Sequence[tuple[float, float]]]],
    size: tuple[int, int],
) -> list[list[tuple[int, list[tuple[float, float]]]]]:
    """Group line hulls into vertically contiguous components."""
    valid_hulls = [
        (line_id, list(hull)) for line_id, hull in line_hulls if len(hull) >= 3
    ]
    if not valid_hulls:
        return []

    heights = [
        max(point[1] for point in hull) - min(point[1] for point in hull)
        for _, hull in valid_hulls
    ]
    gap_limit = max(4 * statistics.median(heights), 0.06 * min(size))
    ordered = sorted(valid_hulls, key=lambda item: _centroid(item[1])[1])
    components: list[list[tuple[int, list[tuple[float, float]]]]] = []
    for item in ordered:
        if not components:
            components.append([item])
            continue
        previous_y = _centroid(components[-1][-1][1])[1]
        current_y = _centroid(item[1])[1]
        if current_y - previous_y > gap_limit:
            components.append([item])
        else:
            components[-1].append(item)
    return components


def _candidate_mask(
    *,
    size: tuple[int, int],
    line_hulls: Sequence[tuple[int, Sequence[tuple[float, float]]]],
    padding_px: int,
) -> tuple[Image.Image, list[list[int]]]:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    components = _cluster_line_hulls(line_hulls, size)
    if not components:
        return mask, []

    for component in components:
        for _, hull in component:
            draw.polygon(hull, fill=255)
        for (_, first), (_, second) in zip(component, component[1:]):
            bridge = _convex_hull([*first, *second])
            if len(bridge) >= 3:
                draw.polygon(bridge, fill=255)
    return _dilate_and_smooth(mask, padding_px), [
        [line_id for line_id, _ in component] for component in components
    ]


def _line_evidence(
    lines: Sequence[Any],
    words_by_ref: Mapping[WordRef, Any],
    owner_by_ref: Mapping[WordRef, str | None],
    image_height: int,
    letters: Sequence[Any],
) -> list[dict[str, Any]]:
    words_by_line: dict[int, list[Any]] = defaultdict(list)
    for ref, word in words_by_ref.items():
        words_by_line[ref[0]].append(word)
    letters_by_line: dict[int, list[Any]] = defaultdict(list)
    for letter in letters:
        letters_by_line[int(_value(letter, "line_id"))].append(letter)
    evidence = []
    for line in sorted(lines, key=lambda value: int(_value(value, "line_id"))):
        line_id = int(_value(line, "line_id"))
        words = words_by_line.get(line_id, [])
        points = [
            point for word in words for point in _polygon(word, image_height)
        ]
        word_angles = []
        for word in words:
            bottom_left = _value(word, "bottom_left")
            bottom_right = _value(word, "bottom_right")
            word_angles.append(
                math.degrees(
                    math.atan2(
                        float(bottom_right["y"]) - float(bottom_left["y"]),
                        float(bottom_right["x"]) - float(bottom_left["x"]),
                    )
                )
            )
        letter_angles = []
        for letter in letters_by_line.get(line_id, ()):
            bottom_left = _value(letter, "bottom_left")
            bottom_right = _value(letter, "bottom_right")
            letter_angles.append(
                math.degrees(
                    math.atan2(
                        float(bottom_right["y"]) - float(bottom_left["y"]),
                        float(bottom_right["x"]) - float(bottom_left["x"]),
                    )
                )
            )
        angles = letter_angles or word_angles
        destinations = sorted(
            {
                owner_by_ref[_word_ref(word)] or "DISCARD"
                for word in words
                if _word_ref(word) in owner_by_ref
            }
        )
        evidence.append(
            {
                "line_id": line_id,
                "text": _value(line, "text", ""),
                "confidence": _value(line, "confidence"),
                "stored_angle_degrees": _value(line, "angle_degrees"),
                "derived_baseline_degrees": (
                    statistics.median(angles) if angles else None
                ),
                "derived_baseline_source": (
                    "LETTER_CORNERS"
                    if letter_angles
                    else "WORD_CORNERS" if word_angles else None
                ),
                "assigned_to": destinations,
                "letter_count": len(letters_by_line.get(line_id, ())),
                "word_refs": [
                    {
                        "line_id": line_id,
                        "word_id": int(_value(word, "word_id")),
                    }
                    for word in sorted(
                        words, key=lambda value: int(_value(value, "word_id"))
                    )
                ],
                "hull_px": [list(point) for point in _convex_hull(points)],
            }
        )
    return evidence


def _contact_sheet(
    overlay: Image.Image, visible_crops: Mapping[str, Image.Image]
) -> Image.Image:
    cards: list[tuple[str, Image.Image]] = [("assignment overlay", overlay)]
    cards.extend(
        (segment_key, image) for segment_key, image in visible_crops.items()
    )
    card_width = 640
    card_height = 460
    columns = 2
    rows = math.ceil(len(cards) / columns)
    sheet = Image.new(
        "RGB", (columns * card_width, rows * card_height), "white"
    )
    draw = ImageDraw.Draw(sheet)
    for index, (label, image) in enumerate(cards):
        column = index % columns
        row = index // columns
        x = column * card_width
        y = row * card_height
        draw.text((x + 12, y + 10), label, fill="black")
        preview = image.copy()
        preview.thumbnail((card_width - 24, card_height - 48))
        if preview.mode == "RGBA":
            background = Image.new("RGB", preview.size, "white")
            background.paste(preview, mask=preview.getchannel("A"))
            preview = background
        elif preview.mode != "RGB":
            preview = preview.convert("RGB")
        paste_x = x + (card_width - preview.width) // 2
        paste_y = y + 38 + (card_height - 48 - preview.height) // 2
        sheet.paste(preview, (paste_x, paste_y))
    sheet.thumbnail((1400, 1200))
    return sheet


def build_preview_bundle(
    original_image: Image.Image,
    *,
    image_type: str,
    strategy: str,
    lines: Sequence[Any],
    words_by_ref: Mapping[WordRef, Any],
    segments: Sequence[Mapping[str, Any]],
    discard_refs: set[WordRef],
    letters: Sequence[Any] = (),
    padding_px: int = 12,
) -> dict[str, Any]:
    """Render assignment evidence, masks, crops, metrics, and findings."""
    image = original_image.convert("RGB")
    owner_by_ref: dict[WordRef, str | None] = {
        ref: None for ref in discard_refs
    }
    segment_refs: dict[str, set[WordRef]] = {}
    for segment in segments:
        key = str(segment["segment_key"])
        refs = {
            (int(ref["line_id"]), int(ref["word_id"]))
            for ref in segment["word_refs"]
        }
        segment_refs[key] = refs
        owner_by_ref.update({ref: key for ref in refs})

    overlay_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay_layer)
    color_by_segment = {
        str(segment["segment_key"]): _COLORS[index % len(_COLORS)]
        for index, segment in enumerate(segments)
    }
    for ref, word in words_by_ref.items():
        owner = owner_by_ref.get(ref)
        color = _DISCARD_COLOR if owner is None else color_by_segment[owner]
        polygon = _polygon(word, image.height)
        overlay_draw.polygon(
            polygon, fill=(*color, 105), outline=(*color, 255)
        )
    overlay = Image.alpha_composite(
        image.convert("RGBA"), overlay_layer
    ).convert("RGB")

    candidates: dict[str, Image.Image] = {}
    component_lines: dict[str, list[list[int]]] = {}
    for segment in segments:
        key = str(segment["segment_key"])
        visible_regions = segment.get("visible_regions", ())
        if visible_regions:
            mask = Image.new("L", image.size, 0)
            draw = ImageDraw.Draw(mask)
            for region in visible_regions:
                draw.polygon(
                    _explicit_region_points(region, image.width, image.height),
                    fill=255,
                )
            candidates[key] = mask
            component_lines[key] = [
                (
                    sorted(
                        int(line_id) for line_id in region.get("line_ids", ())
                    )
                    if isinstance(region, Mapping)
                    else []
                )
                for region in visible_regions
            ]
        elif strategy == "RECTANGULAR" and segment.get("geometry"):
            mask = Image.new("L", image.size, 0)
            ImageDraw.Draw(mask).polygon(
                segment["geometry"]["src_corners"], fill=255
            )
            candidates[key] = mask
            # The rectangle always covers every assigned line, so
            # disconnection must be detected from the line geometry
            # itself, not from the mask.
            component_lines[key] = [
                sorted(line_id for line_id, _ in component)
                for component in _cluster_line_hulls(
                    _line_hulls(segment_refs[key], words_by_ref, image.height),
                    image.size,
                )
            ]
        else:
            candidates[key], component_lines[key] = _candidate_mask(
                size=image.size,
                line_hulls=_line_hulls(
                    segment_refs[key], words_by_ref, image.height
                ),
                padding_px=padding_px,
            )

    findings: list[dict[str, Any]] = []
    masks: dict[str, Image.Image] = {}
    foreground_union = Image.new("L", image.size, 0)
    ordered_segments = sorted(
        segments, key=lambda value: int(value.get("z_index", 0)), reverse=True
    )
    if strategy == "LAYERED_MULTI_REGION":
        for index, first in enumerate(ordered_segments):
            for second in ordered_segments[index + 1 :]:
                if int(first.get("z_index", 0)) != int(
                    second.get("z_index", 0)
                ):
                    continue
                first_key = str(first["segment_key"])
                second_key = str(second["segment_key"])
                if ImageChops.multiply(
                    candidates[first_key], candidates[second_key]
                ).getbbox():
                    findings.append(
                        {
                            "code": "AMBIGUOUS_LAYER_ORDER",
                            "severity": "BLOCKER",
                            "message": (
                                f"Segments {first_key} and {second_key} overlap "
                                "but have the same z_index."
                            ),
                        }
                    )
    for segment in ordered_segments:
        key = str(segment["segment_key"])
        candidate = candidates[key]
        if image_type == "PHOTO" and strategy == "LAYERED_MULTI_REGION":
            masks[key] = ImageChops.subtract(candidate, foreground_union)
            foreground_union = ImageChops.lighter(foreground_union, masks[key])
        else:
            masks[key] = candidate

    if strategy == "LAYERED_MULTI_REGION":
        findings.append(
            {
                "code": "LAYERED_APPLY_NOT_SUPPORTED",
                "severity": "BLOCKER",
                "message": (
                    "Layered PHOTO plans can be reviewed and revised, but apply "
                    "is blocked until masked output rendering is enabled."
                ),
            }
        )
    if image_type == "NATIVE" and strategy == "LAYERED_MULTI_REGION":
        findings.append(
            {
                "code": "NATIVE_LAYERING_UNSUPPORTED",
                "severity": "BLOCKER",
                "message": "NATIVE sources do not support layered segmentation.",
            }
        )

    segment_images: dict[str, dict[str, Image.Image]] = {}
    metrics: dict[str, Any] = {}
    visible_crops: dict[str, Image.Image] = {}
    letter_counts_by_ref = defaultdict(int)
    for letter in letters:
        letter_counts_by_ref[_word_ref(letter)] += 1
    for segment in segments:
        key = str(segment["segment_key"])
        mask = masks[key]
        rgba = image.convert("RGBA")
        rgba.putalpha(mask)
        bbox = mask.getbbox() or (0, 0, 1, 1)
        visible_crop = rgba.crop(bbox)
        visible_crops[key] = visible_crop
        segment_images[key] = {"mask": mask, "visible_crop": visible_crop}

        assigned_coverages = [
            _mask_coverage(mask, _polygon(words_by_ref[ref], image.height))
            for ref in sorted(segment_refs[key])
        ]
        retained = sum(
            _point_in_mask(
                mask, _centroid(_polygon(words_by_ref[ref], image.height))
            )
            for ref in segment_refs[key]
        )
        foreign_refs = set(words_by_ref) - segment_refs[key] - discard_refs
        foreign_centroids = sum(
            _point_in_mask(
                mask, _centroid(_polygon(words_by_ref[ref], image.height))
            )
            for ref in foreign_refs
        )
        if retained != len(segment_refs[key]):
            findings.append(
                {
                    "code": "ASSIGNED_WORD_CLIPPED",
                    "severity": "BLOCKER",
                    "segment_key": key,
                    "message": "One or more assigned word centroids are outside the mask.",
                }
            )
        if foreign_centroids:
            severity = "BLOCKER" if image_type == "SCAN" else "WARNING"
            findings.append(
                {
                    "code": "FOREIGN_WORD_CAPTURED",
                    "severity": severity,
                    "segment_key": key,
                    "message": f"The mask contains {foreign_centroids} foreign word centroids.",
                }
            )
        if len(component_lines[key]) > 1:
            findings.append(
                {
                    "code": "DISCONNECTED_VISIBLE_REGIONS",
                    "severity": (
                        "BLOCKER" if strategy == "RECTANGULAR" else "WARNING"
                    ),
                    "segment_key": key,
                    "message": (
                        f"The segment has {len(component_lines[key])} visible "
                        "components. A single rectangular output would include "
                        "the pixels between them."
                    ),
                }
            )
        metrics[key] = {
            "assigned_word_count": len(segment_refs[key]),
            "assigned_letter_count": sum(
                letter_counts_by_ref[ref] for ref in segment_refs[key]
            ),
            "word_centroids_retained": retained,
            "word_centroid_retention_ratio": (
                retained / len(segment_refs[key]) if segment_refs[key] else 0.0
            ),
            "mean_word_quad_coverage": (
                statistics.fmean(assigned_coverages)
                if assigned_coverages
                else 0.0
            ),
            "min_word_quad_coverage": min(assigned_coverages, default=0.0),
            "foreign_word_centroids_in_mask": foreign_centroids,
            "visible_region_count": len(component_lines[key]),
            "visible_region_line_ids": component_lines[key],
            "mask_area_px": sum(1 for value in mask.getdata() if value),
            "source_bbox_px": {
                "x": bbox[0],
                "y": bbox[1],
                "width": bbox[2] - bbox[0],
                "height": bbox[3] - bbox[1],
            },
        }

    return {
        "images": {
            "overlay": overlay,
            "contact_sheet": _contact_sheet(overlay, visible_crops),
            "segments": segment_images,
        },
        "metrics": metrics,
        "findings": findings,
        "evidence": {
            "lines": _line_evidence(
                lines, words_by_ref, owner_by_ref, image.height, letters
            )
        },
    }
