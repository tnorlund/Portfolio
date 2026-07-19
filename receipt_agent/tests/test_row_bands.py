"""Prove the row_bands helpers reproduce the legacy groupers exactly (P1b).

The legacy implementations are inlined here as oracles; the shared helpers
must agree with them element-for-element on adversarial fixtures (ties,
chained centers, degenerate boxes) so the migration is provably
byte-identical at the grouping layer.
"""

import random

from receipt_agent.agents.label_evaluator.rendering.row_bands import (
    group_rows_greedy,
    group_rows_quantized,
)


def _legacy_glyph_renderer_group(rows, tol):
    rows = sorted(rows, key=lambda r: -r[0])
    grouped, current, last_cy = [], [], None
    for cy, w in rows:
        if last_cy is None or abs(cy - last_cy) <= tol:
            current.append(w)
        else:
            grouped.append(current)
            current = [w]
        last_cy = cy
    if current:
        grouped.append(current)
    return grouped


def _legacy_cached_group(words):
    grouped = {}
    for word in words:
        bbox = word.get("bbox")
        if not bbox:
            continue
        y = round((float(bbox[1]) + float(bbox[3])) / 16) * 16
        grouped.setdefault(int(y), []).append(word)
    return [
        line_words for _, line_words in sorted(grouped.items(), reverse=True)
    ]


def _random_rows(rng, n):
    rows = []
    for i in range(n):
        cy = rng.choice([rng.uniform(0, 1000), rng.choice([100.0, 500.0])])
        rows.append((cy, {"i": i}))
    return rows


def test_greedy_matches_glyph_renderer_fallback():
    rng = random.Random(7)
    for trial in range(200):
        rows = _random_rows(rng, rng.randint(0, 40))
        tol = rng.choice([0.0, 5.0, 25.0, 400.0])
        expected = _legacy_glyph_renderer_group(rows, tol)
        got = [
            [w for _, w in row]
            for row in group_rows_greedy(
                rows,
                lambda r: r[0],
                tol,
                reference="prev",
                descending=True,
            )
        ]
        assert got == expected, (trial, tol)


def test_greedy_row_median_matches_stylescan_contract():
    # stylescan.group_visual_lines semantics: ascending, strict <, per-item
    # tolerance, reference = median of the open row.
    from statistics import median

    def legacy(ws):
        ws = sorted(ws, key=lambda w: w["cy"])
        lines = []
        for w in ws:
            if (
                lines
                and abs(w["cy"] - median(x["cy"] for x in lines[-1]))
                < w["h"] * 0.6
            ):
                lines[-1].append(w)
            else:
                lines.append([w])
        return lines

    rng = random.Random(11)
    for trial in range(200):
        ws = [
            {
                "cy": rng.uniform(0, 400),
                "h": rng.choice([0.0, 8.0, 14.0, 30.0]),
            }
            for _ in range(rng.randint(0, 30))
        ]
        expected = legacy(ws)
        got = group_rows_greedy(
            ws,
            lambda w: w["cy"],
            lambda w: w["h"] * 0.6,
            reference="row_median",
            strict=True,
        )
        assert got == expected, trial


def test_quantized_matches_cached_grouper():
    rng = random.Random(3)
    for trial in range(200):
        words = []
        for i in range(rng.randint(0, 50)):
            if rng.random() < 0.1:
                words.append({"i": i})  # no bbox: skipped by both
            else:
                y0 = rng.uniform(0, 1000)
                words.append(
                    {"i": i, "bbox": [rng.uniform(0, 900), y0, 0, y0 + 14]}
                )
        expected = _legacy_cached_group(words)
        got = group_rows_quantized(
            [w for w in words if w.get("bbox")],
            lambda w: float(w["bbox"][1]) + float(w["bbox"][3]),
            step=16,
            descending=True,
        )
        assert got == expected, trial
