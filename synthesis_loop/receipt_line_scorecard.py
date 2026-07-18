#!/usr/bin/env python3
"""Deterministic real-vs-synthetic receipt line scorecard.

This is the metric counterpart to ``glyph_review.py``. It compares a real receipt
image to a synthesized render using the receipt OCR word boxes as anchors, then
emits row/word metrics that map directly to visual review comments:

* glyph height, width-per-character, and ink density ratios
* row right-edge / center / baseline deltas
* source-gap segment anchor deltas for mid-line fields
* KEY: VALUE spacing deltas after colon tokens
* long-numeric barcode caption size/weight ratios

Usage:
  receipt_line_scorecard.py <merchant> <image_id> <receipt_id> <synth_png> <out_json>
      [--real-png REAL] [--md OUT.md]

The CLI needs AWS/Dynamo only to fetch the real image and OCR words. The metric
functions are pure and unit-testable with local images + word dicts.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from dataclasses import dataclass
from io import BytesIO
from statistics import median
from typing import Any, Mapping, Sequence

import numpy as np
from PIL import Image
from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
    GridWord,
    group_words_into_grid_lines,
    is_price_token,
)

DEFAULT_THRESHOLDS = {
    "height_ratio_minor": 1.04,
    "height_ratio_blocker": 1.10,
    "density_ratio_minor": 1.08,
    "density_ratio_blocker": 1.20,
    "wpc_ratio_minor": 1.08,
    "wpc_ratio_blocker": 1.18,
    "right_edge_delta_minor_px": 8.0,
    "right_edge_delta_blocker_px": 18.0,
    "center_delta_minor_px": 10.0,
    "center_delta_blocker_px": 24.0,
    "baseline_delta_minor_px": 6.0,
    "baseline_delta_blocker_px": 14.0,
    "colon_gap_delta_minor_px": 4.0,
    "colon_gap_delta_blocker_px": 10.0,
    "segment_delta_minor_px": 10.0,
    "segment_delta_blocker_px": 24.0,
    "barcode_caption_height_minor": 1.10,
    "barcode_caption_height_blocker": 1.20,
}

_LONG_NUMERIC_RE = re.compile(r"^[\[\]\(\)\sXx0-9\-]+$")
_TOKEN_AFTER_COLON_OK = {"#:"}


@dataclass(frozen=True)
class InkStats:
    left: float
    top: float
    right: float
    bottom: float
    width: float
    height: float
    density: float
    center_x: float
    center_y: float
    ink_px: int


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _ocr_box_to_pixels(
    bbox: Sequence[float],
    width: int,
    height: int,
    *,
    margin: int = 0,
) -> tuple[float, float, float, float] | None:
    if not isinstance(bbox, Sequence) or isinstance(bbox, (str, bytes)):
        return None
    if len(bbox) < 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox[:4])
    except (TypeError, ValueError):
        return None
    if not all(_finite(v) for v in (x0, y0, x1, y1)):
        return None
    inner_w = width - 2 * margin
    inner_h = height - 2 * margin
    left = margin + (min(x0, x1) / 1000.0) * inner_w
    right = margin + (max(x0, x1) / 1000.0) * inner_w
    # Receipt coordinates are y-high-is-top.
    top = margin + (1.0 - max(y0, y1) / 1000.0) * inner_h
    bottom = margin + (1.0 - min(y0, y1) / 1000.0) * inner_h
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _clip_box(
    box: Sequence[float],
    width: int,
    height: int,
    *,
    pad_x: float = 0.0,
    pad_y: float = 0.0,
) -> tuple[int, int, int, int] | None:
    left, top, right, bottom = (float(v) for v in box[:4])
    left = max(0, int(math.floor(left - pad_x)))
    top = max(0, int(math.floor(top - pad_y)))
    right = min(width, int(math.ceil(right + pad_x)))
    bottom = min(height, int(math.ceil(bottom + pad_y)))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _threshold_ink(gray: np.ndarray) -> np.ndarray:
    """A deterministic local-paper threshold for dark thermal ink."""
    if gray.size == 0:
        return np.zeros_like(gray, dtype=bool)
    # Use a high percentile, not the median: dense barcode/caption crops can be
    # more than half ink, which would make black look like the local paper.
    paper = float(np.percentile(gray, 90))
    threshold = max(0.0, min(230.0, paper - 22.0))
    return gray < threshold


def measure_ink(
    image: Image.Image,
    box: Sequence[float],
    *,
    pad_x: float = 0.0,
    pad_y: float = 0.0,
) -> InkStats | None:
    clipped = _clip_box(
        box, image.width, image.height, pad_x=pad_x, pad_y=pad_y
    )
    if clipped is None:
        return None
    left, top, right, bottom = clipped
    gray = np.asarray(image.convert("L").crop(clipped))
    ink = _threshold_ink(gray)
    ink_px = int(ink.sum())
    if ink_px < 4:
        return None
    ys, xs = np.where(ink)
    ink_left = left + float(xs.min())
    ink_right = left + float(xs.max() + 1)
    ink_top = top + float(ys.min())
    ink_bottom = top + float(ys.max() + 1)
    width = max(1.0, ink_right - ink_left)
    height = max(1.0, ink_bottom - ink_top)
    return InkStats(
        left=ink_left,
        top=ink_top,
        right=ink_right,
        bottom=ink_bottom,
        width=width,
        height=height,
        density=float(ink.mean()),
        center_x=(ink_left + ink_right) / 2.0,
        center_y=(ink_top + ink_bottom) / 2.0,
        ink_px=ink_px,
    )


def _word_text(word: Mapping[str, Any]) -> str:
    return str(word.get("text") or "").strip()


def _is_long_numeric_caption(text: str) -> bool:
    glyphs = re.sub(r"\\s+", "", text)
    digits = sum(ch.isdigit() for ch in glyphs)
    return (
        digits >= 14
        and digits >= 0.75 * max(1, len(glyphs))
        and bool(_LONG_NUMERIC_RE.match(glyphs))
    )


def _box_for_word(
    word: Mapping[str, Any],
    image: Image.Image,
    *,
    margin: int,
) -> tuple[float, float, float, float] | None:
    bbox = word.get("bbox")
    if bbox is None:
        return None
    return _ocr_box_to_pixels(bbox, image.width, image.height, margin=margin)


def _grid_words(
    words: Sequence[Mapping[str, Any]], real: Image.Image
) -> tuple[list[GridWord], dict[int, Mapping[str, Any]]]:
    out: list[GridWord] = []
    by_id: dict[int, Mapping[str, Any]] = {}
    for word in words:
        text = _word_text(word)
        if not text:
            continue
        box = _box_for_word(word, real, margin=0)
        if box is None:
            continue
        source_line = word.get("line_id")
        try:
            source_line = int(source_line) if source_line is not None else None
        except (TypeError, ValueError):
            source_line = None
        gw = GridWord(*box, text=text, ink=(0, 0, 0), source_line=source_line)
        out.append(gw)
        by_id[id(gw)] = word
    return out, by_id


def _row_word_source(
    row: Sequence[GridWord], by_id: Mapping[int, Mapping[str, Any]]
):
    return [by_id[id(word)] for word in row if id(word) in by_id]


def _row_text(words: Sequence[Mapping[str, Any]]) -> str:
    return " ".join(_word_text(word) for word in words if _word_text(word))


def _severity(value: float | None, minor: float, blocker: float) -> str:
    if value is None:
        return "NA"
    if abs(value) >= blocker:
        return "BLOCKER"
    if abs(value) >= minor:
        return "MINOR"
    return "PASS"


def _ratio_severity(value: float | None, minor: float, blocker: float) -> str:
    if value is None:
        return "NA"
    distance = abs(value - 1.0)
    if distance >= abs(blocker - 1.0):
        return "BLOCKER"
    if distance >= abs(minor - 1.0):
        return "MINOR"
    return "PASS"


def _worst(*items: str) -> str:
    order = {"BLOCKER": 3, "MINOR": 2, "PASS": 1, "NA": 0}
    return max(items, key=lambda item: order.get(item, -1))


def _row_band(
    row: Sequence[GridWord],
    image: Image.Image,
    *,
    margin: int,
    source_words: Sequence[Mapping[str, Any]],
) -> tuple[float, float, float, float]:
    if not source_words:
        top = min(w.top for w in row)
        bottom = max(w.bottom for w in row)
        return 0.0, top, float(image.width), bottom
    boxes = [
        _box_for_word(word, image, margin=margin) for word in source_words
    ]
    boxes = [box for box in boxes if box is not None]
    if not boxes:
        top = min(w.top for w in row)
        bottom = max(w.bottom for w in row)
        return 0.0, top, float(image.width), bottom
    top = min(box[1] for box in boxes)
    bottom = max(box[3] for box in boxes)
    pad_y = max(4.0, (bottom - top) * 0.35)
    return (
        0.0,
        max(0.0, top - pad_y),
        float(image.width),
        min(float(image.height), bottom + pad_y),
    )


def _row_segments(row: Sequence[GridWord]) -> list[list[GridWord]]:
    ordered = sorted(row, key=lambda word: word.left)
    if not ordered:
        return []
    heights = [max(1.0, word.bottom - word.top) for word in ordered]
    pitch = max(8.0, median(heights) * 0.55)
    segments: list[list[GridWord]] = [[ordered[0]]]
    prev = ordered[0]
    for word in ordered[1:]:
        gap = word.left - prev.right
        if gap > pitch * 4.0:
            segments.append([word])
        else:
            segments[-1].append(word)
        prev = word
    return segments


def _measure_segment(
    image: Image.Image,
    words: Sequence[Mapping[str, Any]],
    *,
    margin: int,
) -> InkStats | None:
    boxes = [_box_for_word(word, image, margin=margin) for word in words]
    boxes = [box for box in boxes if box is not None]
    if not boxes:
        return None
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[2] for box in boxes)
    bottom = max(box[3] for box in boxes)
    return measure_ink(
        image,
        (left, top, right, bottom),
        pad_x=max(3.0, (right - left) * 0.08),
        pad_y=max(3.0, (bottom - top) * 0.25),
    )


def _word_scores(
    real: Image.Image,
    synth: Image.Image,
    words: Sequence[Mapping[str, Any]],
    *,
    synth_margin: int,
) -> list[dict[str, Any]]:
    scores = []
    for word in words:
        text = _word_text(word)
        if len(text.replace(" ", "")) < 2:
            continue
        if _is_long_numeric_caption(text):
            continue
        real_box = _box_for_word(word, real, margin=0)
        synth_box = _box_for_word(word, synth, margin=synth_margin)
        if real_box is None or synth_box is None:
            continue
        real_stats = measure_ink(real, real_box, pad_x=3, pad_y=5)
        synth_stats = measure_ink(synth, synth_box, pad_x=3, pad_y=5)
        if real_stats is None or synth_stats is None:
            continue
        glyphs = max(1, len(text.replace(" ", "")))
        scores.append(
            {
                "text": text,
                "line_id": word.get("line_id"),
                "height_ratio": synth_stats.height / real_stats.height,
                "wpc_ratio": (synth_stats.width / glyphs)
                / (real_stats.width / glyphs),
                "density_ratio": synth_stats.density
                / max(1e-6, real_stats.density),
                "real_height": real_stats.height,
                "synth_height": synth_stats.height,
            }
        )
    return scores


def _colon_gap_scores(
    real: Image.Image,
    synth: Image.Image,
    source_words: Sequence[Mapping[str, Any]],
    *,
    synth_margin: int,
) -> list[dict[str, Any]]:
    scores = []
    ordered = list(source_words)
    for left_word, right_word in zip(ordered, ordered[1:]):
        left_text = _word_text(left_word)
        right_text = _word_text(right_word)
        if not left_text.endswith(":") or left_text in _TOKEN_AFTER_COLON_OK:
            continue
        if not right_text:
            continue
        real_left = _measure_segment(real, [left_word], margin=0)
        real_right = _measure_segment(real, [right_word], margin=0)
        synth_left = _measure_segment(synth, [left_word], margin=synth_margin)
        synth_right = _measure_segment(
            synth, [right_word], margin=synth_margin
        )
        if not all((real_left, real_right, synth_left, synth_right)):
            continue
        real_gap = real_right.left - real_left.right  # type: ignore[union-attr]
        synth_gap = synth_right.left - synth_left.right  # type: ignore[union-attr]
        # Only score "KEY:VALUE" joins that are actually separated in the real
        # ink. OCR boxes for long values can overlap the label crop by a few px;
        # treating overlap-vs-overlap as a spacing error produces false blockers.
        # Right-column labels like "Mode:     Issuer" are alignment problems, not
        # colon-spacing problems.
        if real_gap <= 0.0 or real_gap > 24.0:
            continue
        scores.append(
            {
                "pair": f"{left_text} {right_text}",
                "real_gap_px": real_gap,
                "synth_gap_px": synth_gap,
                "gap_delta_px": synth_gap - real_gap,
            }
        )
    return scores


def _barcode_caption_score(
    row_text: str,
    real_stats: InkStats | None,
    synth_stats: InkStats | None,
) -> dict[str, Any] | None:
    if not _is_long_numeric_caption(row_text):
        return None
    if real_stats is None or synth_stats is None:
        return None
    return {
        "caption_text": row_text,
        "height_ratio": synth_stats.height / real_stats.height,
        "density_ratio": synth_stats.density / max(1e-6, real_stats.density),
        "right_delta_px": synth_stats.right - real_stats.right,
        "center_delta_px": synth_stats.center_x - real_stats.center_x,
    }


def score_receipt_images(
    real: Image.Image,
    synth: Image.Image,
    words: Sequence[Mapping[str, Any]],
    *,
    synth_margin: int = 10,
    row_pitch_px: float = 34.0,
    thresholds: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Return deterministic real-vs-synth row and aggregate metrics."""
    thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    real = real.convert("RGB")
    synth = synth.convert("RGB")
    if real.size != synth.size:
        real = real.resize(synth.size, Image.Resampling.LANCZOS)

    grid_words, by_id = _grid_words(words, real)
    rows = group_words_into_grid_lines(grid_words, row_pitch_px)
    rows = sorted(
        rows, key=lambda row: sum(w.center_y for w in row) / len(row)
    )
    word_scores = _word_scores(real, synth, words, synth_margin=synth_margin)

    row_scores: list[dict[str, Any]] = []
    colon_scores_all: list[dict[str, Any]] = []
    segment_scores_all: list[dict[str, Any]] = []
    barcode_scores: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows, start=1):
        source_words = sorted(
            _row_word_source(row, by_id),
            key=lambda word: (
                _box_for_word(word, real, margin=0)[0]
                if _box_for_word(word, real, margin=0)
                else 0.0
            ),
        )
        text = _row_text(source_words)
        real_band = _row_band(row, real, margin=0, source_words=source_words)
        synth_band = _row_band(
            row, synth, margin=synth_margin, source_words=source_words
        )
        real_stats = measure_ink(real, real_band)
        synth_stats = measure_ink(synth, synth_band)

        height_ratio = (
            synth_stats.height / real_stats.height
            if real_stats is not None and synth_stats is not None
            else None
        )
        density_ratio = (
            synth_stats.density / max(1e-6, real_stats.density)
            if real_stats is not None and synth_stats is not None
            else None
        )
        right_delta = (
            synth_stats.right - real_stats.right
            if real_stats is not None and synth_stats is not None
            else None
        )
        center_delta = (
            synth_stats.center_x - real_stats.center_x
            if real_stats is not None and synth_stats is not None
            else None
        )
        baseline_delta = (
            synth_stats.bottom - real_stats.bottom
            if real_stats is not None and synth_stats is not None
            else None
        )

        colon_scores = _colon_gap_scores(
            real, synth, source_words, synth_margin=synth_margin
        )
        colon_scores_all.extend(
            {
                **item,
                "row_index": row_index,
                "row_text": text,
            }
            for item in colon_scores
        )

        segment_scores = []
        for seg_no, segment_grid in enumerate(_row_segments(row), start=1):
            segment_words = [
                by_id[id(word)] for word in segment_grid if id(word) in by_id
            ]
            if not segment_words:
                continue
            real_seg = _measure_segment(real, segment_words, margin=0)
            synth_seg = _measure_segment(
                synth, segment_words, margin=synth_margin
            )
            if real_seg is None or synth_seg is None:
                continue
            seg = {
                "segment": seg_no,
                "text": _row_text(segment_words),
                "center_delta_px": synth_seg.center_x - real_seg.center_x,
                "right_delta_px": synth_seg.right - real_seg.right,
                "left_delta_px": synth_seg.left - real_seg.left,
            }
            segment_scores.append(seg)
            segment_scores_all.append(
                {**seg, "row_index": row_index, "row_text": text}
            )

        barcode = _barcode_caption_score(text, real_stats, synth_stats)
        if barcode is not None:
            barcode = {**barcode, "row_index": row_index}
            barcode_scores.append(barcode)

        is_price_row = any(is_price_token(_word_text(w)) for w in source_words)
        price_right_delta = None
        if is_price_row:
            price_words = [
                word
                for word in source_words
                if is_price_token(_word_text(word))
            ]
            real_price = _measure_segment(real, price_words, margin=0)
            synth_price = _measure_segment(
                synth, price_words, margin=synth_margin
            )
            if real_price is not None and synth_price is not None:
                price_right_delta = synth_price.right - real_price.right
        row_severity = _worst(
            (
                _severity(
                    price_right_delta,
                    thresholds["right_edge_delta_minor_px"],
                    thresholds["right_edge_delta_blocker_px"],
                )
                if is_price_row
                else "PASS"
            ),
            *[
                _severity(
                    item["gap_delta_px"],
                    thresholds["colon_gap_delta_minor_px"],
                    thresholds["colon_gap_delta_blocker_px"],
                )
                for item in colon_scores
            ],
            *[
                _severity(
                    item["center_delta_px"],
                    thresholds["segment_delta_minor_px"],
                    thresholds["segment_delta_blocker_px"],
                )
                for item in segment_scores[1:]
            ],
            (
                _ratio_severity(
                    barcode["height_ratio"] if barcode else None,
                    thresholds["barcode_caption_height_minor"],
                    thresholds["barcode_caption_height_blocker"],
                )
                if barcode
                else "PASS"
            ),
        )

        row_scores.append(
            {
                "row_index": row_index,
                "source_lines": sorted(
                    {
                        int(word.get("line_id"))
                        for word in source_words
                        if str(word.get("line_id") or "").lstrip("-").isdigit()
                    }
                ),
                "text": text,
                "severity": row_severity,
                "height_ratio": height_ratio,
                "density_ratio": density_ratio,
                "right_delta_px": right_delta,
                "price_right_delta_px": price_right_delta,
                "center_delta_px": center_delta,
                "baseline_delta_px": baseline_delta,
                "is_price_row": is_price_row,
                "colon_gaps": colon_scores,
                "segments": segment_scores,
                "barcode_caption": barcode,
            }
        )

    def _median(values: Sequence[float | None]) -> float | None:
        clean = [
            float(v)
            for v in values
            if v is not None and math.isfinite(float(v))
        ]
        return float(median(clean)) if clean else None

    summary = {
        "row_count": len(row_scores),
        "word_count": len(word_scores),
        "height_ratio_median": _median(
            [item["height_ratio"] for item in word_scores]
        ),
        "wpc_ratio_median": _median(
            [item["wpc_ratio"] for item in word_scores]
        ),
        "density_ratio_median": _median(
            [item["density_ratio"] for item in word_scores]
        ),
        "price_right_delta_abs_median_px": _median(
            [
                abs(item["price_right_delta_px"])
                for item in row_scores
                if item["price_right_delta_px"] is not None
            ]
        ),
        "colon_gap_delta_median_px": _median(
            [item["gap_delta_px"] for item in colon_scores_all]
        ),
        "barcode_caption_height_ratio_median": _median(
            [item["height_ratio"] for item in barcode_scores]
        ),
        "severity_counts": {
            severity: sum(
                1 for item in row_scores if item["severity"] == severity
            )
            for severity in ("PASS", "MINOR", "BLOCKER", "NA")
        },
    }

    failures = []
    for item in row_scores:
        if item["severity"] in ("MINOR", "BLOCKER"):
            failures.append(
                {
                    "severity": item["severity"],
                    "row_index": item["row_index"],
                    "source_lines": item["source_lines"],
                    "text": item["text"],
                    "reasons": _row_reasons(item, thresholds),
                }
            )

    return {
        "summary": summary,
        "thresholds": dict(thresholds),
        "failures": failures,
        "rows": row_scores,
        "words": word_scores,
    }


def _row_reasons(
    row: Mapping[str, Any], thresholds: Mapping[str, float]
) -> list[str]:
    reasons: list[str] = []
    right = row.get("price_right_delta_px")
    if (
        row.get("is_price_row")
        and right is not None
        and abs(float(right)) >= thresholds["right_edge_delta_minor_px"]
    ):
        reasons.append(f"price_right_delta_px={float(right):.1f}")
    for gap in row.get("colon_gaps") or []:
        delta = float(gap["gap_delta_px"])
        if abs(delta) >= thresholds["colon_gap_delta_minor_px"]:
            reasons.append(f"colon_gap {gap['pair']} delta={delta:.1f}px")
    for segment in (row.get("segments") or [])[1:]:
        delta = float(segment["center_delta_px"])
        if abs(delta) >= thresholds["segment_delta_minor_px"]:
            reasons.append(
                f"segment {segment['segment']} center_delta={delta:.1f}px"
            )
    barcode = row.get("barcode_caption")
    if barcode is not None:
        ratio = float(barcode["height_ratio"])
        if (
            abs(ratio - 1.0)
            >= thresholds["barcode_caption_height_minor"] - 1.0
        ):
            reasons.append(f"barcode_caption_height_ratio={ratio:.3f}")
    return reasons


def _write_markdown(report: Mapping[str, Any], path: str) -> None:
    summary = report["summary"]
    lines = [
        "# Receipt Line Scorecard",
        "",
        "## Summary",
        "",
        f"- rows: {summary['row_count']}",
        f"- words: {summary['word_count']}",
        f"- height_ratio_median: {_fmt(summary['height_ratio_median'])}",
        f"- wpc_ratio_median: {_fmt(summary['wpc_ratio_median'])}",
        f"- density_ratio_median: {_fmt(summary['density_ratio_median'])}",
        f"- barcode_caption_height_ratio_median: {_fmt(summary['barcode_caption_height_ratio_median'])}",
        f"- price_right_delta_abs_median_px: {_fmt(summary['price_right_delta_abs_median_px'])}",
        f"- severity_counts: {summary['severity_counts']}",
        "",
        "## Failures",
        "",
        "| severity | row | source lines | text | reasons |",
        "|---|---:|---|---|---|",
    ]
    failures = report.get("failures") or []
    if not failures:
        lines.append("| PASS | - | - | - | none |")
    for failure in failures:
        reasons = "; ".join(failure.get("reasons") or [])
        text = str(failure.get("text") or "").replace("|", "\\|")
        lines.append(
            f"| {failure['severity']} | {failure['row_index']} | "
            f"{','.join(map(str, failure.get('source_lines') or []))} | "
            f"{text} | {reasons} |"
        )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _fmt(value: Any) -> str:
    if value is None:
        return "NA"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value)


def _load_words_and_real(merchant: str, image_id: str, receipt_id: int):
    del merchant
    import boto3

    from receipt_dynamo.entities.receipt import item_to_receipt
    from receipt_dynamo.entities.receipt_word import item_to_receipt_word

    table = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    region = os.environ.get("AWS_REGION", "us-east-1")
    s3 = boto3.client("s3", region_name=region)

    # Read the RECEIPT and RECEIPT_WORD rows straight off the image partition
    # rather than via get_image_details: that aggregator reconstructs every
    # entity in the partition and aborts when a post-re-OCR OCR_JOB row fails
    # its strict round-trip check. We only need this receipt's words + s3 keys.
    dynamo = boto3.client("dynamodb", region_name=region)
    receipt = None
    raw_words = []
    lek = None
    while True:
        kw = dict(
            TableName=table,
            KeyConditionExpression="#pk = :pk",
            ExpressionAttributeNames={"#pk": "PK", "#t": "TYPE"},
            ExpressionAttributeValues={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":r": {"S": "RECEIPT"},
                ":rw": {"S": "RECEIPT_WORD"},
            },
            FilterExpression="#t IN (:r, :rw)",
        )
        if lek:
            kw["ExclusiveStartKey"] = lek
        resp = dynamo.query(**kw)
        for it in resp["Items"]:
            t = it["TYPE"]["S"]
            try:
                if t == "RECEIPT":
                    r = item_to_receipt(it)
                    if str(r.receipt_id) == str(receipt_id):
                        receipt = r
                elif t == "RECEIPT_WORD":
                    raw_words.append(item_to_receipt_word(it))
            except Exception:  # noqa: BLE001 - skip a drifted row, not the receipt
                continue
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
    if receipt is None:
        raise RuntimeError(
            f"receipt {receipt_id} not found for image {image_id}"
        )
    words = []
    for word in raw_words:
        if str(word.receipt_id) != str(receipt_id):
            continue
        words.append(
            {
                "text": word.text,
                "line_id": word.line_id,
                "word_id": word.word_id,
                "bbox": [
                    word.top_left["x"] * 1000,
                    word.top_left["y"] * 1000,
                    word.bottom_right["x"] * 1000,
                    word.bottom_right["y"] * 1000,
                ],
            }
        )
    for bucket, key in (
        (receipt.cdn_s3_bucket, receipt.cdn_s3_key),
        (receipt.raw_s3_bucket, receipt.raw_s3_key),
    ):
        if not bucket or not key:
            continue
        try:
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            return Image.open(BytesIO(body)).convert("RGB"), words
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError(
        f"could not load real image for {image_id}:{receipt_id}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 5:
        print(__doc__)
        return 2
    merchant, image_id, receipt_id_s, synth_png, out_json = argv[:5]
    rest = argv[5:]
    real_png = None
    out_md = None
    i = 0
    while i < len(rest):
        if rest[i] == "--real-png" and i + 1 < len(rest):
            real_png = rest[i + 1]
            i += 2
        elif rest[i] == "--md" and i + 1 < len(rest):
            out_md = rest[i + 1]
            i += 2
        else:
            raise SystemExit(f"unknown argument: {rest[i]}")
    receipt_id = int(receipt_id_s)
    if real_png:
        real = Image.open(real_png).convert("RGB")
        _, words = _load_words_and_real(merchant, image_id, receipt_id)
    else:
        real, words = _load_words_and_real(merchant, image_id, receipt_id)
    synth = Image.open(synth_png).convert("RGB")
    report = score_receipt_images(real, synth, words)
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    if out_md:
        _write_markdown(report, out_md)
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
