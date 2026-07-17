#!/usr/bin/env python3
"""Apply two explicit adversarial lenses to recall-pass candidates.

Lens A defends the current PAYMENT/FOOTER/BARCODE label.  Lens B attacks the
content claim by requiring an affirmative TRANSACTION_INFO signal.  Conflicts
are emitted for manual adjudication; no corpus writes are performed here.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from txinfo_recall_recover import line_key
from txinfo_recall_suggest import (
    CODELIKE_RE,
    DATETIME_RE,
    DIRECT_PATTERNS,
    context_text,
    reasons_for,
    searchable,
)


AMOUNT_RE = re.compile(
    r"^\s*(?:usd\s*)?\$?\s*[-+]?\d{1,8}(?:,\d{3})*(?:\.\d{2})?\s*$",
    re.IGNORECASE,
)
MASKED_PAN_RE = re.compile(r"(?:x|\*){3,}\s*\d{0,4}", re.IGNORECASE)
BARE_PAN_RE = re.compile(r"^\s*\d{12,19}\s*$")
EMV_LABEL_RE = re.compile(
    r"^\s*(?:aid|tvr|iad|tsi|ac|tc|arc|cvm|atc|pan\s+seq|issuer|mode|"
    r"application\s+(?:id|label)|card\s+reader|input\s+type|response|"
    r"approval\s+code)\s*:?.*$",
    re.IGNORECASE,
)
HEX_RE = re.compile(r"^[0-9a-f]{12,}$", re.IGNORECASE)
CARD_PAYLOAD_RE = re.compile(r"^[0-9a-fol]{12,}$", re.IGNORECASE)
PAYMENT_DETAIL_RE = re.compile(
    r"\b(card\s*#?|entry\s+method|cardholder|account\s+(?:type|number)|purchase|"
    r"approved|approval|issuer|chip\s+read|contactless|customer\s+copy|"
    r"merchant\s+copy|guest\s+copy|payment\s+(?:amount|id)|change|balance|visa|mastercard|"
    r"amex|american\s+express|debit|credit|gift\s+card|auth(?:orization)?"
    r"(?:\s+code|\s*#|\s*:)|gratuity\s+suggestion|transaction\s+type|"
    r"payment\s+date|date\s+when\s+available|date\s+of\s+birth|"
    r"total\s+transaction\s+amount|park(?:ing)?[- ]?dur(?:ation)?|last\s+4|"
    r"start\s+date|expiration\s+date|"
    r"duration)\b|\bacct\s*:|^\s*type\s*$",
    re.IGNORECASE,
)
TRANSACTION_ID_RE = re.compile(
    r"\b(transaction|txn|trans|tran\s+id|order|invoice|inv\s*#|check\s*#|"
    r"ticket\s*#|receipt\s*#|confirmation|reference|ref\s*#|sequence|"
    r"seq\s*#|trace|rrn|stan|entry\s+id|session\s*#|jrnl|journal)\b",
    re.IGNORECASE,
)
PROMO_POLICY_RE = re.compile(
    r"\b(valid|coupon|offer|discount|weekly\s+ad|save\s+(?:money|paper)|"
    r"returns?|refund|exchange|policy|hours|open\s+\d|vaccine|survey|"
    r"feedback|win\s+a|sign\s+up|visit\s+www|thank\s+you|come\s+again|"
    r"limit\s+one\s+per\s+transaction|not\s+valid|qualifying\s+purchase|"
    r"bring\s+this\s+back|cannot\s+be\s+combined|new\s+deals|code\s+expires|"
    r"deals\s+weekly|save\s+the\s+date|points?\s+(?:expiring|available)|"
    r"no\s+cash\s+redemption|chance\s+to\s+win|pro\s+xtra\s+spend|"
    r"excludes\s+sprouts\s+express|test\s+recommended)\b",
    re.IGNORECASE,
)
STRONG_PROMO_CONTEXT_RE = re.compile(
    r"\b(coupon|offer|discount|returns?|refund|exchange|policy|valid|"
    r"bring\s+this\s+back|cannot\s+be\s+combined|qualifying\s+purchase|"
    r"new\s+deals|deals\s+weekly|save\s+the\s+date|"
    r"points?\s+(?:expiring|available)|no\s+cash\s+redemption|"
    r"chance\s+to\s+win|pro\s+xtra\s+spend|excludes\s+sprouts\s+express|"
    r"test\s+recommended|code\s+expires)\b",
    re.IGNORECASE,
)
AUTH_ONLY_RE = re.compile(
    r"(?:\b(?:auth(?:orization)?|autr)(?:\s+(?:code|id|ref(?:erence)?)|"
    r"\s*\.?\s*(?:#|no\.)|\s*:|\s+[a-z0-9])|"
    r"\bapproval\s*[:#]|\bappr(?:oval)?\s+(?:code|#)|\bapp[a-z]{0,2}\s*[:#])",
    re.IGNORECASE,
)
BATCH_RE = re.compile(
    r"\b(?:batch|bat)\s*(?:#|no\.?|number)?\s*(?::|$)", re.IGNORECASE
)
GENERIC_PAYMENT_TRANSACTION_RE = re.compile(
    r"^\s*(?:(?:payment\s+card\s+purchase|sale)\s+transaction|"
    r"transaction\s+type\s*:?.*|total\s+transaction\s+amount\s*:?.*)\s*$",
    re.IGNORECASE,
)
DECORATION_RE = re.compile(r"^\s*[*#._-]{3,}\s*$")
INVALID_DATE_RE = re.compile(r"(?:^|\s)0[/.-]0{1,2}(?:\s|$)")
PROMO_DATE_RANGE_RE = re.compile(
    r"^\s*(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*day,?.*[-–]"
    r"(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*day,?.*$",
    re.IGNORECASE,
)
APPLIED_PAYMENT_RE = re.compile(
    r"^\s*applied\s+to\s+(?:order|account)\s*:\s*$", re.IGNORECASE
)
PAIRABLE_CLASSES = {
    "personnel",
    "hospitality",
    "terminal",
    "transaction",
    "membership",
    "register",
}


def explicit_terminal_value(item: dict[str, Any]) -> bool:
    """Return whether a code/PAN-looking value is directly paired with TID."""
    above = item.get("context_above", [])
    if not above:
        return False
    immediately_above = searchable(str(above[-1].get("text", "")))
    return re.search(
        r"(?:\btid\b|\bterminal(?:\s+id)?\b|\bdevice\s+id\b|"
        r"\bstation\b|\btill\b|term\s*#)",
        immediately_above,
        re.IGNORECASE,
    ) is not None


def content_exclusion(item: dict[str, Any]) -> str | None:
    """Return the non-TXINFO class that defeats a superficial signal."""
    text = searchable(str(item.get("line_text", ""))).strip()
    context = searchable(context_text(item))
    reasons = reasons_for(item)
    direct_metadata = set(reasons) & {
        "personnel",
        "hospitality",
        "terminal",
        "transaction",
        "membership",
        "register",
    }

    if "payment-block-datetime" in reasons:
        return "explicit card-block timestamp exception"
    if re.match(
        r"^(?:auth(?:orization)?|autr)\s+ref(?:erence)?\b", text, re.IGNORECASE
    ):
        return "authorization reference is PAYMENT"
    if AUTH_ONLY_RE.search(text) and not re.search(
        r"\b(?:trace|rrn|transaction|txn|invoice|order|check)\b|\bref\s*[:#]",
        text,
        re.IGNORECASE,
    ):
        return "authorization-only field is PAYMENT"
    above = item.get("context_above", [])
    immediately_above = searchable(str(above[-1].get("text", ""))) if above else ""
    if (
        CODELIKE_RE.fullmatch(text)
        and AUTH_ONLY_RE.search(immediately_above)
        and not direct_metadata
    ):
        return "value belongs to the preceding authorization field"
    if (
        CODELIKE_RE.fullmatch(text)
        and EMV_LABEL_RE.fullmatch(immediately_above)
        and not direct_metadata
    ):
        return "value belongs to the preceding EMV field"
    if (
        CODELIKE_RE.fullmatch(text)
        and BATCH_RE.search(immediately_above)
        and not direct_metadata
    ):
        return "value belongs to the preceding batch field"
    if CODELIKE_RE.fullmatch(text) and re.search(
        r"\b(?:mid|merchant\s+id)\s*:?\s*$", immediately_above, re.IGNORECASE
    ) and not direct_metadata:
        return "value belongs to the preceding merchant-ID field"
    if BATCH_RE.search(text):
        return "batch identifier is payment configuration"
    if GENERIC_PAYMENT_TRANSACTION_RE.fullmatch(text):
        return "generic card-purchase heading is PAYMENT"
    if APPLIED_PAYMENT_RE.fullmatch(text):
        return "payment-allocation field is PAYMENT"
    if (
        item.get("current_label") == "FOOTER"
        and "datetime" in reasons
        and STRONG_PROMO_CONTEXT_RE.search(context)
    ):
        return "date belongs to a promotional or return-policy footer"
    if (
        item.get("current_label") == "BARCODE"
        and STRONG_PROMO_CONTEXT_RE.search(context)
        and re.fullmatch(r"[\d *#./:-]+", text)
    ):
        return "numeric content belongs to a coupon barcode"
    if PROMO_POLICY_RE.search(text):
        return "promotional, policy, or store-hours content is FOOTER"
    if PROMO_DATE_RANGE_RE.fullmatch(text):
        return "promotional date range is FOOTER"
    if re.search(r"\b(?:www\s*\.|[a-z0-9-]{3,}\s*(?:dot\s+|\.?)com)\b", text, re.IGNORECASE):
        return "website callout is FOOTER"
    if re.search(r"\bplease\s+pay\s+(?:the\s+)?cashier\b", text, re.IGNORECASE):
        return "cashier instruction is FOOTER, not personnel metadata"
    if DECORATION_RE.fullmatch(text):
        return "decorative separator has no metadata value"
    if INVALID_DATE_RE.search(text):
        return "invalid OCR date fragment has no metadata value"
    time_match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", text)
    if time_match and (
        int(time_match.group(1)) > 23 or int(time_match.group(2)) > 59
    ):
        return "invalid OCR time fragment has no metadata value"
    if (
        item.get("current_label") == "FOOTER"
        and "home depot" in str(item.get("merchant", "")).lower()
        and int(item.get("line_id", 0)) >= 70
        and DATETIME_RE.search(text)
    ):
        return "date belongs to a Pro Xtra footer statement"
    if (
        item.get("current_label") == "PAYMENT"
        and re.fullmatch(r"[0-9a-f]{4,8}", text, re.IGNORECASE)
        and re.search(r"\b(?:auth|autr)\s+ref", context, re.IGNORECASE)
    ):
        return "wrapped authorization-reference fragment is PAYMENT"
    below = item.get("context_below", [])
    immediately_below = searchable(str(below[0].get("text", ""))) if below else ""
    if (
        item.get("current_label") == "PAYMENT"
        and re.fullmatch(r"\d{5,8}", text)
        and re.search(r"\btid\s*:?\s*$", immediately_below, re.IGNORECASE)
    ):
        return "merchant-ID value immediately precedes the TID field"
    if (
        item.get("current_label") == "BARCODE"
        and re.fullmatch(r"[a-z0-9]{8,16}", text, re.IGNORECASE)
        and re.fullmatch(r"\d{18,30}", immediately_above)
    ):
        return "auxiliary barcode payload remains BARCODE"
    if (
        item.get("current_label") == "BARCODE"
        and re.fullmatch(r"[\d ]{8,20}", text)
        and re.search(r"\btransaction\s+details\b", immediately_below, re.IGNORECASE)
    ):
        return "unlabeled numeric barcode payload remains BARCODE"
    original_text = str(item.get("line_text", ""))
    compact_alnum = "".join(char for char in original_text if char.isalnum())
    if (
        item.get("current_label") == "PAYMENT"
        and len(compact_alnum) >= 12
        and re.fullmatch(r"[A-Za-z0-9Ѐ-ӿ]+", compact_alnum)
        and re.search(r"\b(?:aid|arc|tsi|tvr|iad|tc|tid|seq|rrn)\b", context, re.IGNORECASE)
        and not direct_metadata
        and not explicit_terminal_value(item)
        and not re.search(
            r"\b(transaction|txn|reference|ref|trace|rrn)\b",
            immediately_above,
            re.IGNORECASE,
        )
    ):
        return "unlabeled card payload in an EMV block is PAYMENT"
    if (
        item.get("current_label") == "PAYMENT"
        and text.upper() == "DATE"
        and re.search(
            r"\b(card|visa|mastercard|debit|credit|ref\s+num)\b",
            context,
            re.IGNORECASE,
        )
    ):
        return "date label belongs to the card-payment block"
    if re.search(r"\bmid\b|merchant\s+id", text, re.IGNORECASE) and not re.search(
        r"\btid\b|terminal", text, re.IGNORECASE
    ):
        return "merchant identifier is payment configuration"
    if (
        (MASKED_PAN_RE.search(text) or BARE_PAN_RE.fullmatch(text))
        and not (direct_metadata & {"membership", "register", "terminal"})
        and not explicit_terminal_value(item)
    ):
        return "card number/PAN is PAYMENT"
    if EMV_LABEL_RE.fullmatch(text):
        return "EMV field is PAYMENT"
    if HEX_RE.fullmatch(text.replace(" ", "")) and re.search(
        r"\b(aid|tvr|iad|tc|arc)\b", context, re.IGNORECASE
    ):
        return "EMV value is PAYMENT"
    if (
        item.get("current_label") == "PAYMENT"
        and CARD_PAYLOAD_RE.fullmatch(text.replace(" ", ""))
        and not direct_metadata
        and not explicit_terminal_value(item)
        and not re.search(
            r"\b(transaction|txn|reference|ref|trace|rrn)\b",
            immediately_above,
            re.IGNORECASE,
        )
    ):
        return "unlabeled hexadecimal card payload is PAYMENT"
    if (
        PAYMENT_DETAIL_RE.search(text)
        and "membership" not in direct_metadata
        and not TRANSACTION_ID_RE.search(text)
    ):
        return "payment-instrument detail is PAYMENT"
    return None


def immediate_context(item: dict[str, Any]) -> str:
    above = item.get("context_above", [])
    below = item.get("context_below", [])
    values = []
    if above:
        values.append(str(above[-1].get("text", "")))
    if below:
        values.append(str(below[0].get("text", "")))
    return searchable(" ".join(values))


def plausible_preceding_signal(item: dict[str, Any], reasons: list[str]) -> bool:
    above = item.get("context_above", [])
    if not above:
        return False
    text = searchable(str(item.get("line_text", ""))).strip()
    immediately_above = searchable(str(above[-1].get("text", "")))
    preceding_classes = {
        name
        for name, pattern in DIRECT_PATTERNS.items()
        if name in PAIRABLE_CLASSES and pattern.search(immediately_above)
    }
    paired_classes = {
        reason.removeprefix("paired-")
        for reason in reasons
        if reason.startswith("paired-")
    }
    matching_classes = preceding_classes & paired_classes
    if not matching_classes or not CODELIKE_RE.fullmatch(text):
        return False
    if re.search(r"\d", text):
        return True
    return bool(
        matching_classes & {"personnel", "hospitality"}
        and re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,39}", text)
    )


def load_labels(directory: Path) -> dict[int, dict[str, bool]]:
    labels: dict[int, dict[str, bool]] = {}
    for path in sorted(directory.glob("packet_*.json")):
        payload = json.loads(path.read_text())
        packet_number = int(payload["packet"])
        labels[packet_number] = {
            str(item["id"]): bool(item["is_txinfo"])
            for item in payload["labels"]
        }
    return labels


def current_label_lens(item: dict[str, Any]) -> tuple[str, str]:
    text = searchable(str(item.get("line_text", ""))).strip()
    context = searchable(context_text(item))
    reasons = reasons_for(item)
    current_label = item.get("current_label")
    direct_reasons = {
        reason for reason in reasons if not reason.startswith("paired-")
    }
    paired_reasons = {
        reason for reason in reasons if reason.startswith("paired-")
    }

    exclusion = content_exclusion(item)
    if exclusion:
        return "REFUTED", exclusion
    if (
        direct_reasons
        & {"personnel", "hospitality", "terminal", "membership", "register"}
    ):
        return "UPHELD", "explicit metadata class defeats the current label"
    if "transaction" in direct_reasons and not PROMO_POLICY_RE.search(text):
        return "UPHELD", "explicit transaction identifier defeats the current label"
    if plausible_preceding_signal(item, reasons):
        return "UPHELD", "value is owned by the preceding metadata field"
    if current_label == "PAYMENT" and AMOUNT_RE.fullmatch(text) and not paired_reasons:
        return "REFUTED", "pure amount/value belongs to the current payment block"
    if (
        MASKED_PAN_RE.fullmatch(text) or MASKED_PAN_RE.search(text)
    ) and not explicit_terminal_value(item):
        return "REFUTED", "masked PAN or card number is PAYMENT"
    if BARE_PAN_RE.fullmatch(text) and not explicit_terminal_value(item) and re.search(
        r"\b(card|visa|mastercard|debit|credit|account)\b", context, re.IGNORECASE
    ):
        return "REFUTED", "unmasked card number is PAYMENT"
    if EMV_LABEL_RE.fullmatch(text) or (
        HEX_RE.fullmatch(text.replace(" ", ""))
        and re.search(r"\b(aid|tvr|iad|tc|arc)\b", context, re.IGNORECASE)
    ):
        return "REFUTED", "EMV label/value is PAYMENT"
    if current_label == "PAYMENT" and re.search(
        r"\b(aid|tvr|iad|tsi|tc|arc|application\s+(?:id|label)|card\s+reader)\b",
        immediate_context(item),
        re.IGNORECASE,
    ):
        return "REFUTED", "value belongs to an adjacent EMV/payment field"
    if current_label == "FOOTER" and PROMO_POLICY_RE.search(text):
        return "REFUTED", "promotional/policy footer context"
    if (
        current_label == "FOOTER"
        and direct_reasons == {"datetime"}
        and STRONG_PROMO_CONTEXT_RE.search(context)
    ):
        return "REFUTED", "date belongs to a promotional/policy footer block"
    below = item.get("context_below", [])
    immediately_below = searchable(str(below[0].get("text", ""))) if below else ""
    if (
        current_label == "BARCODE"
        and re.fullmatch(r"\d{18,24}", text)
        and re.search(r"\bop\s*#", immediately_below, re.IGNORECASE)
    ):
        return "UPHELD", "register stamp is directly followed by its operator record"
    if current_label == "BARCODE" and "barcode-register-stamp" not in reasons:
        if re.fullmatch(r"[\d *#./:-]+", text):
            return "REFUTED", "barcode-like numeric content lacks register context"
    if PAYMENT_DETAIL_RE.search(text) and not TRANSACTION_ID_RE.search(text):
        return "REFUTED", "payment-instrument or authorization detail"
    if re.search(r"\bmid\b|merchant\s+id", text, re.IGNORECASE) and not re.search(
        r"\btid\b|terminal", text, re.IGNORECASE
    ):
        return "REFUTED", "merchant identifier is payment configuration, not transaction metadata"
    if direct_reasons:
        return "UPHELD", "current label cannot explain the metadata signal"
    return "REFUTED", "no reason to move the line from its current section"


def content_lens(item: dict[str, Any]) -> tuple[str, str]:
    reasons = reasons_for(item)
    exclusion = content_exclusion(item)
    if exclusion:
        return "REFUTED", exclusion
    direct = [
        reason
        for reason in reasons
        if reason
        in {
            "personnel",
            "hospitality",
            "terminal",
            "transaction",
            "membership",
            "register",
            "datetime",
            "barcode-register-stamp",
        }
    ]
    if direct:
        return "UPHELD", "affirmative metadata signal: " + ", ".join(direct)
    if plausible_preceding_signal(item, reasons):
        paired = [reason for reason in reasons if reason.startswith("paired-")]
        return "UPHELD", "preceding field owns metadata value: " + ", ".join(paired)
    return "REFUTED", "content lacks a transaction-metadata class"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--recovered-labels", type=Path, required=True)
    parser.add_argument("--completed-labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--include-heuristic-sweep",
        action="store_true",
        help="Verify the union of first-pass positives and all heuristic signals.",
    )
    args = parser.parse_args()

    labels = load_labels(args.recovered_labels)
    overlap = set(labels) & set(load_labels(args.completed_labels))
    if overlap:
        raise ValueError(f"duplicate packet label sources: {sorted(overlap)}")
    labels.update(load_labels(args.completed_labels))

    packet_numbers = {
        int(path.stem.removeprefix("packet_"))
        for path in args.packets.glob("packet_*.json")
    }
    if set(labels) != packet_numbers:
        missing = sorted(packet_numbers - set(labels))
        extra = sorted(set(labels) - packet_numbers)
        raise ValueError(f"packet coverage mismatch: missing={missing}, extra={extra}")

    results = []
    scanned = 0
    first_pass_candidate_count = 0
    heuristic_candidate_count = 0
    union_candidate_count = 0
    for packet_number in sorted(packet_numbers):
        packet = json.loads(
            (args.packets / f"packet_{packet_number:03d}.json").read_text()
        )
        expected_ids = {line_key(line).canonical for line in packet["lines"]}
        if set(labels[packet_number]) != expected_ids:
            raise ValueError(f"line coverage mismatch in packet {packet_number}")
        scanned += len(packet["lines"])
        for line in packet["lines"]:
            canonical = line_key(line).canonical
            first_pass_candidate = labels[packet_number][canonical]
            heuristic_candidate = bool(reasons_for(line))
            first_pass_candidate_count += int(first_pass_candidate)
            heuristic_candidate_count += int(heuristic_candidate)
            if not first_pass_candidate and not (
                args.include_heuristic_sweep and heuristic_candidate
            ):
                continue
            union_candidate_count += 1
            lens_a, reason_a = current_label_lens(line)
            lens_b, reason_b = content_lens(line)
            if lens_a == "UPHELD" and lens_b == "UPHELD":
                outcome = "CONFIRMED"
            elif lens_a == "REFUTED" and lens_b == "REFUTED":
                outcome = "REFUTED"
            else:
                outcome = "DISPUTED"
            results.append(
                {
                    "packet": packet_number,
                    "id": canonical,
                    "candidate_sources": [
                        source
                        for source, included in (
                            ("first-pass", first_pass_candidate),
                            ("heuristic-sweep", heuristic_candidate),
                        )
                        if included
                    ],
                    "outcome": outcome,
                    "lens_current_label": {"verdict": lens_a, "reason": reason_a},
                    "lens_content": {"verdict": lens_b, "reason": reason_b},
                    "signals": reasons_for(line),
                    **line,
                }
            )

    counts = Counter(item["outcome"] for item in results)
    summary = {
        "packets": len(packet_numbers),
        "scanned": scanned,
        "first_pass_candidates": first_pass_candidate_count,
        "heuristic_candidates": heuristic_candidate_count,
        "heuristic_added_candidates": union_candidate_count
        - first_pass_candidate_count,
        "verified_union_candidates": union_candidate_count,
        "outcomes": dict(sorted(counts.items())),
        "method": "full-union-two-adversarial-lenses-v2",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "VERIFICATION_RESULTS.json").write_text(
        json.dumps(results, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "VERIFICATION_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    for outcome in ("CONFIRMED", "DISPUTED", "REFUTED"):
        subset = [item for item in results if item["outcome"] == outcome]
        (args.output / f"{outcome}.json").write_text(
            json.dumps(subset, indent=2, sort_keys=True) + "\n"
        )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
