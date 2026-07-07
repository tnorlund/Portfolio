"""Shared DynamoDB loader for the dedup CLIs.

Loads receipts/words/labels (and optionally per-receipt totals + merchants)
once, into the plain dict structures consumed by the pure builders in
:mod:`receipt_upload.dedup.context` and the cross-image gate in
:mod:`receipt_upload.dedup.stage5_plan`.
"""

from __future__ import annotations

from collections import defaultdict

from receipt_upload.dedup.context import LabelObs

from receipt_dynamo import DynamoClient

ENV_TABLE = {"dev": "ReceiptsTable-dc5be22", "prod": "ReceiptsTable-d7ff76a"}


def load_inputs(table: str, *, with_summaries: bool = False):
    """Return ``(receipts, words_by_receipt, labels_by_receipt)``.

    With ``with_summaries=True`` also returns ``(totals, merchants)`` (two
    extra table scans), keyed by ``(image_id, receipt_id)`` — needed only by
    the cross-image transaction-identity gate.
    """
    dc = DynamoClient(table)
    receipts = dc.list_receipts()[0]
    words = dc.list_receipt_words()[0]
    labels = dc.list_receipt_word_labels()[0]

    words_by_receipt = defaultdict(dict)
    text_at = {}
    for w in words:
        k = (w.image_id, w.receipt_id)
        t = getattr(w, "text", "") or ""
        words_by_receipt[k][(w.line_id, w.word_id)] = t
        text_at[(w.image_id, w.receipt_id, w.line_id, w.word_id)] = t

    labels_by_receipt = defaultdict(list)
    for lb in labels:
        k = (lb.image_id, lb.receipt_id)
        labels_by_receipt[k].append(
            LabelObs(
                label=lb.label,
                line_id=lb.line_id,
                word_id=lb.word_id,
                word_text=text_at.get(
                    (lb.image_id, lb.receipt_id, lb.line_id, lb.word_id), ""
                ),
                validation_status=getattr(lb, "validation_status", None),
            )
        )

    if not with_summaries:
        return receipts, words_by_receipt, labels_by_receipt

    totals = {}
    for s in dc.list_receipt_summaries()[0]:
        su = getattr(s, "summary", s)
        g = getattr(su, "grand_total", None)
        if g is not None:
            totals[(su.image_id, su.receipt_id)] = float(g)
    merchants = {}
    for m in dc.list_receipt_metadatas()[0]:
        merchants[(m.image_id, m.receipt_id)] = getattr(
            m, "canonical_merchant_name", None
        ) or getattr(m, "merchant_name", None)
    return receipts, words_by_receipt, labels_by_receipt, totals, merchants
