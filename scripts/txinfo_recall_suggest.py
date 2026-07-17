#!/usr/bin/env python3
"""Rank interrupted recall-packet lines for manual TRANSACTION_INFO review.

This is deliberately a review aid, not an auto-labeler.  It favors recall and
records the lexical/contextual reasons that caused a line to be surfaced.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from txinfo_recall_recover import line_key


DIRECT_PATTERNS = {
    "personnel": re.compile(
        r"\b(cashier|ashier|server|sery?er|ser\s+er|served|operator|attendant|"
        r"clerk|associate|sales\s+rep|host|"
        r"helped\s+by|printed\s+by|closed\s+by|assigned|sociate|erver|serv\s*:)\b",
        re.IGNORECASE,
    ),
    "hospitality": re.compile(
        r"\b(table|tbl|guest(?:\s+count)?|chk|cust|tableno|ableno|checkno|leckno)\b",
        re.IGNORECASE,
    ),
    "terminal": re.compile(
        r"(?:\bterminal(?:\s+id)?\b|\bdevice\s+id\b|\bstation\b|\blane\s*#?|\btill\b|"
        r"\b(?:pos|ptid|tid)\b|\bt\s*id\b|term\s*#|ter\s+id|workstation|cashbox)",
        re.IGNORECASE,
    ),
    "transaction": re.compile(
        r"(?:\btransaction\b|\btxn|\btrans\s*(?:id|#|no\.?|number)|\btran\s*(?:id|[:#])|"
        r"(?:order|urder)(?:ed)?\s*[:#]|invoice|check\s*[:#]|"
        r"check\s+(?:number|no\.?)(?:\s*[:#])?|ticket\s*#|"
        r"ticket\s*[:#]|order\s+(?:id|number)|txn\s*id|"
        r"inv\s*(?:#|id|il)|trars\s*#|your\s*#\s*is|q\s*#|"
        r"confirmation\s+(?:no|number)|receipt(?:\s+code|\s*#|\s*:)|"
        r"receipt\s+\d|"
        r"rec\s*#|session\s*#|entry\s+id|refa?\s*[:#]|reference\s*[:#]|"
        r"reference\s+(?:id|no\.?|number)|ref(?:erence)?\s+(?:num|no\.?|number)|"
        r"ret[.,]?\s*(?:number|num|no\.?)\s*:|"
        r"(?:pos|cct)\s+(?:ref|rcr)|(?:tran\s+)?seg\s*#|"
        r"tran\s+(?:seg|serial)\s+(?:#|no\.?|number)|"
        r"\b(?:seq(?:uence)?|sea)\b\s*#?|trace\s*[:#]|rrn\s*[:#]|stan\s*[:#]|tsn\s*:|"
        r"auth/trace|"
        r"\bdate\b|c[/\\]?it\s+time|check[- ]?out\s+time|jrnl|journal)",
        re.IGNORECASE,
    ),
    "authorization": re.compile(
        r"(?:\b(?:auth(?:orization)?|autr)(?:\s+(?:code|id|ref(?:erence)?)|"
        r"\s*\.?\s*(?:#|no\.)|\s*:|\s+[a-z0-9])|"
        r"\bapproval\s*[:#]|\bappr(?:oval)?\s+(?:code|#)|\bapp[a-z]{0,2}\s*[:#])",
        re.IGNORECASE,
    ),
    "membership": re.compile(
        r"\b(?:member(?:ship)?\s*(?:id|no\.?|number)|"
        r"loyalty\s*(?:id|no\.?|number)|customer\s+(?:id|no\.?|number))\b|"
        r"\b(?:member(?:ship)?|loyalty)\s*#|"
        r"\bextracare\s+card\s*(?:#|no\.?|number|id)\s*:?\s*",
        re.IGNORECASE,
    ),
    "register": re.compile(
        r"(?:\breg\b|\breg(?:ister)?\s*[:#]|\bop\s*#|\bstore\s*[:#])",
        re.IGNORECASE,
    ),
}

DATETIME_RE = re.compile(
    r"(?:\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b|"
    r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b|"
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}\b|"
    r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?\b)",
    re.IGNORECASE,
)
PAYMENT_CONTEXT_RE = re.compile(
    r"\b(card|auth|authorization|aid|tvr|iad|tsi|arc|entry|purchase|"
    r"approval|approved|chip|transaction\s+type|total\s+amount|gratuity|"
    r"appr|batch|sales?\s+draft|available|deposit|duration|park[- ]?dur|"
    r"emv|visa|mastercard|debit|credit|issuer|pan|pin|sequence|seq)\b",
    re.IGNORECASE,
)
CODELIKE_RE = re.compile(r"^[#*A-Z0-9][#*A-Z0-9 ./:_-]{0,31}$", re.IGNORECASE)
DIGIT_RUN_RE = re.compile(r"\d{10,}")


def context_text(line: dict[str, Any]) -> str:
    neighbors = [*line.get("context_above", []), *line.get("context_below", [])]
    return " ".join(str(item.get("text", "")) for item in neighbors)


def searchable(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()


def reasons_for(line: dict[str, Any]) -> list[str]:
    text = searchable(str(line.get("line_text", "")))
    context = searchable(context_text(line))
    reasons = [name for name, pattern in DIRECT_PATTERNS.items() if pattern.search(text)]

    context_reasons = [
        name for name, pattern in DIRECT_PATTERNS.items() if pattern.search(context)
    ]
    if context_reasons and CODELIKE_RE.fullmatch(text.strip()):
        reasons.extend(f"paired-{name}" for name in context_reasons)

    if DATETIME_RE.search(text):
        payment_block = (
            line.get("current_label") == "PAYMENT"
            and PAYMENT_CONTEXT_RE.search(context) is not None
        )
        reasons.append("payment-block-datetime" if payment_block else "datetime")

    looks_like_register_value = bool(
        DIGIT_RUN_RE.search(text)
        or re.fullmatch(r"[#A-Z0-9][#A-Z0-9 ./,:_-]{2,31}", text, re.IGNORECASE)
    )
    structured_register_stamp = bool(re.fullmatch(r"\d{18,30}", text.strip()))
    has_digit = re.search(r"\d", text) is not None
    alphabetic_register_stamp = bool(
        DIRECT_PATTERNS["register"].search(context) and DATETIME_RE.search(context)
    )
    if (
        looks_like_register_value
        and (has_digit or alphabetic_register_stamp)
        and (line.get("current_label") == "BARCODE" or structured_register_stamp)
        and (
            DIRECT_PATTERNS["personnel"].search(context)
            or DIRECT_PATTERNS["register"].search(context)
            or DATETIME_RE.search(context)
        )
    ):
        reasons.append("barcode-register-stamp")
    return sorted(set(reasons))


def iter_packet_lines(packets_dir: Path, packet_numbers: list[int]):
    for packet_number in packet_numbers:
        path = packets_dir / f"packet_{packet_number:03d}.json"
        packet = json.loads(path.read_text())
        for line in packet["lines"]:
            yield packet_number, line


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--packet-numbers", type=int, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--labeled-lines", type=Path)
    args = parser.parse_args()

    suggestions = []
    for packet_number, line in iter_packet_lines(args.packets, args.packet_numbers):
        reasons = reasons_for(line)
        if not reasons:
            continue
        suggestions.append(
            {
                "packet": packet_number,
                "id": line_key(line).canonical,
                "reasons": reasons,
                **line,
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(suggestions, indent=2, sort_keys=True) + "\n")

    report: dict[str, Any] = {"suggestions": len(suggestions)}
    if args.labeled_lines:
        known = [json.loads(raw) for raw in args.labeled_lines.read_text().splitlines()]
        true_ids = {item["id"] for item in known if item["is_txinfo"]}
        suggested_ids = {
            line_key(item).canonical for item in known if reasons_for(item)
        }
        report.update(
            {
                "known_positives": len(true_ids),
                "known_positives_surfaced": len(true_ids & suggested_ids),
                "known_positive_recall": (
                    len(true_ids & suggested_ids) / len(true_ids) if true_ids else 1.0
                ),
                "known_lines_surfaced": len(suggested_ids),
            }
        )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
