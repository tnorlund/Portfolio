"""Golden-set regression gate for the line-item extraction core.

Runs ``extract_items`` over the hermetic OCR fixture (word geometry captured
from the dev table) and scores names/prices against the hand-labeled truth.
Per-merchant floors are pinned at the measured post-deskew baseline so any
future change that regresses a format fails CI even if the aggregate
improves -- the Trader Joe's failure (100% recall, 0% names) hid inside a
35% average for months.

Floors are FLOORS, not targets: raise them when a change genuinely improves
a merchant, never lower them to make a change pass.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from receipt_upload.line_items.geometry import extract_items

_FIXTURES = Path(__file__).parent / "fixtures"


def _money(v):
    if v is None:
        return None
    t = str(v).replace("$", "").replace(",", "").strip().lstrip("-")
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _norm(s: str) -> str:
    t = re.sub(r"\s+", " ", (s or "").strip().upper())
    t = re.sub(r"<[A-Z]>", " ", t)
    t = re.sub(r"\b\d{5,}\b", " ", t)
    t = re.sub(r"[^A-Z0-9 ]", " ", t)
    t = re.sub(r"\b\d+\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _score_receipt(
    truth: list[dict], pred: list[dict]
) -> tuple[int, int, int]:
    """(matched, name_ok, true_count) with price-first greedy matching."""
    used: set[int] = set()
    matched = name_ok = 0
    for t in truth:
        tp = _money(t.get("price"))
        best, best_score = None, 0.0
        for i, p in enumerate(pred):
            if i in used:
                continue
            price_ok = tp is not None and _money(p.get("price")) == tp
            ta, tb = set(_norm(t.get("name", "")).split()), set(
                _norm(p.get("name", "")).split()
            )
            sim = len(ta & tb) / max(len(ta), len(tb)) if ta and tb else 0.0
            score = (2.0 if price_ok else 0.0) + sim
            if score > best_score:
                best, best_score = i, score
        if best is not None and best_score >= 0.5:
            used.add(best)
            matched += 1
            if _norm(t.get("name", "")) == _norm(pred[best].get("name", "")):
                name_ok += 1
    return matched, name_ok, len(truth)


# (recall_floor, name_floor, precision_floor) per merchant, from the
# measured post-deskew baseline (2026-07-30), rounded DOWN slightly for
# run-to-run stability. Precision floors added after the corpus sweep
# vetoed a decoder that PASSED recall/name floors while over-generating
# (+70 items corpus-wide): over-generation is invisible to recall and
# names, so it must be gated explicitly.
_FLOORS = {
    # Name floor raised 0.85 -> 0.90 with the 2026-08-03 agent-originated
    # promotions (two single-item Sprouts receipts, measured names 0.949).
    "Sprouts Farmers Market": (1.00, 0.90, 0.95),
    "Roast & Rice Asian Fusion": (1.00, 1.00, 0.95),
    "TRADER JOE'S": (1.00, 1.00, 0.95),
    "Trader Joe's": (1.00, 1.00, 0.95),
    "In-N-Out Burger": (1.00, 1.00, 0.75),
    "Wild Fork": (1.00, 0.60, 0.95),
    "The Home Depot": (0.80, 0.25, 0.50),
    "Costco Wholesale": (0.40, 0.00, 0.85),
    "Target": (0.65, 0.10, 0.90),
    # 2026-07-30 local-capture additions. WFM precision floor is 0.50
    # because the unseen tare-weight row currently emits a $0.05 item;
    # RAISE to 0.95+ when the tare template lands in the priors.
    "Whole Foods Market": (1.00, 1.00, 0.50),
}


def test_golden_set_per_merchant_floors() -> None:
    golden = {
        (r["image_id"], r["receipt_id"]): r
        for r in json.load(open(_FIXTURES / "line_items_golden.json"))[
            "receipts"
        ]
    }
    ocr = {
        (r["image_id"], r["receipt_id"]): r
        for r in json.load(open(_FIXTURES / "line_items_golden_ocr.json"))[
            "receipts"
        ]
    }
    assert set(golden) == set(ocr), "fixtures out of sync"

    per: dict[str, list[int]] = {}
    for key, g in golden.items():
        o = ocr[key]
        items, _ = extract_items(o["words"], set(o["items_line_ids"]))
        truth = [t for t in g["true_items"] if not t.get("is_discount")]
        m, n, tc = _score_receipt(truth, items)
        agg = per.setdefault(g["merchant"], [0, 0, 0, 0])
        agg[0] += m
        agg[1] += n
        agg[2] += tc
        agg[3] += len(items)

    failures = []
    for merchant, (m, n, tc, np_) in sorted(per.items()):
        recall = m / tc if tc else 0.0
        names = n / m if m else 0.0
        precision = m / np_ if np_ else 0.0
        floor = _FLOORS.get(merchant)
        if floor is None:
            continue
        rf, nf, pf = floor
        if recall < rf or names < nf or precision < pf:
            failures.append(
                f"{merchant}: recall {recall:.0%} (floor {rf:.0%}), "
                f"names {names:.0%} (floor {nf:.0%}), "
                f"precision {precision:.0%} (floor {pf:.0%})"
            )
    assert not failures, "golden regression:\n  " + "\n  ".join(failures)
