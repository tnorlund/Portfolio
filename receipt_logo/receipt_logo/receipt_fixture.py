from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReceiptHeaderMatch:
    merchant_name: str
    fixture_path: str
    token_indices: tuple[int, ...]
    bounds: tuple[int, int, int, int]
    tokens: tuple[str, ...]

    @property
    def width(self) -> int:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> int:
        return self.bounds[3] - self.bounds[1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "merchant_name": self.merchant_name,
            "fixture_path": self.fixture_path,
            "token_indices": list(self.token_indices),
            "bounds": list(self.bounds),
            "width": self.width,
            "height": self.height,
            "tokens": list(self.tokens),
        }


def _tokens_from_ocr_lines(data):
    """Flatten the repo's OCR fixture shape ({"lines": [{"words": [...]}]})
    into parallel tokens/bboxes lists; (None, None) when the shape is absent.
    Word boxes are normalized {x, y, width, height} dicts (y-up) -> convert
    to [x0, y0, x1, y1]."""
    lines = data.get("lines")
    if not isinstance(lines, list):
        return None, None
    tokens, bboxes = [], []
    for line in lines:
        for word in line.get("words") or []:
            text = word.get("text")
            box = word.get("bounding_box") or {}
            if not text or not all(
                k in box for k in ("x", "y", "width", "height")
            ):
                continue
            tokens.append(str(text))
            bboxes.append(
                [
                    float(box["x"]),
                    float(box["y"]),
                    float(box["x"]) + float(box["width"]),
                    float(box["y"]) + float(box["height"]),
                ]
            )
    if not tokens:
        return None, None
    return tokens, bboxes


def inspect_receipt_fixture(
    fixture_path: str | Path,
    merchant_name: str = "Sprouts Farmers Market",
) -> ReceiptHeaderMatch | None:
    """Find a merchant-name header in a Portfolio receipt-style JSON fixture."""

    path = Path(fixture_path).expanduser().resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    tokens = data.get("tokens")
    bboxes = data.get("bboxes")
    if not isinstance(tokens, list) or not isinstance(bboxes, list):
        tokens, bboxes = _tokens_from_ocr_lines(data)
        if tokens is None:
            return None

    wanted = tuple(part.upper() for part in merchant_name.split())
    normalized = [str(token).upper().strip(".,:;") for token in tokens]

    for start in range(0, len(normalized) - len(wanted) + 1):
        window = tuple(normalized[start : start + len(wanted)])
        if window == wanted:
            indices = tuple(range(start, start + len(wanted)))
            selected_boxes = [bboxes[index] for index in indices]
            bounds = _boxes_bounds(selected_boxes)
            return ReceiptHeaderMatch(
                merchant_name=merchant_name,
                fixture_path=str(path),
                token_indices=indices,
                bounds=bounds,
                tokens=tuple(str(tokens[index]) for index in indices),
            )

    # Synthetic fixtures often split Sprouts over two visual rows; allow a
    # compact subsequence match before giving up.
    positions: list[int] = []
    cursor = 0
    for wanted_token in wanted:
        try:
            index = normalized.index(wanted_token, cursor)
        except ValueError:
            return None
        positions.append(index)
        cursor = index + 1
    if positions[-1] - positions[0] > 12:
        return None

    selected_boxes = [bboxes[index] for index in positions]
    return ReceiptHeaderMatch(
        merchant_name=merchant_name,
        fixture_path=str(path),
        token_indices=tuple(positions),
        bounds=_boxes_bounds(selected_boxes),
        tokens=tuple(str(tokens[index]) for index in positions),
    )


def _boxes_bounds(boxes: list[list[int]]) -> tuple[int, int, int, int]:
    xs0 = [int(box[0]) for box in boxes]
    ys0 = [int(box[1]) for box in boxes]
    xs1 = [int(box[2]) for box in boxes]
    ys1 = [int(box[3]) for box in boxes]
    return min(xs0), min(ys0), max(xs1), max(ys1)
