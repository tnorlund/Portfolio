from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image

Point = tuple[int, int]
Edge = tuple[Point, Point]


@dataclass(frozen=True)
class VectorizeOptions:
    """Options for deterministic PNG-to-SVG path extraction."""

    alpha_threshold: int = 16
    opaque_alpha_threshold: int = 192
    max_colors: int = 4
    palette_merge_distance: float = 28.0
    simplify_tolerance: float = 1.25
    min_contour_area: float = 4.0
    title: str | None = None


@dataclass(frozen=True)
class PathLayer:
    fill: str
    rgb: tuple[int, int, int]
    pixel_count: int
    path_count: int
    point_count: int
    signed_area: float
    d: str


@dataclass(frozen=True)
class VectorizeResult:
    source_path: str
    width: int
    height: int
    title: str
    visible_pixel_count: int
    palette: tuple[str, ...]
    layers: tuple[PathLayer, ...]
    svg: str

    def manifest(self, svg_path: str | None = None) -> dict:
        payload = asdict(self)
        payload.pop("svg")
        payload["svg_path"] = svg_path
        payload["svg_bytes"] = len(self.svg.encode("utf-8"))
        payload["layers"] = [
            {key: value for key, value in asdict(layer).items() if key != "d"}
            for layer in self.layers
        ]
        return payload


def vectorize_logo(
    source_path: str | Path,
    options: VectorizeOptions | None = None,
) -> VectorizeResult:
    """Convert a transparent logo image into color-layer SVG paths."""

    opts = options or VectorizeOptions()
    source = Path(source_path).expanduser().resolve()
    image = Image.open(source).convert("RGBA")
    arr = np.asarray(image)
    alpha = arr[:, :, 3]
    visible = alpha > opts.alpha_threshold

    if not bool(visible.any()):
        raise ValueError(f"{source} has no pixels above alpha threshold")

    palette = _dominant_palette(arr, visible, opts)
    layer_ids = _assign_palette_layers(arr, visible, palette)
    layers: list[PathLayer] = []

    for layer_index, rgb in enumerate(palette):
        mask = layer_ids == layer_index
        pixel_count = int(mask.sum())
        if pixel_count == 0:
            continue

        contours = contours_from_mask(mask)
        filtered: list[list[Point]] = []
        signed_area = 0.0
        point_count = 0

        for contour in contours:
            contour = remove_collinear(contour)
            contour = simplify_closed_polygon(contour, opts.simplify_tolerance)
            area = polygon_area(contour)
            if abs(area) < opts.min_contour_area:
                continue
            filtered.append(contour)
            signed_area += area
            point_count += len(contour)

        if not filtered:
            continue

        layers.append(
            PathLayer(
                fill=rgb_to_hex(rgb),
                rgb=tuple(int(c) for c in rgb),
                pixel_count=pixel_count,
                path_count=len(filtered),
                point_count=point_count,
                signed_area=round(signed_area, 3),
                d=paths_to_d(filtered),
            )
        )

    title = opts.title or source.stem.replace("_", " ")
    svg = render_svg(image.width, image.height, title, layers)

    return VectorizeResult(
        source_path=str(source),
        width=image.width,
        height=image.height,
        title=title,
        visible_pixel_count=int(visible.sum()),
        palette=tuple(layer.fill for layer in layers),
        layers=tuple(layers),
        svg=svg,
    )


def write_vector_asset(
    result: VectorizeResult,
    output_dir: str | Path,
    slug: str,
) -> tuple[Path, Path]:
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    # A slug with separators or dot-segments must not escape output_dir
    # (the MCP/CLI accept arbitrary strings here).
    safe_slug = re.sub(r"[^A-Za-z0-9._-]", "_", Path(slug).name).strip(".")
    if not safe_slug:
        raise ValueError(f"unusable slug: {slug!r}")
    svg_path = (out / f"{safe_slug}.svg").resolve()
    manifest_path = (out / f"{safe_slug}.manifest.json").resolve()
    for candidate in (svg_path, manifest_path):
        candidate.relative_to(out)
    svg_path.write_text(result.svg, encoding="utf-8")
    manifest_path.write_text(
        json.dumps(result.manifest(str(svg_path)), indent=2) + "\n",
        encoding="utf-8",
    )
    return svg_path, manifest_path


def render_svg(
    width: int,
    height: int,
    title: str,
    layers: Iterable[PathLayer],
    fill_override: str | None = None,
) -> str:
    safe_title = xml_escape(title)
    lines = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width} {height}" role="img" '
            f'aria-labelledby="title">'
        ),
        f'  <title id="title">{safe_title}</title>',
    ]
    for layer in layers:
        fill = fill_override or layer.fill
        lines.append(
            "  "
            f'<path fill="{fill}" fill-rule="evenodd" '
            f'clip-rule="evenodd" d="{layer.d}"/>'
        )
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def contours_from_mask(mask: np.ndarray) -> list[list[Point]]:
    """Trace boundary loops for a binary mask using oriented pixel edges."""

    if mask.ndim != 2:
        raise ValueError("mask must be 2D")

    height, width = mask.shape
    outgoing: dict[Point, set[Point]] = defaultdict(set)
    unused: set[Edge] = set()

    ys, xs = np.nonzero(mask)
    for y_raw, x_raw in zip(ys.tolist(), xs.tolist(), strict=True):
        y = int(y_raw)
        x = int(x_raw)
        if y == 0 or not mask[y - 1, x]:
            _add_edge(unused, outgoing, (x, y), (x + 1, y))
        if x == width - 1 or not mask[y, x + 1]:
            _add_edge(unused, outgoing, (x + 1, y), (x + 1, y + 1))
        if y == height - 1 or not mask[y + 1, x]:
            _add_edge(unused, outgoing, (x + 1, y + 1), (x, y + 1))
        if x == 0 or not mask[y, x - 1]:
            _add_edge(unused, outgoing, (x, y + 1), (x, y))

    contours: list[list[Point]] = []
    while unused:
        start, end = next(iter(unused))
        unused.remove((start, end))
        contour = [start, end]
        previous = start
        current = end

        guard = 0
        max_steps = len(unused) + 2
        while current != start and guard <= max_steps:
            guard += 1
            candidates = [
                candidate
                for candidate in outgoing.get(current, set())
                if (current, candidate) in unused
            ]
            if not candidates:
                break
            chosen = _choose_next(previous, current, candidates)
            unused.remove((current, chosen))
            contour.append(chosen)
            previous, current = current, chosen

        if len(contour) > 3 and contour[-1] == start:
            contour.pop()
            contours.append(contour)

    return contours


def remove_collinear(points: list[Point]) -> list[Point]:
    if len(points) <= 3:
        return points
    output: list[Point] = []
    count = len(points)
    for index, point in enumerate(points):
        prev_point = points[(index - 1) % count]
        next_point = points[(index + 1) % count]
        v1 = (point[0] - prev_point[0], point[1] - prev_point[1])
        v2 = (next_point[0] - point[0], next_point[1] - point[1])
        if v1[0] * v2[1] - v1[1] * v2[0] != 0:
            output.append(point)
    return output


def simplify_closed_polygon(
    points: list[Point], tolerance: float
) -> list[Point]:
    if tolerance <= 0 or len(points) <= 4:
        return points
    closed = points + [points[0]]
    simplified = _rdp(closed, tolerance)
    if simplified and simplified[-1] == simplified[0]:
        simplified = simplified[:-1]
    return simplified if len(simplified) >= 3 else points


def polygon_area(points: list[Point]) -> float:
    area = 0.0
    for index, point in enumerate(points):
        next_point = points[(index + 1) % len(points)]
        area += point[0] * next_point[1] - next_point[0] * point[1]
    return area / 2.0


def paths_to_d(contours: Iterable[list[Point]]) -> str:
    parts: list[str] = []
    for contour in contours:
        first = contour[0]
        parts.append(f"M{first[0]} {first[1]}")
        for x, y in contour[1:]:
            parts.append(f"L{x} {y}")
        parts.append("Z")
    return " ".join(parts)


def rgb_to_hex(rgb: tuple[int, int, int] | np.ndarray) -> str:
    r, g, b = (int(channel) for channel in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _dominant_palette(
    arr: np.ndarray,
    visible: np.ndarray,
    opts: VectorizeOptions,
) -> list[tuple[int, int, int]]:
    alpha = arr[:, :, 3]
    palette_source = visible & (alpha >= opts.opaque_alpha_threshold)
    if not bool(palette_source.any()):
        palette_source = visible

    pixels = arr[:, :, :3][palette_source]
    counts = Counter(tuple(int(c) for c in pixel) for pixel in pixels)
    palette: list[tuple[int, int, int]] = []

    for rgb, _count in counts.most_common():
        if all(
            _rgb_distance(rgb, existing) >= opts.palette_merge_distance
            for existing in palette
        ):
            palette.append(rgb)
        if len(palette) >= opts.max_colors:
            break

    if not palette:
        raise ValueError("could not extract a visible color palette")
    return palette


def _assign_palette_layers(
    arr: np.ndarray,
    visible: np.ndarray,
    palette: list[tuple[int, int, int]],
) -> np.ndarray:
    layer_ids = np.full(visible.shape, -1, dtype=np.int16)
    pixels = arr[:, :, :3].astype(np.float32)
    palette_arr = np.asarray(palette, dtype=np.float32)
    diff = pixels[:, :, None, :] - palette_arr[None, None, :, :]
    distances = np.sum(diff * diff, axis=3)
    layer_ids[visible] = np.argmin(distances, axis=2)[visible]
    return layer_ids


def _rgb_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return math.sqrt(
        sum((int(x) - int(y)) ** 2 for x, y in zip(a, b, strict=True))
    )


def _add_edge(
    unused: set[Edge],
    outgoing: dict[Point, set[Point]],
    start: Point,
    end: Point,
) -> None:
    unused.add((start, end))
    outgoing[start].add(end)


def _choose_next(
    previous: Point, current: Point, candidates: list[Point]
) -> Point:
    if len(candidates) == 1:
        return candidates[0]

    direction_order = {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}
    incoming = (
        (
            int(math.copysign(1, current[0] - previous[0]))
            if current[0] != previous[0]
            else 0
        ),
        (
            int(math.copysign(1, current[1] - previous[1]))
            if current[1] != previous[1]
            else 0
        ),
    )
    incoming_index = direction_order.get(incoming, 0)
    turn_preference = {1: 0, 0: 1, 3: 2, 2: 3}

    def score(candidate: Point) -> tuple[int, int, int]:
        vector = (
            (
                int(math.copysign(1, candidate[0] - current[0]))
                if candidate[0] != current[0]
                else 0
            ),
            (
                int(math.copysign(1, candidate[1] - current[1]))
                if candidate[1] != current[1]
                else 0
            ),
        )
        candidate_index = direction_order.get(vector, 0)
        turn = (candidate_index - incoming_index) % 4
        return (turn_preference[turn], candidate[1], candidate[0])

    return min(candidates, key=score)


def _rdp(points: list[Point], epsilon: float) -> list[Point]:
    if len(points) < 3:
        return points

    first = points[0]
    last = points[-1]
    max_distance = 0.0
    max_index = 0
    for index in range(1, len(points) - 1):
        distance = _perpendicular_distance(points[index], first, last)
        if distance > max_distance:
            max_distance = distance
            max_index = index

    if max_distance > epsilon:
        left = _rdp(points[: max_index + 1], epsilon)
        right = _rdp(points[max_index:], epsilon)
        return left[:-1] + right
    return [first, last]


def _perpendicular_distance(point: Point, start: Point, end: Point) -> float:
    if start == end:
        return math.dist(point, start)
    numerator = abs(
        (end[1] - start[1]) * point[0]
        - (end[0] - start[0]) * point[1]
        + end[0] * start[1]
        - end[1] * start[0]
    )
    denominator = math.dist(start, end)
    return numerator / denominator
