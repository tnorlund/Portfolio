"""Repair ITEMS-section coverage so line-item extraction can see the items.

Four repair classes (2026-07-29 section-repair review, reports in
~/portfolio-backups/2026-07-29/line-item-team/section_repair.md):

  B  ITEMS row exists but PENDING/INVALID: promote to VALID — only when
     extraction over the existing line_ids already reconciles (match/near)
     against the receipt summary.
  C  ITEMS section too narrow: absorb unsectioned priced bands in the items
     zone, but only a subset whose prices close the reconciliation gap, and
     only when re-running the real extractor confirms match/near.
  A  No ITEMS row but real products present: create one from the unsectioned
     zone. Gated behind --allow-create plus an explicit allowlist file of
     "image_id:receipt_id" lines (human-reviewed).
  D  No sections at all (no ReceiptRow entities exist): out of scope here —
     run scripts/backfill_receipt_rows.py first, then the upload-determinism
     assigner; this script only reports such receipts.

Safety:
  * --dry-run by default; --apply to write.
  * Hard refusal of the prod table (name containing "d7ff76a").
  * Pre-image gzip JSONL backup of every row to be mutated, fsynced before
    the first write, plus a *_proposals.jsonl.gz audit stream.
  * Invariants enforced per receipt (violations skip the receipt):
      1. no line_ids overlap with any other section on the receipt;
      2. when the section carries row_ids, they are recomputed from
         RECEIPT_ROW and validate_section_row_coverage() must pass
         (it is not called by any write path — we call it ourselves);
      3. extract_items + reconcile over the proposed line set must land
         match/near.

Usage:
    python3.12 scripts/repair_item_sections.py --classes B,C [--apply]
    python3.12 scripts/repair_item_sections.py --classes A --allow-create \
        --allowlist reviewed_class_a.txt --apply
"""

from __future__ import annotations

import argparse
import gzip
import itertools
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/Users/tnorlund/Portfolio/receipt_dynamo")

import boto3  # noqa: E402

from extract_line_items import (  # noqa: E402
    band_words,
    extract_items,
    fetch_receipt_records,
    parse_band,
    reconcile,
)
from receipt_dynamo.data.dynamo_client import DynamoClient  # noqa: E402
from receipt_dynamo.entities.receipt_section import (  # noqa: E402
    ReceiptSection,
    validate_section_row_coverage,
)

DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_MARKER = "d7ff76a"
BACKUP_DIR = os.path.expanduser(
    f"~/portfolio-backups/{datetime.now(timezone.utc):%Y-%m-%d}"
)
REPAIR_TAG = "items-repair-v1"
HEADER_SECTIONS = ("STOREFRONT", "ADDRESS", "TRANSACTION_INFO")
SUMMARY_SECTIONS = ("SUMMARY", "TOTAL_LINE", "PAYMENT")

# ---------------------------------------------------------------------------
# OCR-tolerant arithmetic-row detector (lifted from the review's guard.py —
# plain keyword matching misses OCR-mangled rows like "Sustota.: $15.00").
ARITH_VOCAB = (
    "subtotal", "total", "tax", "balance", "amount", "change", "savings",
    "saving", "payment", "tender", "tip", "gratuity", "due", "cash",
    "credit", "debit", "refund", "deposit", "fee", "surcharge", "rounding",
    "grand", "net", "visa", "mastercard",
)
_SHORT = {"bal", "tax", "tot", "sub", "amt", "chg", "tip", "due", "net"}
_PCT_RE = re.compile(r"\d+\s*%")


def _lev(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def looks_arithmetic(text: str) -> bool:
    """Does this band name a receipt arithmetic row (subtotal/total/...)?"""
    if _PCT_RE.search(text):
        return True
    for tok in re.findall(r"[A-Za-z]{3,}", text.lower()):
        if tok in _SHORT:
            return True
        if tok.startswith("sub") and len(tok) >= 6:
            return True
        for w in ARITH_VOCAB:
            if tok == w:
                return True
            m = max(len(tok), len(w))
            if len(tok) >= 4 and 1.0 - _lev(tok, w) / m >= 0.72:
                return True
    return False


# ---------------------------------------------------------------------------
def evaluate(words: list[dict], summary: Optional[dict], line_ids: set[int]):
    """Run the real extractor over a line set and reconcile."""
    items, collapsed = extract_items(words, line_ids)
    status, item_sum, baseline = reconcile(
        [x for x in items if not x["is_discount"]], summary
    )
    return status, item_sum, baseline, len(items), collapsed


def _f(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def forbidden_values(summary: Optional[dict]) -> set[float]:
    """Prices that ARE summary figures — absorbing them fakes a repair."""
    if not summary:
        return set()
    sub, gr, tax = (
        _f(summary.get("subtotal")),
        _f(summary.get("grand_total")),
        _f(summary.get("tax")),
    )
    out = {round(v, 2) for v in (sub, gr, tax) if v}
    if gr and tax:
        out.add(round(gr - tax, 2))
    return out


def candidate_bands(
    words: list[dict],
    sections: list[dict],
    summary: Optional[dict],
    zone: str,
) -> tuple[set[int], list[dict]]:
    """Unsectioned priced bands eligible for absorption into ITEMS."""
    by_type: dict[str, set[int]] = defaultdict(set)
    for s in sections:
        by_type[str(s.get("section_type"))].update(
            int(x) for x in (s.get("line_ids") or [])
        )
    items = by_type["ITEMS"]
    sectioned = set()
    for k, v in by_type.items():
        sectioned |= v

    def yspan(lids: set[int]) -> Optional[tuple[float, float]]:
        ys = [w["y_mid"] for w in words if w["line_id"] in lids]
        return (min(ys), max(ys)) if ys else None

    y_sum = yspan(
        by_type["SUMMARY"] | by_type["TOTAL_LINE"] | by_type["PAYMENT"]
    )
    y_hdr = yspan(set().union(*(by_type[h] for h in HEADER_SECTIONS)))
    y_items = yspan(items)
    if zone == "items" and y_items:
        lo = min(y_items[0], y_sum[1]) if y_sum else y_items[0]
        hi = (
            max(y_items[1], y_hdr[0])
            if y_hdr and y_hdr[0] > y_items[1]
            else y_items[1]
        )
    else:
        lo = y_sum[1] if y_sum else 0.0
        hi = y_hdr[0] if y_hdr else 1.0
        if hi <= lo:
            lo, hi = 0.0, 1.0

    # NOTE on orientation: y ranges here only bound the candidate zone; the
    # subset search + re-extraction confirmation below is what accepts a
    # repair, so a loose zone costs candidates, not correctness.
    free_words = [w for w in words if w["line_id"] not in sectioned]
    cands = []
    for band in band_words(free_words):
        y = sum(w["y_mid"] for w in band) / len(band)
        if not (lo <= y <= hi):
            continue
        parsed = parse_band(band)
        if parsed is None or parsed["price"] in (None, 0):
            continue
        text = parsed["raw_text"]
        if looks_arithmetic(text):
            continue
        cands.append(
            {
                "lids": sorted({w["line_id"] for w in band}),
                "price": parsed["price"],
                "text": text[:60],
            }
        )
    fv = forbidden_values(summary)
    if len(cands) > 1:
        # value guard: a band whose price IS a summary figure is suspect —
        # unless it is the only candidate (single-item receipts).
        cands = [c for c in cands if round(abs(c["price"]), 2) not in fv]
    return items, cands


def subset_closing_gap(
    cands: list[dict], delta: float, tol: float, maxk: int = 6
) -> Optional[list[int]]:
    if abs(delta) <= tol:
        return []
    for k in range(1, min(len(cands), maxk) + 1):
        for combo in itertools.combinations(range(len(cands)), k):
            if abs(sum(cands[i]["price"] for i in combo) - delta) <= tol:
                return list(combo)
    return None


# ---------------------------------------------------------------------------
class Journal:
    """Pre-image backups + proposal audit, fsynced before any write."""

    def __init__(self, cls: str, apply_mode: bool):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        mode = "apply" if apply_mode else "dryrun"
        self.backup_path = os.path.join(
            BACKUP_DIR, f"section_repair_{cls}_{mode}_{stamp}.jsonl.gz"
        )
        self.proposal_path = os.path.join(
            BACKUP_DIR, f"section_repair_{cls}_{mode}_{stamp}_proposals.jsonl.gz"
        )
        self._backup = gzip.open(self.backup_path, "wt")
        self._props = gzip.open(self.proposal_path, "wt")

    def backup(self, row: dict) -> None:
        self._backup.write(json.dumps(row, default=str) + "\n")
        self._backup.flush()
        os.fsync(self._backup.fileno())

    def propose(self, rec: dict) -> None:
        self._props.write(json.dumps(rec, default=str) + "\n")
        self._props.flush()

    def close(self) -> None:
        self._backup.close()
        self._props.close()


def check_invariants(
    dynamo: DynamoClient,
    section: ReceiptSection,
    all_sections: list[dict],
    words: list[dict],
    summary: Optional[dict],
) -> Optional[str]:
    """Return a failure reason, or None when all three invariants hold."""
    new_lids = set(section.line_ids)
    for s in all_sections:
        if str(s.get("section_type")) == "ITEMS":
            continue
        overlap = new_lids & {int(x) for x in (s.get("line_ids") or [])}
        if overlap:
            return f"overlap with {s.get('section_type')}: {sorted(overlap)}"

    rows = dynamo.get_receipt_rows_from_receipt(
        section.image_id, section.receipt_id
    )
    if section.row_ids is not None or rows:
        covering = [
            r for r in rows if set(r.line_ids) & new_lids
        ]
        union = set()
        for r in covering:
            union |= set(int(x) for x in r.line_ids)
        if union >= new_lids and covering:
            section.row_ids = sorted({int(r.row_id) for r in covering})
            try:
                validate_section_row_coverage(section, rows)
            except ValueError:
                # coverage union is wider than line_ids: widen line_ids to
                # keep the invariant (rows are the authoritative grouping)
                section.line_ids = sorted(union)
                try:
                    validate_section_row_coverage(section, rows)
                except ValueError as exc:
                    return f"row coverage: {exc}"
        else:
            # an absorbed line maps to no row -> per plan, drop the receipt
            if section.row_ids is not None:
                return "absorbed line maps to no ReceiptRow"

    status, item_sum, baseline, n_items, _ = evaluate(
        words, summary, set(section.line_ids)
    )
    if status not in ("match", "near"):
        return f"post-repair reconcile is {status} ({item_sum} vs {baseline})"
    return None


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default=DEV_TABLE)
    ap.add_argument("--classes", default="B,C", help="comma list of B,C,A,D")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--allow-create", action="store_true")
    ap.add_argument("--allowlist", help="file of image_id:receipt_id lines (class A)")
    args = ap.parse_args()

    if PROD_MARKER in args.table:
        sys.exit("REFUSED: this script never writes to the prod table.")
    classes = {c.strip().upper() for c in args.classes.split(",")}

    client = boto3.client("dynamodb", region_name="us-east-1")
    dynamo = DynamoClient(args.table)

    # Census: every receipt's ITEMS section rows (all statuses)
    from boto3.dynamodb.types import TypeDeserializer

    des = TypeDeserializer()
    sections_by_receipt: dict[tuple[str, int], list[dict]] = defaultdict(list)
    kwargs = dict(
        TableName=args.table,
        IndexName="GSITYPE",
        KeyConditionExpression="#t = :t",
        ExpressionAttributeNames={"#t": "TYPE"},
        ExpressionAttributeValues={":t": {"S": "RECEIPT_SECTION"}},
    )
    while True:
        resp = client.query(**kwargs)
        for raw in resp["Items"]:
            item = {k: des.deserialize(v) for k, v in raw.items()}
            m = re.match(r"IMAGE#(.+)", item["PK"])
            m2 = re.match(r"RECEIPT#(\d+)#", item["SK"])
            if m and m2:
                sections_by_receipt[(m.group(1), int(m2.group(1)))].append(item)
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    allowset: set[tuple[str, int]] = set()
    if args.allowlist:
        for line in open(args.allowlist):
            line = line.strip()
            if line and not line.startswith("#"):
                img, rid = line.split(":")
                allowset.add((img, int(rid)))

    stats: dict[str, int] = defaultdict(int)
    for cls in sorted(classes & {"B", "C", "A"}):
        journal = Journal(cls, args.apply)
        print(f"=== class {cls} ({'APPLY' if args.apply else 'dry-run'}) ===")
        for (img, rid), secs in sorted(sections_by_receipt.items()):
            items_secs = [
                s for s in secs if str(s.get("section_type")) == "ITEMS"
            ]
            has_valid = any(
                s.get("validation_status") == "VALID" for s in items_secs
            )
            if cls == "B" and not (items_secs and not has_valid):
                continue
            if cls == "C" and not has_valid:
                continue
            if cls == "A" and (items_secs or (img, rid) not in allowset):
                continue
            if cls == "A" and not args.allow_create:
                continue

            words, live_secs, summary = fetch_receipt_records(
                client, args.table, img, rid
            )
            sec_dict = next(iter(items_secs), None)
            cur_lids = (
                {int(x) for x in sec_dict.get("line_ids", [])}
                if sec_dict
                else set()
            )
            st0, sum0, base0, _, _ = evaluate(words, summary, cur_lids)
            rec: dict[str, Any] = {
                "class": cls,
                "key": f"{img}:{rid}",
                "before": {"status": st0, "sum": sum0, "baseline": base0},
            }

            proposed_lids = set(cur_lids)
            if cls == "B":
                if st0 not in ("match", "near"):
                    rec["decision"] = f"skip: extraction is {st0}"
                    journal.propose(rec)
                    stats["B-skipped"] += 1
                    continue
            else:
                if base0 is None:
                    rec["decision"] = "skip: no baseline"
                    journal.propose(rec)
                    stats[f"{cls}-no-baseline"] += 1
                    continue
                if cls == "C" and st0 in ("match", "near"):
                    continue
                _, cands = candidate_bands(
                    words, live_secs, summary, "items" if cls == "C" else "zone"
                )
                delta = round(base0 - (sum0 or 0), 2)
                tol = max(0.02, base0 * 0.01)
                combo = subset_closing_gap(cands, delta, tol)
                if combo is None:
                    rec["decision"] = "skip: no subset closes the gap"
                    rec["n_candidates"] = len(cands)
                    journal.propose(rec)
                    stats[f"{cls}-no-fit"] += 1
                    continue
                added = sorted(
                    {l for i in combo for l in cands[i]["lids"]}
                )
                proposed_lids |= set(added)
                rec["added_line_ids"] = added
                rec["added_texts"] = [cands[i]["text"] for i in combo]

            # Build the entity to write
            if sec_dict:
                section = ReceiptSection(
                    receipt_id=rid,
                    image_id=img,
                    section_type="ITEMS",
                    line_ids=sorted(proposed_lids),
                    created_at=str(
                        sec_dict.get("created_at")
                        or datetime.now(timezone.utc)
                    ),
                    confidence=(
                        float(sec_dict["confidence"])
                        if sec_dict.get("confidence") is not None
                        else None
                    ),
                    model_source=(
                        f"{sec_dict.get('model_source')}+{REPAIR_TAG}"
                        if cls == "C"
                        else sec_dict.get("model_source")
                    ),
                    # B: promote on reconcile evidence. C: KEEP the prior
                    # status — the section was already VALID and the
                    # extension is reconciliation-verified; demoting it
                    # hides the repaired section from the extractor's
                    # VALID gate (the bug this script exists to fix).
                    validation_status=(
                        "VALID"
                        if cls == "B"
                        else sec_dict.get("validation_status") or "PENDING"
                    ),
                    row_ids=(
                        [int(x) for x in sec_dict["row_ids"]]
                        if sec_dict.get("row_ids")
                        else None
                    ),
                )
            else:
                section = ReceiptSection(
                    receipt_id=rid,
                    image_id=img,
                    section_type="ITEMS",
                    line_ids=sorted(proposed_lids),
                    created_at=datetime.now(timezone.utc),
                    model_source=f"section-{REPAIR_TAG}",
                    validation_status="PENDING",
                )

            reason = check_invariants(
                dynamo, section, live_secs, words, summary
            )
            if reason:
                rec["decision"] = f"skip: invariant — {reason}"
                journal.propose(rec)
                stats[f"{cls}-invariant"] += 1
                continue

            st1, sum1, _, n1, _ = evaluate(
                words, summary, set(section.line_ids)
            )
            rec["after"] = {"status": st1, "sum": sum1, "n_items": n1}
            rec["decision"] = "apply" if args.apply else "would-apply"
            journal.propose(rec)
            stats[f"{cls}-proposed"] += 1
            print(
                f"  {img}:{rid}  {st0}({sum0}) -> {st1}({sum1}) "
                f"baseline={base0}"
                + (
                    f"  +lines {rec.get('added_line_ids')}"
                    if rec.get("added_line_ids")
                    else "  (status promotion)"
                )
            )

            if args.apply:
                if sec_dict:
                    journal.backup(
                        {"action": f"class-{cls}", "pre_image": sec_dict}
                    )
                    dynamo.update_receipt_section(section)
                else:
                    journal.backup(
                        {"action": "class-A-create", "pre_image": None,
                         "key": f"{img}:{rid}"}
                    )
                    dynamo.add_receipt_section(section)
                stats[f"{cls}-applied"] += 1
        journal.close()
        print(
            f"  journal: {journal.proposal_path}"
            + (f"\n  backups: {journal.backup_path}" if args.apply else "")
        )

    if "D" in classes:
        all_receipts: set[tuple[str, int]] = set()
        kwargs = dict(
            TableName=args.table,
            IndexName="GSITYPE",
            KeyConditionExpression="#t = :t",
            ExpressionAttributeNames={"#t": "TYPE"},
            ExpressionAttributeValues={":t": {"S": "RECEIPT"}},
            ProjectionExpression="PK, SK",
        )
        while True:
            resp = client.query(**kwargs)
            for raw in resp["Items"]:
                m = re.match(r"IMAGE#(.+)", raw["PK"]["S"])
                m2 = re.match(r"RECEIPT#(\d+)$", raw["SK"]["S"])
                if m and m2:
                    all_receipts.add((m.group(1), int(m2.group(1))))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        sectionless = sorted(all_receipts - set(sections_by_receipt))
        print(
            f"=== class D === {len(sectionless)} receipts have no sections "
            "(and typically no ReceiptRow entities). Not automated here: run "
            "scripts/backfill_receipt_rows.py for them, then the "
            "upload-determinism assigner."
        )
        for img, rid in sectionless:
            print(f"  {img}:{rid}")

    print("\nsummary:", dict(stats))


if __name__ == "__main__":
    main()
