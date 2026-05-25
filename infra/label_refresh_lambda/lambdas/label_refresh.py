"""
Label Refresh on Text Change Lambda Handler.

Triggered by the DynamoDB stream when a ReceiptWord's text attribute
changes (most commonly after the Mac OCR worker writes new text from a
regional re-OCR job). For each affected word, re-runs similarity
validation against the Chroma `words` collection and updates each
label's validation_status accordingly.

Mirrors the per-label logic in
`scripts/receipt_mcp_server.py::validate_word_similarity_impl`, but
runs server-side so labels stay in sync without manual intervention.

Trigger: DynamoDB stream (EventSourceMapping with filter on
`SK begins_with LINE# AND eventName=MODIFY`).

Environment:
    DYNAMODB_TABLE_NAME      — the ReceiptsTable name
    CHROMA_CLOUD_API_KEY     — Chroma Cloud API key
    CHROMA_CLOUD_TENANT      — Chroma Cloud tenant
    CHROMA_CLOUD_DATABASE    — Chroma Cloud database (e.g. receipt_dev)
    MIN_SIMILARITY           — similarity floor (default 0.80)
    MIN_MATCHES              — minimum same-label matches (default 3)
    CONSENSUS_THRESHOLD      — vote threshold for VALID/INVALID (default 0.80)
    SAME_MERCHANT_BOOST      — similarity bump for same-merchant evidence (default 0.10)
    DRY_RUN                  — "true" to log proposed updates without writing
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Lazy module-scope clients so warm Lambda containers reuse connections.
_dynamo_client = None
_chroma_client = None
_words_collection = None

# SK pattern for a ReceiptWord row: LINE#{line_id:05d}#WORD#{word_id:05d}
_WORD_SK_RE = re.compile(r"^LINE#(\d{5})#WORD#(\d{5})$")


def _get_clients():
    """Initialize DynamoDB + Chroma clients once per container."""
    global _dynamo_client, _chroma_client, _words_collection
    if _dynamo_client is None:
        from receipt_chroma.data.chroma_client import ChromaClient
        from receipt_dynamo import DynamoClient

        _dynamo_client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])
        _chroma_client = ChromaClient(
            cloud_api_key=os.environ["CHROMA_CLOUD_API_KEY"],
            cloud_tenant=os.environ["CHROMA_CLOUD_TENANT"],
            cloud_database=os.environ["CHROMA_CLOUD_DATABASE"],
            mode="read",
        )
        _words_collection = _chroma_client.get_collection("words")
        logger.info("Initialized DynamoDB + Chroma clients")
    return _dynamo_client, _chroma_client, _words_collection


def _extract_word_change(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return word-change info for a MODIFY record whose text changed.

    Filters out every other entity type. Returns None for non-targets.
    """
    if record.get("eventName") != "MODIFY":
        return None
    dynamodb = record.get("dynamodb", {})
    new_image = dynamodb.get("NewImage") or {}
    old_image = dynamodb.get("OldImage") or {}

    pk = new_image.get("PK", {}).get("S", "")
    sk = new_image.get("SK", {}).get("S", "")
    if not pk.startswith("IMAGE#"):
        return None

    match = _WORD_SK_RE.match(sk)
    if not match:
        return None

    new_text = new_image.get("text", {}).get("S", "")
    old_text = old_image.get("text", {}).get("S", "")
    if new_text == old_text:
        return None  # Not a text change

    return {
        "image_id": pk.removeprefix("IMAGE#"),
        "line_id": int(match.group(1)),
        "word_id": int(match.group(2)),
        "receipt_id": int(new_image.get("receipt_id", {}).get("N", "0")),
        "new_text": new_text,
        "old_text": old_text,
    }


def _dist_to_sim(distance: float) -> float:
    """L2 distance → cosine-like similarity in [0, 1]."""
    return max(0.0, 1.0 - (distance / 2.0))


def _evaluate_label(
    words_collection,
    *,
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    label: str,
    min_similarity: float,
    min_matches: int,
    consensus_threshold: float,
    same_merchant_boost: float,
) -> dict[str, Any] | None:
    """Decide a label's new validation_status from Chroma similarity evidence.

    Returns None if the word has no embedding or insufficient evidence.
    """
    chroma_id = (
        f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"
        f"#LINE#{line_id:05d}#WORD#{word_id:05d}"
    )
    result = words_collection.get(
        ids=[chroma_id], include=["embeddings", "metadatas"]
    )
    if not result.get("ids"):
        return None
    embeddings = result.get("embeddings") or []
    if not len(embeddings):
        return None
    embedding = embeddings[0]
    if hasattr(embedding, "tolist"):
        embedding = embedding.tolist()
    meta = (result.get("metadatas") or [{}])[0]
    merchant = meta.get("merchant_name", "")

    label_field = f"label_{label}"
    pos = words_collection.query(
        query_embeddings=[embedding],
        n_results=10,
        where={"$and": [{"label_status": "validated"}, {label_field: True}]},
        include=["metadatas", "distances"],
    )
    neg = words_collection.query(
        query_embeddings=[embedding],
        n_results=10,
        where={"$and": [{"label_status": "validated"}, {label_field: False}]},
        include=["metadatas", "distances"],
    )

    def _consensus(query_result: dict[str, Any]) -> float:
        metas = (query_result.get("metadatas") or [[]])[0]
        dists = (query_result.get("distances") or [[]])[0]
        total = 0.0
        for m, d in zip(metas, dists):
            sim = _dist_to_sim(d)
            if sim < min_similarity:
                continue
            cand_id = (
                f"IMAGE#{m.get('image_id', '')}#RECEIPT#{m.get('receipt_id', 0):05d}"
                f"#LINE#{m.get('line_id', 0):05d}#WORD#{m.get('word_id', 0):05d}"
            )
            if cand_id == chroma_id:
                continue
            weight = sim
            if m.get("merchant_name") == merchant and merchant:
                weight = min(1.0, weight + same_merchant_boost)
            total += weight
        return total

    votes_for = _consensus(pos)
    votes_against = _consensus(neg)
    total_votes = votes_for + votes_against
    if total_votes == 0:
        return None  # No similar validated words

    # Approximate match count by counting items above similarity floor
    def _count(query_result: dict[str, Any]) -> int:
        dists = (query_result.get("distances") or [[]])[0]
        return sum(1 for d in dists if _dist_to_sim(d) >= min_similarity)

    n_for, n_against = _count(pos), _count(neg)
    if n_for + n_against < min_matches:
        return None

    confidence = votes_for / total_votes
    if confidence >= consensus_threshold:
        status, reason = "VALID", f"{confidence:.0%} similar VALID for {label}"
    elif confidence <= (1.0 - consensus_threshold):
        status, reason = "INVALID", f"{(1 - confidence):.0%} similar REJECTED {label}"
    else:
        status, reason = "NEEDS_REVIEW", f"Mixed: {confidence:.0%} for"
    return {"status": status, "reason": reason, "confidence": round(confidence, 3)}


def _process_word(
    dynamo,
    words_collection,
    *,
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    new_text: str,
    old_text: str,
    dry_run: bool,
    min_similarity: float,
    min_matches: int,
    consensus_threshold: float,
    same_merchant_boost: float,
) -> dict[str, Any]:
    """Re-evaluate every existing label on this word and write updates."""
    out = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "line_id": line_id,
        "word_id": word_id,
        "old_text": old_text,
        "new_text": new_text,
        "labels_examined": 0,
        "labels_updated": 0,
        "updates": [],
    }

    try:
        # The DynamoClient exposes word-label lookup via receipt details.
        # Pull just this receipt's labels and filter to this word.
        details = dynamo.get_receipt_details(image_id, receipt_id)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Skip %s/%d/%d/%d: %s", image_id, receipt_id, line_id, word_id, exc)
        out["error"] = str(exc)
        return out

    word_labels = [
        lbl
        for lbl in (details.receipt_word_labels or [])
        if lbl.line_id == line_id and lbl.word_id == word_id
    ]

    for label in word_labels:
        out["labels_examined"] += 1
        decision = _evaluate_label(
            words_collection,
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            label=label.label,
            min_similarity=min_similarity,
            min_matches=min_matches,
            consensus_threshold=consensus_threshold,
            same_merchant_boost=same_merchant_boost,
        )
        if decision is None:
            continue
        new_status = decision["status"]
        if new_status == label.validation_status:
            continue

        out["updates"].append({
            "label": label.label,
            "old_status": label.validation_status,
            "new_status": new_status,
            "reason": decision["reason"],
            "confidence": decision["confidence"],
        })

        if dry_run:
            continue

        label.validation_status = new_status
        label.label_proposed_by = "label-refresh-on-text-change"
        try:
            dynamo.update_receipt_word_label(label)
            out["labels_updated"] += 1
        except Exception as exc:  # pylint: disable=broad-except
            logger.error(
                "Failed to update label %s on %s/%d/%d/%d: %s",
                label.label,
                image_id,
                receipt_id,
                line_id,
                word_id,
                exc,
            )

    return out


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry. Processes a batch of DynamoDB stream records."""
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    min_similarity = float(os.environ.get("MIN_SIMILARITY", "0.80"))
    min_matches = int(os.environ.get("MIN_MATCHES", "3"))
    consensus_threshold = float(os.environ.get("CONSENSUS_THRESHOLD", "0.80"))
    same_merchant_boost = float(os.environ.get("SAME_MERCHANT_BOOST", "0.10"))

    dynamo, _chroma, words_collection = _get_clients()

    processed: list[dict[str, Any]] = []
    skipped = 0

    for record in event.get("Records", []):
        change = _extract_word_change(record)
        if change is None:
            skipped += 1
            continue

        result = _process_word(
            dynamo,
            words_collection,
            dry_run=dry_run,
            min_similarity=min_similarity,
            min_matches=min_matches,
            consensus_threshold=consensus_threshold,
            same_merchant_boost=same_merchant_boost,
            **change,
        )
        processed.append(result)

    total_updates = sum(r.get("labels_updated", 0) for r in processed)
    total_proposed = sum(len(r.get("updates", [])) for r in processed)
    response = {
        "processed": len(processed),
        "skipped": skipped,
        "labels_proposed": total_proposed,
        "labels_updated": total_updates,
        "dry_run": dry_run,
    }
    logger.info("label_refresh_summary: %s", json.dumps(response))
    if processed and total_proposed:
        # Surface details for the first few updates so CloudWatch logs are useful
        for r in processed[:10]:
            if r.get("updates"):
                logger.info(
                    "word=%s/%d/%d/%d text='%s'→'%s' updates=%s",
                    r["image_id"],
                    r["receipt_id"],
                    r["line_id"],
                    r["word_id"],
                    r["old_text"],
                    r["new_text"],
                    r["updates"],
                )
    return response
