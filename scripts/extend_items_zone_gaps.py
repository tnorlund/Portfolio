#!/usr/bin/env python3.12
"""Zone-gap ITEMS boundary extension (failure-mode H batch repair).

Mode H in the failure-mode audit is 45% of dev reconciliation mismatches
(68 receipts): priced item text sits just OUTSIDE the ITEMS section
boundary, so the extractor never sees it. 47 of the 68 were judged
recoverable WITHOUT re-OCR — the content was OCR'd, the zone just does
not reach it. This script finds those receipts and repairs the boundary,
receipt by receipt, under the same arithmetic guard the MCP
``extend_items_section`` tool enforces.

Discovery (pure functions, unit-tested in
tests/test_extend_items_zone_gaps.py):
  * candidates are UNSECTIONED priced bands in the corridor directly
    above/below the ITEMS zone (bounded by the nearest other section on
    each side, orientation-agnostic);
  * each band's price must sit in the receipt's price column
    (|x - price_column_x| < 0.15 — the load-bearing column gate from the
    ReceiptRow convention; a previous repair veto hinged on it);
  * settlement/tender/summary-arithmetic bands are never candidates
    (geometry's decoder vocabulary via the OCR-tolerant
    ``looks_arithmetic``, plus geometry's WAS/SALE-PRICE and
    non-product-note guards);
  * a band whose amount equals a printed summary figure is never
    absorbed when the receipt already extracts >= 2 items (on single-
    item receipts the item legitimately equals the total);
  * a candidate subset must close the reconciliation gap arithmetically
    (``subset_closing_gap``, match tolerance first, then near).

Accept authority: ``extend_items_section_impl`` from
scripts/receipt_mcp_server.py — the SAME guard the MCP tool uses. It
re-runs the real extractor and refuses unless |delta| strictly shrinks
AND the reconciliation status improves; it also refuses double-claimed
lines, keeps the section's row anchor consistent, PRESERVES the prior
validation_status (the repair_item_sections lesson: a careless write
demoted VALID -> PENDING and hid sections from the extractor's VALID
gate), and bumps the summary timestamp so the stream stage regenerates
the receipt's line items.

Deliberate exclusions, reported per run:
  * split receipts — fragments sharing merchant+date+grand_total across
    DIFFERENT image ids carry the full printed total but only part of
    the items, so they can never reconcile (audit caveat; they need
    merging at ingest, not boundary repair);
  * receipts with no ITEMS section (class-A jurisdiction of
    repair_item_sections.py) and receipts with no usable baseline.

Safety: dry-run by default; --apply to write; hard refusal of the prod
table; gzip pre-image journal fsynced before the first write (the
repair_item_sections Journal).

Usage:
    python3.12 scripts/extend_items_zone_gaps.py                # dry run
    python3.12 scripts/extend_items_zone_gaps.py --apply
    python3.12 scripts/extend_items_zone_gaps.py --histogram
    python3.12 scripts/extend_items_zone_gaps.py --receipt IMG_ID:1
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from collections import Counter, defaultdict
from statistics import median
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "receipt_dynamo",
    ),
)

import boto3  # noqa: E402
from extract_line_items import _query_all, fetch_receipt_records  # noqa: E402
from repair_item_sections import (  # noqa: E402
    Journal,
    evaluate,
    forbidden_values,
    looks_arithmetic,
    subset_closing_gap,
)

from receipt_dynamo.amounts import looks_like_receipt_amount  # noqa: E402
from receipt_dynamo.data.dynamo_client import DynamoClient  # noqa: E402
from receipt_upload.line_items.geometry import (  # noqa: E402
    NON_PRODUCT_NOTE_RE,
    SALE_PRICE_RE,
    WAS_PRICE_RE,
    band_words,
    parse_band,
)

DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_MARKER = "d7ff76a"
# The load-bearing price-column gate: a candidate's price word must sit
# within this x-distance of the receipt's price column.
PRICE_COLUMN_TOL = 0.15
# Receipt-level status = worst item status (mirrors the MCP servers).
_SEVERITY = {"match": 0, "near": 1, "no-baseline": 2, "mismatch": 3}
# Amount words are 2-decimal money (mirrors geometry.parse_band's gate).
_TWO_DECIMAL_RE = re.compile(r"\d[.,]\d{2}(?!\d)")


# ---------------------------------------------------------------------------
# pure discovery helpers (unit-tested)
# ---------------------------------------------------------------------------
def receipt_price_column_x(
    rows: list[Any], words: list[dict], items_lids: set[int]
) -> Optional[float]:
    """The receipt's price-column x.

    ``ReceiptRow.price_column_x`` is the canonical convention (median
    over rows that carry one). Fallback: the median x of amount words
    inside the current ITEMS zone. None when neither exists — the
    column gate then rejects every candidate (fail closed).
    """
    xs = [
        float(r.price_column_x)
        for r in rows or []
        if getattr(r, "price_column_x", None) is not None
    ]
    if xs:
        return float(median(xs))
    xs = [
        w["x"]
        for w in words
        if w["line_id"] in items_lids
        and looks_like_receipt_amount(w["text"])
        and _TWO_DECIMAL_RE.search(w["text"])
    ]
    return float(median(xs)) if xs else None


def items_corridor(
    words: list[dict], sections: list[dict]
) -> Optional[tuple[float, float]]:
    """Y interval from the ITEMS zone out to the nearest other section.

    Orientation-agnostic: any non-ITEMS section whose y-span lies
    entirely beyond one side of the ITEMS span bounds the corridor on
    that side; with no bounding section the corridor runs to the
    receipt edge. Candidates outside the corridor would have to jump
    OVER another section to reach ITEMS — never a zone gap.
    """

    def yspan(lids: set[int]) -> Optional[tuple[float, float]]:
        ys = [w["y_mid"] for w in words if w["line_id"] in lids]
        return (min(ys), max(ys)) if ys else None

    items_span: Optional[tuple[float, float]] = None
    other_spans: list[tuple[float, float]] = []
    for s in sections:
        span = yspan({int(x) for x in (s.get("line_ids") or [])})
        if span is None:
            continue
        if str(s.get("section_type")) == "ITEMS":
            items_span = (
                span
                if items_span is None
                else (
                    min(items_span[0], span[0]),
                    max(items_span[1], span[1]),
                )
            )
        else:
            other_spans.append(span)
    if items_span is None:
        return None
    lo, hi = 0.0, 1.0
    for s_lo, s_hi in other_spans:
        if s_hi <= items_span[0]:
            lo = max(lo, s_hi)
        if s_lo >= items_span[1]:
            hi = min(hi, s_lo)
    return lo, hi


def discover_candidates(
    words: list[dict],
    sections: list[dict],
    summary: Optional[dict],
    rows: list[Any],
    n_items: int,
) -> list[dict]:
    """Zone-gap candidate bands, nearest to the ITEMS boundary first.

    Each candidate carries the band's line_ids, its parsed price, a
    text preview, and its y-distance from the ITEMS span edge. Every
    veto here is a NON-candidate; final acceptance still belongs to
    the extend_items_section arithmetic guard.
    """
    corridor = items_corridor(words, sections)
    if corridor is None:
        return []
    lo, hi = corridor

    sectioned: set[int] = set()
    items_lids: set[int] = set()
    for s in sections:
        lids = {int(x) for x in (s.get("line_ids") or [])}
        sectioned |= lids
        if str(s.get("section_type")) == "ITEMS":
            items_lids |= lids
    items_ys = [w["y_mid"] for w in words if w["line_id"] in items_lids]
    if not items_ys:
        return []
    i_lo, i_hi = min(items_ys), max(items_ys)

    col_x = receipt_price_column_x(rows, words, items_lids)
    if col_x is None:
        return []  # no price column to align against: fail closed

    fv = forbidden_values(summary)
    free = [w for w in words if w["line_id"] not in sectioned]
    out: list[dict] = []
    for band in band_words(free):
        y = sum(w["y_mid"] for w in band) / len(band)
        if not (lo <= y <= hi):
            continue
        parsed = parse_band(band)
        if parsed is None or not parsed.get("price"):
            continue
        text = parsed["raw_text"]
        # Settlement/tender/summary arithmetic rows are never items —
        # the OCR-tolerant detector covers geometry's SETTLEMENT_RE
        # vocabulary plus mangled forms ("Sustota.: $15.00").
        if looks_arithmetic(text):
            continue
        # Price-echo annotations the decoder drops anyway: absorbing
        # them cannot shrink |delta|, so spend the proposal elsewhere.
        if (
            WAS_PRICE_RE.search(text)
            or SALE_PRICE_RE.search(text)
            or NON_PRODUCT_NOTE_RE.search(text)
        ):
            continue
        ref = parsed.get("price_word_id")
        price_x = None
        if ref:
            for w in band:
                if (
                    w["line_id"] == ref["line_id"]
                    and w["word_id"] == ref["word_id"]
                ):
                    price_x = w["x"]
                    break
        if price_x is None or abs(price_x - col_x) >= PRICE_COLUMN_TOL:
            continue  # the load-bearing column gate
        # Never absorb a printed summary figure when the receipt
        # already extracts >= 2 items (single-item receipts may
        # legitimately equal the total).
        if n_items >= 2 and round(abs(parsed["price"]), 2) in fv:
            continue
        dist = 0.0 if i_lo <= y <= i_hi else min(abs(y - i_lo), abs(y - i_hi))
        out.append(
            {
                "lids": sorted({w["line_id"] for w in band}),
                "price": parsed["price"],
                "text": text[:60],
                "distance": round(dist, 4),
            }
        )
    out.sort(key=lambda c: (c["distance"], c["lids"]))
    return out


def propose_extension(
    cands: list[dict], delta: float, baseline: Optional[float]
) -> Optional[list[int]]:
    """Candidate subset whose prices close the reconciliation gap.

    ``delta`` is baseline - item_sum (the shortfall). Match tolerance
    first; the near tolerance only when the current gap is itself
    beyond near (mismatch -> near still improves the status, which the
    arithmetic guard requires). Returns the line_ids to add, or None.
    """
    if baseline is None or not cands:
        return None
    tol_match = max(0.02, baseline * 0.01)
    combo = subset_closing_gap(cands, delta, tol_match)
    if not combo:
        tol_near = max(1.0, baseline * 0.10)
        if abs(delta) > tol_near:
            combo = subset_closing_gap(cands, delta, tol_near)
    if not combo:
        return None
    return sorted({lid for i in combo for lid in cands[i]["lids"]})


def split_groups_from_summaries(
    summaries: list[dict],
) -> set[tuple[str, int]]:
    """Receipts that are fragments of the same physical receipt.

    Same merchant + date + printed grand total under DIFFERENT image
    ids = one receipt photographed/cropped more than once. Each
    fragment carries the FULL printed total but only PART of the
    items, so no boundary repair can ever reconcile it (audit caveat).
    """
    groups: dict[tuple, set[tuple[str, int]]] = defaultdict(set)
    for s in summaries:
        merchant = str(s.get("merchant_name") or "").strip().lower()
        date = str(s.get("date") or "")[:10]
        total = s.get("grand_total")
        if not merchant or not date or total in (None, ""):
            continue
        try:
            key = (merchant, date, round(float(total), 2))
        except (TypeError, ValueError):
            continue
        groups[key].add((str(s["image_id"]), int(s["receipt_id"])))
    excluded: set[tuple[str, int]] = set()
    for members in groups.values():
        if len({img for img, _ in members}) >= 2:
            excluded |= members
    return excluded


# ---------------------------------------------------------------------------
# table scans
# ---------------------------------------------------------------------------
def receipt_recon_statuses(client, table: str) -> dict[tuple[str, int], str]:
    """Receipt-level reconciliation_status over RECEIPT_LINE_ITEM rows."""
    out: dict[tuple[str, int], str] = {}
    for raw in _query_all(
        client,
        TableName=table,
        IndexName="GSITYPE",
        KeyConditionExpression="#t = :t",
        ExpressionAttributeNames={"#t": "TYPE"},
        ExpressionAttributeValues={":t": {"S": "RECEIPT_LINE_ITEM"}},
        ProjectionExpression="PK, SK, reconciliation_status",
    ):
        m = re.match(r"IMAGE#(.+)", raw["PK"]["S"])
        m2 = re.match(r"RECEIPT#(\d+)#LINE_ITEM#", raw["SK"]["S"])
        status = (raw.get("reconciliation_status") or {}).get("S")
        if not (m and m2 and status):
            continue
        key = (m.group(1), int(m2.group(1)))
        prev = out.get(key)
        if prev is None or _SEVERITY.get(status, -1) > _SEVERITY.get(prev, -1):
            out[key] = status
    return out


def fetch_all_summaries(client, table: str) -> list[dict]:
    """Every RECEIPT_SUMMARY row (flat fields) with parsed identity."""
    from boto3.dynamodb.types import TypeDeserializer

    des = TypeDeserializer()
    out: list[dict] = []
    for raw in _query_all(
        client,
        TableName=table,
        IndexName="GSITYPE",
        KeyConditionExpression="#t = :t",
        ExpressionAttributeNames={"#t": "TYPE"},
        ExpressionAttributeValues={":t": {"S": "RECEIPT_SUMMARY"}},
    ):
        m = re.match(r"IMAGE#(.+)", raw["PK"]["S"])
        m2 = re.match(r"RECEIPT#(\d+)#SUMMARY", raw["SK"]["S"])
        if not (m and m2):
            continue
        item = {k: des.deserialize(v) for k, v in raw.items()}
        item["image_id"] = m.group(1)
        item["receipt_id"] = int(m2.group(1))
        out.append(item)
    return out


def print_histogram(statuses: dict[tuple[str, int], str]) -> None:
    counts = Counter(statuses.values())
    total = sum(counts.values())
    print(f"receipt-level reconciliation histogram ({total} receipts):")
    for status in ("match", "near", "mismatch", "no-baseline"):
        n = counts.get(status, 0)
        pct = 100.0 * n / total if total else 0.0
        print(f"  {status:<12} {n:>5}  {pct:5.1f}%")


# ---------------------------------------------------------------------------
def _load_extend_impl():
    """The MCP server's guarded extension — the single accept authority."""
    import receipt_mcp_server  # noqa: PLC0415 — needs `mcp` installed

    return receipt_mcp_server.extend_items_section_impl


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--table", default=DEV_TABLE)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--histogram",
        action="store_true",
        help="print the receipt-level reconciliation histogram and exit",
    )
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument(
        "--receipt", help="IMAGE_ID:RECEIPT_ID single-receipt mode"
    )
    args = ap.parse_args()

    if PROD_MARKER in args.table:
        sys.exit("REFUSED: this script never writes to the prod table.")

    client = boto3.client("dynamodb", region_name="us-east-1")
    statuses = receipt_recon_statuses(client, args.table)
    if args.histogram:
        print_histogram(statuses)
        return

    dynamo = DynamoClient(args.table)
    extend_impl = _load_extend_impl()
    excluded_split = split_groups_from_summaries(
        fetch_all_summaries(client, args.table)
    )

    targets = sorted(
        k for k, s in statuses.items() if s in ("mismatch", "near")
    )
    if args.receipt:
        img, rid = args.receipt.split(":")
        targets = [t for t in targets if t == (img, int(rid))]
    if args.limit:
        targets = targets[: args.limit]

    journal = Journal("H-zone-gap", args.apply)
    stats: Counter = Counter()
    transitions: Counter = Counter()
    split_excluded_hits: list[str] = []

    for img, rid in targets:
        stats["examined"] += 1
        key = f"{img}:{rid}"
        rec: dict[str, Any] = {"class": "H-zone-gap", "key": key}
        if (img, rid) in excluded_split:
            stats["excluded-split-receipt"] += 1
            split_excluded_hits.append(key)
            rec["decision"] = "skip: split-receipt fragment"
            journal.propose(rec)
            continue

        words, sections, summary = fetch_receipt_records(
            client, args.table, img, rid
        )
        items_secs = [
            s for s in sections if str(s.get("section_type")) == "ITEMS"
        ]
        if not items_secs:
            stats["skip-no-items-section"] += 1
            continue
        cur_lids = {int(x) for x in items_secs[0].get("line_ids") or []}
        st0, sum0, base0, n0, _ = evaluate(words, summary, cur_lids)
        rec["before"] = {"status": st0, "sum": sum0, "baseline": base0}
        if st0 == "match":
            stats["skip-already-match"] += 1
            continue
        if base0 is None:
            stats["skip-no-baseline"] += 1
            continue

        rows = dynamo.get_receipt_rows_from_receipt(img, rid) or []
        cands = discover_candidates(words, sections, summary, rows, n0)
        if not cands:
            stats["no-candidates"] += 1
            rec["decision"] = "skip: no zone-gap candidates"
            journal.propose(rec)
            continue
        stats["with-candidates"] += 1
        stats["candidate-bands"] += len(cands)

        delta = round(base0 - (sum0 or 0.0), 2)
        added = propose_extension(cands, delta, base0)
        if not added:
            stats["no-closing-subset"] += 1
            rec["decision"] = "skip: no subset closes the gap"
            rec["n_candidates"] = len(cands)
            journal.propose(rec)
            continue
        rec["added_line_ids"] = added

        verdict = asyncio.run(
            extend_impl(dynamo, img, rid, added, dry_run=True)
        )
        if verdict.get("error") or not verdict.get("verified"):
            stats["guard-refused"] += 1
            rec["decision"] = "refused: " + str(
                verdict.get("refusal") or verdict.get("error")
            )
            journal.propose(rec)
            continue

        stats["guard-passed"] += 1
        before_s = verdict["before"]["status"]
        after_s = verdict["after"]["status"]
        transitions[(before_s, after_s)] += 1
        rec["after"] = verdict["after"]
        rec["decision"] = "apply" if args.apply else "would-apply"
        journal.propose(rec)
        print(
            f"  {key}  {before_s}({verdict['before']['delta']}) -> "
            f"{after_s}({verdict['after']['delta']})  +lines {added}"
        )

        if args.apply:
            sec_dict = items_secs[0]
            journal.backup(
                {
                    "action": "zone-gap-extend",
                    "pre_image": sec_dict,
                    "summary_timestamp": (summary or {}).get(
                        "timestamp_computed"
                    ),
                    "key": key,
                }
            )
            applied = asyncio.run(
                extend_impl(dynamo, img, rid, added, dry_run=False)
            )
            if applied.get("applied"):
                stats["applied"] += 1
            else:
                stats["apply-failed"] += 1
                print(f"    APPLY FAILED for {key}: {applied}")
    journal.close()

    print("\n=== zone-gap extension report ===")
    print_histogram(statuses)
    print("stats:", dict(stats))
    if transitions:
        print("guard-passed transitions:")
        for (b, a), n in sorted(transitions.items()):
            print(f"  {b} -> {a}: {n}")
        projected = Counter(statuses.values())
        for (b, a), n in transitions.items():
            projected[b] -= n
            projected[a] += n
        print("projected histogram after apply + stream regen:")
        for status in ("match", "near", "mismatch", "no-baseline"):
            print(f"  {status:<12} {projected.get(status, 0):>5}")
    if split_excluded_hits:
        print(
            f"excluded split-receipt fragments ({len(split_excluded_hits)}):"
        )
        for key in split_excluded_hits:
            print(f"  {key}")
    print(f"journal: {journal.proposal_path}")
    if args.apply:
        print(f"backups: {journal.backup_path}")


if __name__ == "__main__":
    main()
