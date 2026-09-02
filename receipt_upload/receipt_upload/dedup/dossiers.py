"""Shared DynamoDB loader for the dedup CLIs.

Loads receipts/words/labels (and optionally per-receipt totals + merchants)
once, into the plain dict structures consumed by the pure builders in
:mod:`receipt_upload.dedup.context` and the cross-image gate in
:mod:`receipt_upload.dedup.stage5_plan`.
"""

from __future__ import annotations

from collections import defaultdict

from receipt_dynamo import DynamoClient

from receipt_upload.dedup.context import LabelObs

ENV_TABLE = {"dev": "ReceiptsTable-dc5be22", "prod": "ReceiptsTable-d7ff76a"}


def _receipt_place_merchants(
    dc: DynamoClient,
) -> dict[tuple[str, int], str | None]:
    """Load merchant names from raw ``RECEIPT_PLACE`` rows.

    A table-wide dedup census cannot rely on strict entity conversion because
    historical place rows may retain stale secondary-index projections. Query
    ``GSITYPE`` directly and read only scalar identity/name fields instead.
    """
    merchants: dict[tuple[str, int], str | None] = {}
    last_evaluated_key = None

    while True:
        # All DynamoDB operations live in receipt_dynamo: the data layer's
        # raw census read returns low-level items without entity conversion
        # (drifted projections stay readable) while keeping this module off
        # the private _client.
        items, last_evaluated_key = dc.list_receipt_places_raw(
            last_evaluated_key=last_evaluated_key
        )
        for item in items:
            image_id = item.get("image_id", {}).get("S")
            if not image_id:
                raw_pk = item.get("PK", {}).get("S")
                image_id = (
                    raw_pk.removeprefix("IMAGE#")
                    if isinstance(raw_pk, str)
                    and raw_pk.startswith("IMAGE#")
                    and raw_pk != "IMAGE#"
                    else None
                )

            raw_receipt_id = item.get("receipt_id", {}).get("N")
            if raw_receipt_id is None:
                sk_parts = item.get("SK", {}).get("S", "").split("#")
                raw_receipt_id = (
                    sk_parts[1]
                    if len(sk_parts) == 3
                    and sk_parts[0] == "RECEIPT"
                    and sk_parts[2] == "PLACE"
                    else None
                )
            try:
                receipt_id = int(raw_receipt_id)
            except (TypeError, ValueError):
                continue
            if not image_id:
                continue

            merchants[(image_id, receipt_id)] = item.get(
                "merchant_name", {}
            ).get("S")

        if last_evaluated_key is None:
            return merchants


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
    merchants = _receipt_place_merchants(dc)
    return receipts, words_by_receipt, labels_by_receipt, totals, merchants
