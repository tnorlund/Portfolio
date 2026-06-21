"""Semantic (Chroma kNN) PRODUCT_NAME proposer.

The first-pass LayoutLM model emits no PRODUCT_NAME label at all, and the
deterministic geometry pass only recovers product names that sit on the same
OCR row as a price (~0.42 recall on a 487-receipt corpus). Product names
recur across receipts, so a k-NN over already-validated PRODUCT_NAME words
recovers the rest: measured **F1 ~0.84 (P 0.88 / R 0.80)** vs geometry's 0.59.

Two findings drive the design:

* Search **unscoped** by merchant. Merchant-scoping HURTS recall
  (0.80 -> 0.73): the same product is described almost identically across
  merchants, and filtering to one merchant starves the candidate pool. The
  existing validator may keep merchant as a *boost*, but never as a *filter*.
* Propose, don't override. This only proposes ``PRODUCT_NAME`` (status
  PENDING) for words that carry **no** label, in the line-item band. The
  existing Chroma + LLM validators then confirm — so it never overrides a
  deliberate currency/other label.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

_PROPOSED_BY = "semantic_product_name"
_HEADER = {"ADDRESS_LINE", "PHONE_NUMBER", "STORE_HOURS", "MERCHANT_NAME"}
_MONEY = re.compile(r"\$?\d{1,4}[.,]\d{2}")

# Structural / field-keyword tokens that are never products. The kNN can still
# vote PRODUCT_NAME on these (historical mislabels poison the validated pool),
# so guard deterministically: a word whose *entire* normalized text equals one
# of these is skipped. This only blocks exact structural tokens — multi-letter
# product descriptions ("TUNA", "Taxidermy") are unaffected — and removes the
# bulk of the LLM validator's reject load (field keywords + receipt furniture
# over-proposed as PRODUCT_NAME). See the June21 MCP review.
_FIELD_KEYWORDS = frozenset(
    {
        # totals / currency rows
        "total",
        "subtotal",
        "tax",
        "taxes",
        "tip",
        "gratuity",
        "balance",
        "due",
        "change",
        "savings",
        "save",
        "saved",
        "discount",
        "coupon",
        "amount",
        "amt",
        # quantity / unit columns
        "qty",
        "quantity",
        "ea",
        "each",
        "weight",
        "wt",
        "tare",
        "net",
        "oz",
        "lb",
        "lbs",
        "kg",
        "g",
        "ct",
        "pk",
        # payment / card-slip furniture
        "cash",
        "debit",
        "credit",
        "visa",
        "mastercard",
        "amex",
        "discover",
        "card",
        "tender",
        "tendered",
        "payment",
        "paid",
        "auth",
        "approval",
        "approved",
        "ref",
        "invoice",
        "invoi",
        "mid",
        "tid",
        "aid",
        "arc",
        "terminal",
        "trace",
        "batch",
        "seq",
        "us",
        # loyalty / membership
        "member",
        "members",
        "membership",
        "loyalty",
        "rewards",
        "reward",
        "points",
        "pts",
        "extracare",
        # receipt metadata / headers
        "items",
        "item",
        "qty.",
        "server",
        "table",
        "guest",
        "guests",
        "order",
        "receipt",
        "store",
        "reg",
        "register",
        "trn",
        "cashier",
        "cshr",
        "str",
        "date",
        "time",
        "visit",
        "transaction",
        "trans",
        "return",
        "refund",
        "policy",
        "thank",
        "you",
        "welcome",
    }
)


def _is_field_keyword(text: str) -> bool:
    """True when the word's entire text is a structural/field keyword."""
    norm = re.sub(r"[^a-z]", "", text.lower())
    return bool(norm) and norm in _FIELD_KEYWORDS


def _cy(word) -> float | None:
    box = getattr(word, "bounding_box", None)
    if not box:
        return None
    y = box.get("y")
    if y is None:
        return None
    return y + (box.get("height") or 0.0) / 2.0


def _has_letters(text: str) -> bool:
    return len(re.sub(r"[^A-Za-z]", "", text)) >= 2


def _knn_is_product(
    words_client,
    embedding: List[float],
    image_id: str,
    k: int,
    min_similarity: float,
) -> bool:
    """kNN over validated words (UNSCOPED); majority-vote PRODUCT_NAME."""
    try:
        res = words_client.query(
            collection_name="words",
            query_embeddings=[embedding],
            n_results=k + 8,
            where={"label_status": "validated"},
            include=["metadatas", "distances"],
        )
    except Exception:
        return False
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    votes: List[str] = []
    for meta, dist in zip(metas, dists):
        if meta.get("image_id") == image_id:  # exclude the same receipt
            continue
        if max(0.0, 1.0 - (dist / 2.0)) < min_similarity:  # L2 -> similarity
            continue
        labels = meta.get("valid_labels_array") or []
        primary = (
            "PRODUCT_NAME"
            if "PRODUCT_NAME" in labels
            else (labels[0] if labels else None)
        )
        if primary:
            votes.append(primary)
        if len(votes) >= k:
            break
    return (
        bool(votes) and Counter(votes).most_common(1)[0][0] == "PRODUCT_NAME"
    )


def propose_product_names(
    words: List,
    existing_labels: List[ReceiptWordLabel],
    words_client,
    word_embeddings: Dict[Tuple[int, int], List[float]],
    *,
    k: int = 5,
    min_similarity: float = 0.5,
) -> List[ReceiptWordLabel]:
    """Propose PENDING PRODUCT_NAME labels for unlabeled in-band words.

    Args:
        words: ReceiptWord entities (need ``.bounding_box``/``.text``/ids).
        existing_labels: labels already on the receipt (anchors + any proposals
            from the geometry pass); only unlabeled words are candidates.
        words_client: ChromaClient exposing ``.query(collection_name=..., ...)``.
        word_embeddings: cached embeddings keyed by ``(line_id, word_id)``.
    """
    # A word counts as "already labeled" only if it carries a real, non-rejected
    # core label. A pending ``O`` (background) label or an INVALID-only label
    # leaves the word effectively unlabeled — exactly the tokens this pass should
    # be free to fill with PRODUCT_NAME.
    labeled = {
        (lab.line_id, lab.word_id)
        for lab in existing_labels
        if lab.label != "O"
        and lab.validation_status != ValidationStatus.INVALID.value
    }
    label_at = {
        (lab.line_id, lab.word_id): lab.label for lab in existing_labels
    }

    header = [
        _cy(w)
        for w in words
        if label_at.get((w.line_id, w.word_id)) in _HEADER
    ]
    header = [y for y in header if y is not None]
    totals = [
        _cy(w)
        for w in words
        if label_at.get((w.line_id, w.word_id)) == "GRAND_TOTAL"
    ]
    totals = [y for y in totals if y is not None]
    if not header or not totals:
        return []
    lo, hi = min(min(header), min(totals)), max(max(header), max(totals))

    out: List[ReceiptWordLabel] = []
    for w in words:
        key = (w.line_id, w.word_id)
        if key in labeled:
            continue
        cy = _cy(w)
        if cy is None or not (lo < cy < hi):
            continue
        if not _has_letters(w.text) or _MONEY.search(w.text):
            continue
        if _is_field_keyword(w.text):
            continue
        emb = word_embeddings.get(key)
        if emb is None:
            continue
        if _knn_is_product(words_client, emb, w.image_id, k, min_similarity):
            out.append(
                ReceiptWordLabel(
                    image_id=w.image_id,
                    receipt_id=w.receipt_id,
                    line_id=w.line_id,
                    word_id=w.word_id,
                    label="PRODUCT_NAME",
                    reasoning="Semantic kNN over validated product names (unscoped).",
                    timestamp_added=datetime.now(timezone.utc),
                    validation_status=ValidationStatus.PENDING.value,
                    label_proposed_by=_PROPOSED_BY,
                )
            )
    return out
