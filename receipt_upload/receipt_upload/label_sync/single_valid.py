"""Resolve words carrying more than one VALID label down to at most one.

Rules (applied per word, over its VALID labels):

* **junk** labels (``OTHER``/``UNLABELED``/a raw value/instruction text) are
  never real labels -> ``INVALID``.
* a label whose CATEGORY contradicts the word content is wrong -> ``INVALID``:
  a numeric word is not a name; a text word is not an amount.
* if two same-category labels still conflict (amount-vs-amount or
  name-vs-name), keep the highest-precedence one VALID and demote the rest to
  ``NEEDS_REVIEW`` (flagged for a human, not asserted wrong).

Applied to BOTH envs (only where the label exists), gated + backed-up.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List

from receipt_dynamo import DynamoClient

from receipt_upload.dedup._ddb import DYNAMO_ERRORS
from receipt_upload.dedup.context import is_valid_status

AMOUNT = {
    "UNIT_PRICE", "LINE_TOTAL", "TAX", "SUBTOTAL", "GRAND_TOTAL", "DISCOUNT",
    "CHANGE", "TIP", "REFUND", "CASH_BACK", "AVAILABLE_BALANCE", "QUANTITY",
}
# higher precedence (lower number) is kept VALID when a same-category tie
# survives; the order is a deterministic default, the losers go to NEEDS_REVIEW
_PRECEDENCE = {
    lab: i for i, lab in enumerate([
        "GRAND_TOTAL", "SUBTOTAL", "TAX", "LINE_TOTAL", "UNIT_PRICE",
        "DISCOUNT", "QUANTITY", "CHANGE", "TIP", "REFUND", "CASH_BACK",
        "MERCHANT_NAME", "ADDRESS_LINE", "PHONE_NUMBER", "WEBSITE",
        "STORE_HOURS", "PAYMENT_METHOD", "LOYALTY_ID", "DATE", "TIME",
        "PRODUCT_NAME", "COUPON",
    ])
}


def _is_num(text: str) -> bool:
    return bool(re.fullmatch(r"[\$\-]?\d[\d.,]*%?", (text or "").strip()))


def is_junk_label(label: str) -> bool:
    """A 'label' that is actually a raw value / instruction text / OTHER."""
    return (
        label in {"OTHER", "UNLABELED"}
        or bool(re.search(r"[a-z\"()]| ", label))
        or _is_num(label)
    )


def plan_demotions(text: str, valid_labels: List[str]) -> Dict[str, str]:
    """Return ``{label: new_status}`` for labels to demote (rest stay VALID).

    Guarantees at most one VALID label remains for the word.
    """
    junk = [lab for lab in valid_labels if is_junk_label(lab)]
    real = [lab for lab in valid_labels if not is_junk_label(lab)]
    out: Dict[str, str] = {lab: "INVALID" for lab in junk}
    if len(real) <= 1:
        return out

    amts = [lab for lab in real if lab in AMOUNT]
    names = [lab for lab in real if lab not in AMOUNT]
    if _is_num(text) and amts:
        for lab in names:
            out[lab] = "INVALID"  # a number is not a name
        pool = amts
    elif not _is_num(text) and names and amts:
        for lab in amts:
            out[lab] = "INVALID"  # a text word is not an amount
        pool = names
    else:
        pool = real

    if len(pool) <= 1:
        return out
    winner = min(pool, key=lambda lab: _PRECEDENCE.get(lab, 99))
    for lab in pool:
        if lab != winner:
            out[lab] = "NEEDS_REVIEW"
    return out


def build_resolution(env_tables: Dict[str, str]):
    """Compute per-env label demotions to enforce <=1 VALID per word.

    Returns ``(plan, summary)`` where plan is a list of
    ``(env, image_id, receipt_id, line_id, word_id, label, new_status)``.
    """
    per_env = {}
    word_text = {}  # keyed by (env, word_key) — dev/prod text can differ
    for env, table in env_tables.items():
        dc = DynamoClient(table)
        for w in dc.list_receipt_words()[0]:
            word_text[
                (env, (w.image_id, w.receipt_id, w.line_id, w.word_id))
            ] = (getattr(w, "text", "") or "")
        byword = defaultdict(list)
        for lb in dc.list_receipt_word_labels()[0]:
            byword[
                (lb.image_id, lb.receipt_id, lb.line_id, lb.word_id)
            ].append(lb)
        per_env[env] = byword

    plan = []
    summary = defaultdict(int)
    seen_words = set()
    for env, byword in per_env.items():
        for key, lbs in byword.items():
            valid = sorted({
                lb.label
                for lb in lbs
                if is_valid_status(getattr(lb, "validation_status", None))
            })
            if len(valid) < 2:
                continue
            demotions = plan_demotions(
                word_text.get((env, key), ""), valid
            )
            for label, status in demotions.items():
                plan.append((env, *key, label, status))
                summary[status] += 1
            seen_words.add((env, key))
    summary["conflicted_words"] = len(seen_words)
    return plan, dict(summary)


def apply_resolution(plan, env_tables, *, backup_path: str) -> dict:
    """Apply the demotions to each env, backing up prior statuses first."""
    by_env = defaultdict(list)
    for env, img, rid, ln, wd, label, status in plan:
        by_env[env].append((img, rid, ln, wd, label, status))

    report = {"updated": 0, "missing": 0, "errors": []}
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    backup = {"created_at": now, "prior": []}

    # First pass (read-only): resolve each target label + record its prior
    # status, so the backup is complete and durable BEFORE any mutation.
    pending = []  # (env_dynamo, label_entity, new_status)
    clients = {}
    for env, items in by_env.items():
        dc = clients.setdefault(env, DynamoClient(env_tables[env]))
        index = {
            (lb.image_id, lb.receipt_id, lb.line_id, lb.word_id, lb.label): lb
            for lb in dc.list_receipt_word_labels()[0]
        }
        for img, rid, ln, wd, label, status in items:
            lb = index.get((img, rid, ln, wd, label))
            if lb is None:
                report["missing"] += 1
                continue
            backup["prior"].append(
                [env, img, rid, ln, wd, label,
                 getattr(lb, "validation_status", None)]
            )
            pending.append((dc, lb, status))

    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(backup, f)
    report["backup_path"] = backup_path

    # Second pass: mutate (backup already persisted -> always recoverable).
    for dc, lb, status in pending:
        try:
            lb.validation_status = status
            lb.label_proposed_by = "claude-conflict-resolution"
            dc.update_receipt_word_label(lb)
            report["updated"] += 1
        except DYNAMO_ERRORS as exc:  # surfaced, not raised
            report["errors"].append(
                f"{lb.image_id}#{lb.receipt_id} "
                f"{lb.line_id}:{lb.word_id} {lb.label}: {exc}"
            )
    return report
