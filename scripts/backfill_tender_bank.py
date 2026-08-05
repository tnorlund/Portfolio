#!/usr/bin/env python3
"""Backfill tender + bank-match fields onto ReceiptSummary records.

For every receipt in the target table this script:

1. Classifies tender (cash/card/unknown, network, last4) from the
   payment zone via the canonical ``receipt_upload.tender`` classifier.
2. Assigns the card to a ledger using the per-card rules from the
   2026-07 tender report (Apple Card 7645/5061/1769; Chase debit
   1454/3931/0663/5894/7123/3960/8712; 9297/6081 have no export and
   7739 is a checking account on ATM slips, so both map to ``none``).
3. Joins the two ledgers OFFLINE:
   - Chase: curated matches in ``email_receipts.db`` (read-only).
   - Apple Card: merged CSV + statement-PDF ledger, amount-anchored
     with a tip band gated on the Google Places category -- only
     tippable businesses (restaurant, cafe, bar, salon, ...) may
     settle above the printed total; a grocery "match" 30% above the
     receipt is a different shopping trip, not a tip.
4. Writes tender_class / card_network / card_last4 / ledger /
   bank_amount / bank_match_confidence onto the stored
   ReceiptSummaryRecord.

Match confidence:
    chase confirmed 1.0 | chase auto 0.9
    apple exact amount: 0.95 (merchant agrees) / 0.85 (amount-only)
    apple tip-band:     0.4 + 0.4 * merchant_similarity (cap 0.8)

DRY-RUN BY DEFAULT: pass ``--apply`` to write.

Usage:
    python scripts/backfill_tender_bank.py [--env dev|prod]
        [--table-name NAME] [--apply] [--limit N]
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime
import difflib
import glob
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.receipt_summary import (
    MonetaryTotals,
    ReceiptSummary,
)
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
)
from receipt_upload.tender import classify_tender_for_receipt

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------ ledger rules
# Per-card ledger attribution, cross-validated in the tender report:
# the Apple cards drew 204 Apple / 0 Chase matches, the Chase debits
# 148 Chase / ~2 Apple. 5061 is the same Apple Card as 7645 before its
# 2024-10 renumbering.
APPLE_CARDS = frozenset({"7645", "5061", "1769"})
# 8712 is a card on Chase account 5981, which the held export only covers
# from 2025-04-25. The earlier "~10% of 8712 amounts appear in Chase, i.e.
# chance level" reading measured 8712 across its whole span -- most of which
# predates 5981's window -- and against the wrong account. Scored inside
# 5981's own window it hits 8 of 9 (89%), matching the confirmed Chase
# debits (1454 88%, 3931 85%, 5894 89%) against a date-resampled control of
# ~0.2 expected, with same-day same-merchant settlements (THE STAND-WESTLAKE,
# IN-N-OUTWESTLAKEVILL, ANAWALT LUMBER, COSTCO WHSE #0117 x2, WILD FORK
# FOODS, SPROUTS FARMERS MKT#).
CHASE_CARDS = frozenset(
    {"1454", "3931", "0663", "5894", "7123", "3960", "8712"}
)
# 9297's identity is still undetermined at n=5; 6081 is an Amex and no
# export is held for it.
NO_LEDGER_CARDS = frozenset({"9297", "6081"})
# Chase checking account number printed on ATM/bank slips, not a card.
NON_PURCHASE = frozenset({"7739"})

# Ledger sentinels. ``LEDGER_NONE`` is a *decided* value that persists to
# DynamoDB ("this card has no held ledger"); ``LEDGER_UNKNOWN`` is the
# absence of a decision. They are NOT interchangeable, and the string
# "none" satisfies no ``is None`` test -- keep every branch below explicit
# so a value can never fall through the match dispatch unnoticed again.
LEDGER_APPLE = "apple"
LEDGER_CHASE = "chase"
LEDGER_NONE = "none"
LEDGER_UNKNOWN = None

DEFAULT_DB = "/Users/tnorlund/receipts-email/email_receipts.db"
DEFAULT_APPLE_PDF_TXNS = (
    "/Users/tnorlund/.claude/jobs/7a30c911/tmp/apple_pdf_txns.json"
)
DEFAULT_APPLE_CSV_GLOBS = [
    "/Users/tnorlund/taxes_2023src/TylersAppleCard*/*.csv",
    "/Users/tnorlund/taxes_2024/apple_card/*.csv",
    "/Users/tnorlund/taxes_2025/apple_card/"
    "Apple Card Transactions Jan 01 2025 - Apr 06 2026.csv",
]

# Whether a tip can be added at settlement is a property of the
# business (Google Places category), not of its name.
TIPPABLE = re.compile(
    r"restaurant|_bar$|^bar$|cafe|coffee|deli|sandwich|pizza|sushi|ramen"
    r"|bakery|ice_cream|bagel|brewery|pub|steak|diner|juice|smoothie|donut"
    r"|taco|burger|breakfast|brunch|buffet|meal|night_club|wine|food|salon"
    r"|spa|barber|car_wash|nail|massage|tattoo",
    re.I,
)

# --------------------------------------------------------------- date scan
DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b"), "mdy4"),
    (re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2})\b"), "mdy2"),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "ymd"),
]
MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
MON_RE = re.compile(
    r"\b(" + "|".join(MONTHS) + r")[A-Z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b",
    re.I,
)


def _mk(year: int, month: int, day: int) -> datetime.date | None:
    try:
        date = datetime.date(year, month, day)
    except ValueError:
        return None
    return date if 2015 <= date.year <= 2027 else None


def scan_date(text: str) -> datetime.date | None:
    """Scan raw line text for a plausible receipt date."""
    for regex, kind in DATE_PATTERNS:
        for match in regex.finditer(text):
            a, b, c = match.groups()
            if kind == "ymd":
                date = _mk(int(a), int(b), int(c))
            elif kind == "mdy4":
                date = _mk(int(c), int(a), int(b))
            else:
                date = _mk(2000 + int(c), int(a), int(b))
            if date:
                return date
    match = MON_RE.search(text)
    if match:
        return _mk(
            int(match.group(3)),
            MONTHS.index(match.group(1)[:3].upper()) + 1,
            int(match.group(2)),
        )
    return None


# ------------------------------------------------------------ apple ledger
def load_apple_ledger(
    csv_globs: list[str], pdf_txns_path: str
) -> list[dict[str, Any]]:
    """Merged CSV + statement-PDF Apple Card ledger.

    PDF rows fold into the matching CSV row (same date+amount) as extra
    name variants -- the PDF acquirer strings are richer than the CSV's
    cleaned merchant names and carry many of the matches.
    """
    entries: list[dict[str, Any]] = []
    for pattern in csv_globs:
        for path in sorted(glob.glob(pattern)):
            try:
                rows = list(csv.DictReader(open(path)))
            except OSError:
                continue
            for row in rows:
                if row.get("Type") != "Purchase":
                    continue
                raw_date = (row.get("Transaction Date") or "").strip()
                try:
                    date = datetime.datetime.strptime(
                        raw_date, "%m/%d/%Y"
                    ).date()
                    amount = float(row["Amount (USD)"])
                except (ValueError, KeyError):
                    continue
                entries.append(
                    {
                        "date": date,
                        "amount": amount,
                        "names": [
                            (row.get("Merchant") or "").strip(),
                            (row.get("Description") or "").strip(),
                        ],
                    }
                )
    if Path(pdf_txns_path).exists():
        for txn in json.load(open(pdf_txns_path)):
            entries.append(
                {
                    "date": datetime.date.fromisoformat(txn["date"]),
                    "amount": txn["amount"],
                    "names": [txn["desc"]],
                }
            )
    else:
        logger.warning("apple PDF txns not found: %s", pdf_txns_path)

    merged: dict[tuple[datetime.date, float], dict[str, Any]] = {}
    for entry in entries:
        key = (entry["date"], round(entry["amount"], 2))
        if key in merged:
            merged[key]["names"] += entry["names"]
        else:
            merged[key] = dict(entry)
    ledger = list(merged.values())
    for txn in ledger:
        txn["names"] = [n for n in dict.fromkeys(txn["names"]) if n]
    return ledger


def _norm(text: str) -> str:
    text = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", text).strip()


_STOP = {
    "the",
    "inc",
    "llc",
    "co",
    "com",
    "usa",
    "ca",
    "store",
    "market",
    "and",
    "of",
    "at",
    "no",
    "ste",
    "st",
    "blvd",
    "ave",
    "rd",
    "dr",
    "way",
    "n",
    "s",
    "e",
    "w",
    "tst",
    "sq",
    "py",
    "dd",
    "sp",
    "mark",
}


def _toks(text: str) -> set[str]:
    return {
        t
        for t in _norm(text).split()
        if t not in _STOP and len(t) > 2 and not t.isdigit()
    }


def merch_sim(receipt_name: str, ledger_names: list[str]) -> float:
    """Best similarity of the receipt merchant vs any name variant."""
    rtoks = _toks(receipt_name)
    rnorm = _norm(receipt_name)
    best = 0.0
    for name in ledger_names:
        nnorm, ntoks = _norm(name), _toks(name)
        if not nnorm:
            continue
        if rnorm and (rnorm in nnorm or nnorm in rnorm):
            return 1.0
        if rtoks and ntoks:
            overlap = len(rtoks & ntoks)
            if overlap:
                best = max(
                    best, 0.6 + 0.4 * overlap / min(len(rtoks), len(ntoks))
                )
            # prefix match catches truncated acquirer strings
            for a in rtoks:
                for b in ntoks:
                    if (
                        len(a) >= 4
                        and len(b) >= 4
                        and (a.startswith(b) or b.startswith(a))
                    ):
                        best = max(best, 0.8)
        best = max(best, difflib.SequenceMatcher(None, rnorm, nnorm).ratio())
    return best


def match_apple(
    by_amount: dict[float, list[dict[str, Any]]],
    date: datetime.date | None,
    total: float | None,
    merchant: str,
    category: str,
) -> dict[str, Any] | None:
    """Amount-anchored Apple match, tip band gated on category."""
    if date is None or not total or total <= 0:
        return None
    total = round(total, 2)
    tippable = bool(TIPPABLE.search(category or ""))
    candidates = list(by_amount.get(total, []))
    high = total * 1.35 + 0.01 if tippable else total + 0.01
    for amount, txns in by_amount.items():
        if total < amount <= high:
            candidates += txns
    best = None
    for txn in candidates:
        day_delta = abs((txn["date"] - date).days)
        if day_delta > 3:
            continue
        sim = merch_sim(merchant, txn["names"])
        exact = abs(txn["amount"] - total) < 0.005
        if sim < 0.5 and not exact:
            continue
        score = (2.0 if exact else 1.0) + sim - day_delta * 0.1
        if best is None or score > best[0]:
            best = (score, txn, exact, sim)
    if best and best[0] >= 1.4:
        _, txn, exact, sim = best
        if exact:
            confidence = 0.95 if sim >= 0.5 else 0.85
        else:
            confidence = min(0.8, 0.4 + 0.4 * sim)
        return {
            "amount": txn["amount"],
            "confidence": round(confidence, 2),
            "exact": exact,
        }
    return None


def _chase_result(chase: dict[str, Any]) -> tuple[float, float]:
    """Amount + confidence for a curated Chase match."""
    return chase["amount"], 1.0 if chase["status"] == "confirmed" else 0.9


# ------------------------------------------------------------------- main
def fetch_all(client: DynamoClient) -> dict[str, Any]:
    """Pull receipts+words+labels, lines, sections, places, summaries."""
    logger.info("fetching receipt bundles (words + labels)...")
    bundles = {}
    last_key = None
    while True:
        page = client.list_receipt_details(
            limit=100, last_evaluated_key=last_key
        )
        bundles.update(page.bundles)
        last_key = page.last_evaluated_key
        if last_key is None:
            break
    logger.info("  %d receipts", len(bundles))

    def _list_all(method):
        items, last = [], None
        while True:
            page_items, last = method(limit=1000, last_evaluated_key=last)
            items.extend(page_items)
            if last is None:
                break
        return items

    logger.info("fetching lines...")
    lines = _list_all(client.list_receipt_lines)
    logger.info("  %d lines", len(lines))
    logger.info("fetching sections...")
    sections = _list_all(client.list_receipt_sections)
    logger.info("  %d sections", len(sections))
    logger.info("fetching places...")
    places = _list_all(client.list_receipt_places)
    logger.info("  %d places", len(places))
    logger.info("fetching summaries...")
    summaries = _list_all(client.list_receipt_summaries)
    logger.info("  %d summaries", len(summaries))
    return {
        "bundles": bundles,
        "lines": lines,
        "sections": sections,
        "places": places,
        "summaries": summaries,
    }


def run(args: argparse.Namespace) -> None:
    if args.table_name:
        client = DynamoClient(table_name=args.table_name)
    else:
        # imported lazily: pulls pulumi config
        from receipt_dynamo.data._pulumi import load_env

        config = load_env(env=args.env)
        client = DynamoClient(table_name=config["dynamodb_table_name"])

    data = fetch_all(client)

    lines_by = collections.defaultdict(list)
    for line in data["lines"]:
        lines_by[(line.image_id, line.receipt_id)].append(line)
    sections_by = collections.defaultdict(list)
    for section in data["sections"]:
        sections_by[(section.image_id, section.receipt_id)].append(section)
    place_by = {(p.image_id, p.receipt_id): p for p in data["places"]}
    summary_by = {(s.image_id, s.receipt_id): s for s in data["summaries"]}

    # ---------------------------------------------------------- ledgers
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    snapshot = {}
    for image_id, receipt_id, date, cents, category in con.execute(
        "SELECT image_id, receipt_id, date, grand_total_cents,"
        " merchant_category FROM paper_receipts"
    ):
        snapshot[(image_id, receipt_id)] = (date, cents, category)
    chase_matches = {}
    for ref, status, account, amount_cents in con.execute(
        """SELECT m.ref, m.status, c.account, c.amount_cents
           FROM matches m JOIN chase_transactions c USING(txn_id)
           WHERE m.ref_kind='paper'"""
    ):
        image_id, receipt_id = ref.rsplit(":", 1)
        chase_matches[(image_id, int(receipt_id))] = {
            "status": status,
            "account": account,
            "amount": abs(amount_cents) / 100.0,
        }
    con.close()
    logger.info(
        "chase: %d curated paper matches; snapshot rows: %d",
        len(chase_matches),
        len(snapshot),
    )

    apple_ledger = load_apple_ledger(args.apple_csv, args.apple_pdf_txns)
    logger.info("apple ledger: %d unique transactions", len(apple_ledger))
    apple_by_amount = collections.defaultdict(list)
    for txn in apple_ledger:
        apple_by_amount[round(txn["amount"], 2)].append(txn)

    # ------------------------------------------------------- per receipt
    stats = collections.Counter()
    tender_dist = collections.Counter()
    ledger_dist = collections.Counter()
    to_write: list[ReceiptSummaryRecord] = []

    keys = sorted(data["bundles"].keys())
    if args.limit:
        keys = keys[: args.limit]

    for key in keys:
        bundle = data["bundles"][key]
        image_id = bundle.receipt.image_id
        receipt_id = bundle.receipt.receipt_id
        receipt_key = (image_id, receipt_id)
        stats["receipts"] += 1

        tender = classify_tender_for_receipt(
            lines_by.get(receipt_key, []),
            sections_by.get(receipt_key, []),
            bundle.word_labels,
            bundle.words,
        )
        tender_dist[tender.tender_detail] += 1

        stored = summary_by.get(receipt_key)
        place = place_by.get(receipt_key)
        snap = snapshot.get(receipt_key)

        # date: stored summary -> snapshot -> raw line scan
        date = stored.date.date() if stored and stored.date else None
        if date is None and snap and snap[0]:
            try:
                date = datetime.date.fromisoformat(snap[0][:10])
                if not 2015 <= date.year <= 2027:
                    date = None
            except ValueError:
                date = None
        if date is None:
            text = "\n".join(
                l.text
                for l in sorted(
                    lines_by.get(receipt_key, []), key=lambda x: x.line_id
                )
            )
            date = scan_date(text)

        # total: stored summary -> snapshot cents
        total = stored.grand_total if stored else None
        if not total and snap and snap[1]:
            total = snap[1] / 100.0

        merchant = (
            (place.merchant_name if place else None)
            or (stored.merchant_name if stored else None)
            or ""
        )
        category = (
            (getattr(place, "merchant_category", "") if place else "")
            or (snap[2] if snap and snap[2] else "")
            or " ".join(getattr(place, "merchant_types", []) or [])
        )

        # ledger from the per-card rules
        last4 = tender.card_last4
        if last4 in APPLE_CARDS:
            ledger = LEDGER_APPLE
        elif last4 in CHASE_CARDS:
            ledger = LEDGER_CHASE
        elif last4 in NO_LEDGER_CARDS or last4 in NON_PURCHASE:
            ledger = LEDGER_NONE
        elif tender.tender_class == "cash":
            ledger = LEDGER_NONE
        else:
            ledger = LEDGER_UNKNOWN  # unknown card / unknown tender

        # bank match, gated on the card's ledger where known.
        # Every ledger value is handled explicitly; the trailing ``else``
        # makes an unhandled value loud instead of silently dropping a
        # match that was already fetched.
        bank_amount = confidence = None
        chase = chase_matches.get(receipt_key)

        if ledger == LEDGER_CHASE:
            if chase:
                bank_amount, confidence = _chase_result(chase)
        elif ledger == LEDGER_APPLE:
            apple = match_apple(
                apple_by_amount, date, total, merchant, category
            )
            if apple:
                bank_amount = apple["amount"]
                confidence = apple["confidence"]
        elif ledger == LEDGER_UNKNOWN:
            # no attributable card: accept whichever ledger matched
            apple = match_apple(
                apple_by_amount, date, total, merchant, category
            )
            if chase:
                bank_amount, confidence = _chase_result(chase)
                ledger = LEDGER_CHASE
            elif apple:
                bank_amount = apple["amount"]
                confidence = apple["confidence"]
                ledger = LEDGER_APPLE
        elif ledger == LEDGER_NONE:
            # Deliberate: this card has no held ledger, so any curated
            # match is not trustworthy evidence for it. Counted, not
            # silently dropped -- a rising number here means a card was
            # misfiled into NO_LEDGER_CARDS/NON_PURCHASE.
            if chase:
                stats["suppressed_chase_match_no_ledger"] += 1
        else:
            raise ValueError(f"unhandled ledger value: {ledger!r}")

        if bank_amount is not None:
            stats["bank_matched"] += 1
        ledger_dist[ledger or "(unset)"] += 1

        if stored is None:
            stats["no_summary_record"] += 1
            continue

        updated = ReceiptSummary(
            image_id=image_id,
            receipt_id=receipt_id,
            merchant_name=stored.merchant_name,
            date=stored.date,
            totals=MonetaryTotals(**stored.totals.to_dict()),
            item_count=stored.item_count,
            tender_class=tender.tender_class,
            card_network=tender.card_network,
            card_last4=tender.card_last4,
            ledger=ledger,
            bank_amount=bank_amount,
            bank_match_confidence=confidence,
        )
        old = stored.summary
        if updated == old:
            stats["unchanged"] += 1
            continue
        to_write.append(ReceiptSummaryRecord.from_summary(updated))

    # ------------------------------------------------------------ report
    print("=" * 64)
    print(f"TENDER + BANK BACKFILL ({'APPLY' if args.apply else 'DRY RUN'})")
    print("=" * 64)
    print(f"receipts scanned:        {stats['receipts']}")
    print(f"summary record missing:  {stats['no_summary_record']}")
    print(f"already up to date:      {stats['unchanged']}")
    print(f"records to write:        {len(to_write)}")
    print(f"bank-matched:            {stats['bank_matched']}")
    print(
        "curated matches suppressed (ledger=none): "
        f"{stats['suppressed_chase_match_no_ledger']}"
    )
    print("\ntender_detail distribution:")
    for name, count in tender_dist.most_common():
        print(f"  {name:22s} {count:5d}")
    print("\nledger distribution:")
    for name, count in ledger_dist.most_common():
        print(f"  {name:22s} {count:5d}")
    card_class = sum(
        v
        for k, v in tender_dist.items()
        if k in ("card", "card_generic", "split_or_ambiguous")
    )
    print(
        f"\ncoarse classes: card={card_class} "
        f"cash={tender_dist['cash']} unknown={tender_dist['unknown']}"
    )

    if not args.apply:
        print("\nDRY RUN -- nothing written. Re-run with --apply to write.")
        return

    written = 0
    for start in range(0, len(to_write), args.batch_size):
        batch = to_write[start : start + args.batch_size]
        # This script IS the offline source of truth for the bank fields,
        # so it may legitimately retract a match (null a bank_amount) when
        # the curated ledger no longer carries it — hence the explicit
        # opt-out from the offline-field clobber guard.
        client.upsert_receipt_summaries(batch, allow_offline_field_clear=True)
        written += len(batch)
        logger.info("upserted %d / %d", written, len(to_write))
    print(f"\nwrote {written} summary records")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill tender + bank-match fields (dry-run default)"
    )
    parser.add_argument("--env", choices=["dev", "prod"], default="dev")
    parser.add_argument(
        "--table-name",
        help="DynamoDB table name (skips pulumi env lookup)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write updates to DynamoDB (default: dry run)",
    )
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--apple-pdf-txns", default=DEFAULT_APPLE_PDF_TXNS)
    parser.add_argument(
        "--apple-csv",
        nargs="*",
        default=DEFAULT_APPLE_CSV_GLOBS,
        help="Apple Card CSV glob(s)",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=25)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
