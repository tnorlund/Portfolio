"""Pure NumPy glyph normalization and calibrated shifted-IoU primitives."""

from __future__ import annotations

from collections import deque

import numpy as np


def normalize_glyph(bitmap: np.ndarray, size: int = 32) -> np.ndarray:
    """Crop to ink and resize aspect-preservingly into a square canvas."""

    source = np.asarray(bitmap, dtype=bool)
    if source.ndim != 2:
        raise ValueError("glyph bitmap must be two-dimensional")
    if not source.any():
        return np.zeros((size, size), dtype=bool)
    ys, xs = np.where(source)
    crop = source[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    height, width = crop.shape
    scale = size / max(height, width)
    target_height = max(1, round(height * scale))
    target_width = max(1, round(width * scale))
    y_index = np.minimum(
        (np.arange(target_height) * height) // target_height, height - 1
    )
    x_index = np.minimum(
        (np.arange(target_width) * width) // target_width, width - 1
    )
    resized = crop[np.ix_(y_index, x_index)]
    result: np.ndarray = np.zeros((size, size), dtype=bool)
    y_offset = (size - target_height) // 2
    x_offset = (size - target_width) // 2
    result[
        y_offset : y_offset + target_height,
        x_offset : x_offset + target_width,
    ] = resized
    return result


def _components(mask: np.ndarray) -> list[np.ndarray]:
    source = np.asarray(mask, dtype=bool)
    height, width = source.shape
    seen = np.zeros_like(source)
    result = []
    for y in range(height):
        for x in range(width):
            if not source[y, x] or seen[y, x]:
                continue
            queue = deque([(y, x)])
            seen[y, x] = True
            pixels = []
            while queue:
                current_y, current_x = queue.popleft()
                pixels.append((current_y, current_x))
                for next_y, next_x in (
                    (current_y - 1, current_x),
                    (current_y + 1, current_x),
                    (current_y, current_x - 1),
                    (current_y, current_x + 1),
                ):
                    if (
                        0 <= next_y < height
                        and 0 <= next_x < width
                        and source[next_y, next_x]
                        and not seen[next_y, next_x]
                    ):
                        seen[next_y, next_x] = True
                        queue.append((next_y, next_x))
            component = np.zeros_like(source)
            ys, xs = zip(*pixels)
            component[list(ys), list(xs)] = True
            result.append(component)
    return result


def clean_letter_mask(mask: np.ndarray) -> np.ndarray:
    """Remove neighbor bleed, specks, and rule fragments from a crop."""

    source = np.asarray(mask, dtype=bool)
    if not source.any():
        return source
    width = source.shape[1]
    components = _components(source)
    total = int(source.sum())
    kept = np.zeros_like(source)
    for component in components:
        ink = int(component.sum())
        ys, xs = np.where(component)
        center_x = float(xs.mean()) / max(1, width - 1)
        component_height = int(ys.max() - ys.min() + 1)
        component_width = int(xs.max() - xs.min() + 1)
        if ink < max(3, 0.02 * total):
            continue
        if center_x < 0.12 or center_x > 0.88:
            continue
        if component_height <= 2 and component_width >= 0.85 * width:
            continue
        kept |= component
    if not kept.any():
        return max(components, key=lambda component: int(component.sum()))
    return kept


def _shift(mask: np.ndarray, dy: int, dx: int) -> np.ndarray:
    result = np.zeros_like(mask)
    height, width = mask.shape
    y_start, y_end = max(0, dy), min(height, height + dy)
    x_start, x_end = max(0, dx), min(width, width + dx)
    result[y_start:y_end, x_start:x_end] = mask[
        max(0, -dy) : min(height, height - dy),
        max(0, -dx) : min(width, width - dx),
    ]
    return result


def shifted_iou(
    candidate: np.ndarray, reference: np.ndarray, max_shift: int = 2
) -> float:
    """Maximum IoU across small zero-filled integer translations."""

    candidate_mask = np.asarray(candidate, dtype=bool)
    reference_mask = np.asarray(reference, dtype=bool)
    if candidate_mask.shape != reference_mask.shape:
        raise ValueError("candidate and reference glyphs must share a shape")
    best = 0.0
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            shifted = _shift(candidate_mask, dy, dx)
            intersection = np.logical_and(shifted, reference_mask).sum()
            union = np.logical_or(shifted, reference_mask).sum()
            score = float(intersection / union) if union else 0.0
            best = max(best, score)
    return best


def shifted_iou_stack(
    candidate: np.ndarray,
    references: np.ndarray,
    max_shift: int = 2,
) -> np.ndarray:
    """Vectorized shifted-IoU against an ``(n, H, W)`` reference stack."""

    mask = np.asarray(candidate, dtype=bool)
    stack = np.asarray(references, dtype=bool)
    if stack.ndim != 3:
        raise ValueError("references must have shape (n, H, W)")
    if stack.shape[1:] != mask.shape:
        raise ValueError("candidate and reference glyphs must share a shape")
    if stack.shape[0] == 0:
        return np.zeros(0, dtype=float)
    shifts = np.stack(
        [
            _shift(mask, dy, dx)
            for dy in range(-max_shift, max_shift + 1)
            for dx in range(-max_shift, max_shift + 1)
        ]
    )
    intersection = np.einsum(
        "khw,nhw->kn", shifts.astype(np.int32), stack.astype(np.int32)
    )
    union = (
        shifts.sum(axis=(1, 2), dtype=np.int32)[:, None]
        + stack.sum(axis=(1, 2), dtype=np.int32)[None, :]
        - intersection
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        scores = np.where(union > 0, intersection / union, 0.0)
    return scores.max(axis=0)


__all__ = [
    "clean_letter_mask",
    "normalize_glyph",
    "shifted_iou",
    "shifted_iou_stack",
]
