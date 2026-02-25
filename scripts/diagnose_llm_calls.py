#!/usr/bin/env python3
"""Diagnose LLM calls in the label evaluator and find reduction opportunities.

Downloads execution results from S3, identifies all LLM-routed decisions,
downloads the latest ChromaDB snapshot, then replays consensus lookups
locally to classify each LLM call by failure mode and project reduction
potential.

Usage:
    # Analyze with latest snapshot (auto-downloaded from S3)
    python scripts/diagnose_llm_calls.py \\
        --execution-id bba5341f-8d9a-4df7-801b-491cda8c558a

    # Use a pre-downloaded snapshot
    python scripts/diagnose_llm_calls.py \\
        --execution-id bba5341f-8d9a-4df7-801b-491cda8c558a \\
        --chroma-dir /tmp/chroma_latest_words

Requires:
    pip install boto3 chromadb receipt_chroma
"""

import argparse
import json
import logging
import os
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import boto3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
RESULTS_BUCKET = "label-evaluator-dev-batch-bucket-a82b944"
CHROMADB_BUCKET = "chromadb-dev-shared-buckets-vectors-c239843"

# Reasoning patterns that indicate NON-LLM resolution
AUTO_RESOLVE_INDICATORS = [
    "Tier-1 pattern match",
    "Google Places match",
    "format pattern match",
]
CHROMA_RESOLVE_INDICATORS = [
    "Auto-decided from Chroma consensus",
]
CHROMA_FALLBACK_INDICATOR = "ChromaDB fallback also inconclusive"

# ChromaDB consensus parameters (must match chroma_helpers.py)
N_QUERY = 30
MIN_SIMILARITY = 0.70
DISCOVERY_TOP_K = 10
CONSENSUS_TOP_K = 5
THRESHOLD = 0.60
MIN_EVIDENCE = 2

UNLABELED_SENTINELS = {"", "O", "NONE", "NONE (UNLABELED)", "UNLABELED"}


# ── Data classes ───────────────────────────────────────────────────────────
@dataclass
class LLMWord:
    """A word that was routed to LLM instead of being resolved by ChromaDB."""

    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    word_text: str
    current_label: str
    decision: str
    reasoning: str
    suggested_label: str | None
    confidence: str
    merchant_name: str


@dataclass
class DiagnosticResult:
    """Why ChromaDB didn't resolve a word."""

    word: LLMWord
    failure_mode: str  # no_embedding, no_neighbors, no_label_aware, weak_consensus, etc.
    details: dict = field(default_factory=dict)


# ── Helpers ────────────────────────────────────────────────────────────────
def build_word_chroma_id(
    image_id: str, receipt_id: int, line_id: int, word_id: int
) -> str:
    return (
        f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"
        f"#LINE#{line_id:05d}#WORD#{word_id:05d}"
    )


def classify_decision(reasoning: str) -> str:
    """Classify a decision by its resolution source."""
    for indicator in AUTO_RESOLVE_INDICATORS:
        if indicator in reasoning:
            return "auto_resolve"
    for indicator in CHROMA_RESOLVE_INDICATORS:
        if indicator in reasoning:
            return "chroma_resolve"
    if CHROMA_FALLBACK_INDICATOR in reasoning:
        return "llm_with_chroma_fallback_fail"
    return "llm"


def _result_chroma_id_for_metadata(metadata: dict) -> str:
    """Reconstruct a ChromaDB ID from result metadata."""
    image_id = metadata.get("image_id", "")
    receipt_id = int(metadata.get("receipt_id", 0))
    line_id = int(metadata.get("line_id", 0))
    word_id = int(metadata.get("word_id", 0))
    return build_word_chroma_id(image_id, receipt_id, line_id, word_id)


# ── Step 1: Download execution results ────────────────────────────────────
def download_execution_results(
    execution_id: str, s3_client: Any
) -> list[dict]:
    """Download all per-receipt results from S3."""
    prefix = f"unified/{execution_id}/"
    logger.info("Downloading results from s3://%s/%s", RESULTS_BUCKET, prefix)

    results = []
    paginator = s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=RESULTS_BUCKET, Prefix=prefix)

    keys = []
    for page in pages:
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".json"):
                keys.append(obj["Key"])

    logger.info("Found %d result files", len(keys))

    for key in keys:
        response = s3_client.get_object(Bucket=RESULTS_BUCKET, Key=key)
        data = json.loads(response["Body"].read())
        results.append(data)

    return results


# ── Step 2: Extract LLM-routed decisions ──────────────────────────────────
def extract_llm_words(results: list[dict]) -> list[LLMWord]:
    """Extract all words that were routed to LLM (not auto/chroma resolved)."""
    llm_words = []
    route_counts: Counter = Counter()

    for result in results:
        image_id = result.get("image_id", "")
        receipt_id = result.get("receipt_id", 0)
        merchant_name = result.get("merchant_name", "")

        for dec in result.get("metadata_all_decisions", []):
            issue = dec.get("issue", {})
            review = dec.get("llm_review", {})
            reasoning = review.get("reasoning", "")

            route = classify_decision(reasoning)
            route_counts[route] += 1

            if route in ("llm", "llm_with_chroma_fallback_fail"):
                llm_words.append(
                    LLMWord(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        line_id=issue.get("line_id", 0),
                        word_id=issue.get("word_id", 0),
                        word_text=issue.get("word_text", ""),
                        current_label=issue.get("current_label", ""),
                        decision=review.get("decision", ""),
                        reasoning=reasoning,
                        suggested_label=review.get("suggested_label"),
                        confidence=review.get("confidence", ""),
                        merchant_name=merchant_name,
                    )
                )

    logger.info("Decision routing breakdown:")
    for route, count in route_counts.most_common():
        logger.info("  %-40s %5d", route, count)
    logger.info("Total LLM-routed words: %d", len(llm_words))

    return llm_words


# ── Step 3: Download ChromaDB snapshot ────────────────────────────────────
def download_chroma_snapshot(s3_client: Any, local_dir: str) -> str:
    """Download ChromaDB words snapshot from S3."""
    from receipt_chroma.s3 import download_snapshot_atomic

    logger.info(
        "Downloading ChromaDB words snapshot from s3://%s/words/...",
        CHROMADB_BUCKET,
    )
    result = download_snapshot_atomic(
        bucket=CHROMADB_BUCKET,
        collection="words",
        local_path=local_dir,
        verify_integrity=True,
        s3_client=s3_client,
        parallel=True,
        max_workers=8,
    )

    if result.get("status") != "downloaded":
        raise RuntimeError(f"Snapshot download failed: {result}")

    logger.info(
        "Downloaded snapshot version %s to %s",
        result.get("version_id"),
        local_dir,
    )
    return local_dir


# ── Step 4: Diagnose each LLM word against ChromaDB ──────────────────────
def diagnose_word(
    word: LLMWord,
    words_collection: Any,
) -> DiagnosticResult:
    """Replay ChromaDB lookup for a single word and diagnose failure."""
    chroma_id = build_word_chroma_id(
        word.image_id, word.receipt_id, word.line_id, word.word_id
    )

    # Step A: Check if embedding exists
    try:
        get_result = words_collection.get(
            ids=[chroma_id],
            include=["embeddings", "metadatas"],
        )
    except Exception as exc:
        return DiagnosticResult(
            word=word,
            failure_mode="get_error",
            details={"error": str(exc)},
        )

    embeddings = get_result.get("embeddings")
    if embeddings is None or len(embeddings) == 0:
        return DiagnosticResult(
            word=word,
            failure_mode="no_embedding",
            details={"chroma_id": chroma_id},
        )

    embedding = embeddings[0]
    if hasattr(embedding, "tolist"):
        embedding = embedding.tolist()
    if not isinstance(embedding, list) or len(embedding) == 0:
        return DiagnosticResult(
            word=word,
            failure_mode="invalid_embedding",
            details={"chroma_id": chroma_id, "type": type(embedding).__name__},
        )

    # Get the word's own metadata for reference
    own_meta = {}
    metas = get_result.get("metadatas", [])
    if metas:
        own_meta = metas[0] if isinstance(metas[0], dict) else {}

    # Step B: Query nearest neighbors
    try:
        query_result = words_collection.query(
            query_embeddings=[embedding],
            n_results=N_QUERY,
            include=["metadatas", "distances"],
        )
    except Exception as exc:
        return DiagnosticResult(
            word=word,
            failure_mode="query_error",
            details={"error": str(exc)},
        )

    metadatas = query_result.get("metadatas", [[]])[0]
    distances = query_result.get("distances", [[]])[0]

    # Step C: Filter neighbors above similarity threshold
    neighbors: list[dict] = []
    for meta, dist in zip(metadatas, distances):
        if not isinstance(meta, dict):
            continue
        result_id = _result_chroma_id_for_metadata(meta)
        if result_id == chroma_id:
            continue
        try:
            similarity = max(0.0, 1.0 - (float(dist) / 2.0))
        except (TypeError, ValueError):
            continue

        neighbors.append({
            "similarity": similarity,
            "text": meta.get("text", ""),
            "valid_labels": meta.get("valid_labels_array", []) or [],
            "invalid_labels": meta.get("invalid_labels_array", []) or [],
            "merchant_name": meta.get("merchant_name", ""),
            "above_threshold": similarity >= MIN_SIMILARITY,
        })

    total_neighbors = len(neighbors)
    above_threshold = [n for n in neighbors if n["above_threshold"]]

    if not above_threshold:
        return DiagnosticResult(
            word=word,
            failure_mode="no_neighbors_above_threshold",
            details={
                "chroma_id": chroma_id,
                "total_neighbors": total_neighbors,
                "max_similarity": max(
                    (n["similarity"] for n in neighbors), default=0
                ),
                "top_5_neighbors": [
                    {
                        "text": n["text"],
                        "sim": round(n["similarity"], 4),
                        "valid": n["valid_labels"],
                        "merchant": n["merchant_name"],
                    }
                    for n in neighbors[:5]
                ],
                "own_valid_labels": own_meta.get("valid_labels_array", []),
            },
        )

    # Determine if word is unlabeled or labeled
    normalized_label = (
        word.current_label.strip().upper() if word.current_label else ""
    )
    is_unlabeled = normalized_label in UNLABELED_SENTINELS

    if is_unlabeled:
        return _diagnose_unlabeled(word, above_threshold, chroma_id, own_meta)
    else:
        return _diagnose_labeled(
            word, above_threshold, chroma_id, normalized_label, own_meta
        )


def _diagnose_unlabeled(
    word: LLMWord,
    neighbors: list[dict],
    chroma_id: str,
    own_meta: dict,
) -> DiagnosticResult:
    """Diagnose why an unlabeled word wasn't resolved by discovery+consensus."""

    # Phase 1: Discover candidate label from top-K neighbors
    label_counts: Counter = Counter()
    for n in neighbors[:DISCOVERY_TOP_K]:
        for label in n["valid_labels"]:
            label_str = str(label).strip().upper()
            if label_str not in UNLABELED_SENTINELS:
                label_counts[label_str] = label_counts.get(label_str, 0) + 1

    if not label_counts:
        return DiagnosticResult(
            word=word,
            failure_mode="no_valid_labels_in_neighbors",
            details={
                "chroma_id": chroma_id,
                "num_neighbors_above_threshold": len(neighbors),
                "top_5_neighbors": [
                    {
                        "text": n["text"],
                        "sim": round(n["similarity"], 4),
                        "valid": n["valid_labels"],
                        "invalid": n["invalid_labels"],
                        "merchant": n["merchant_name"],
                    }
                    for n in neighbors[:5]
                ],
                "own_valid_labels": own_meta.get("valid_labels_array", []),
            },
        )

    candidate = label_counts.most_common(1)[0]
    candidate_label, candidate_count = candidate

    if candidate_count < 2:
        return DiagnosticResult(
            word=word,
            failure_mode="candidate_label_too_rare",
            details={
                "chroma_id": chroma_id,
                "candidate_label": candidate_label,
                "candidate_count": candidate_count,
                "all_label_counts": dict(label_counts.most_common()),
                "top_5_neighbors": [
                    {
                        "text": n["text"],
                        "sim": round(n["similarity"], 4),
                        "valid": n["valid_labels"],
                    }
                    for n in neighbors[:5]
                ],
            },
        )

    # Phase 2: Compute consensus for candidate
    label_aware = []
    for n in neighbors:
        has_valid = candidate_label in n["valid_labels"]
        has_invalid = candidate_label in n["invalid_labels"]
        if not has_valid and not has_invalid:
            continue
        label_aware.append({
            "similarity": n["similarity"],
            "has_valid": has_valid,
            "has_invalid": has_invalid,
            "text": n["text"],
            "merchant": n["merchant_name"],
        })

    if not label_aware:
        return DiagnosticResult(
            word=word,
            failure_mode="no_label_aware_neighbors",
            details={
                "chroma_id": chroma_id,
                "candidate_label": candidate_label,
                "candidate_count": candidate_count,
                "num_neighbors_above_threshold": len(neighbors),
            },
        )

    top = label_aware[:CONSENSUS_TOP_K]
    pos = sum(1 for n in top if n["has_valid"])
    neg = sum(1 for n in top if n["has_invalid"])
    total = pos + neg

    if total == 0:
        return DiagnosticResult(
            word=word,
            failure_mode="zero_label_aware_top_k",
            details={
                "chroma_id": chroma_id,
                "candidate_label": candidate_label,
                "label_aware_total": len(label_aware),
            },
        )

    consensus = (pos - neg) / total
    evidence_count = pos + neg

    if evidence_count < MIN_EVIDENCE:
        return DiagnosticResult(
            word=word,
            failure_mode="insufficient_evidence",
            details={
                "chroma_id": chroma_id,
                "candidate_label": candidate_label,
                "consensus": round(consensus, 4),
                "pos": pos,
                "neg": neg,
                "evidence_count": evidence_count,
                "min_required": MIN_EVIDENCE,
                "top_label_aware": top,
            },
        )

    if abs(consensus) < THRESHOLD:
        return DiagnosticResult(
            word=word,
            failure_mode="weak_consensus",
            details={
                "chroma_id": chroma_id,
                "candidate_label": candidate_label,
                "consensus": round(consensus, 4),
                "threshold": THRESHOLD,
                "pos": pos,
                "neg": neg,
                "top_label_aware": top,
            },
        )

    # Should have been resolved! Something else went wrong
    return DiagnosticResult(
        word=word,
        failure_mode="should_have_resolved",
        details={
            "chroma_id": chroma_id,
            "candidate_label": candidate_label,
            "consensus": round(consensus, 4),
            "pos": pos,
            "neg": neg,
            "evidence_count": evidence_count,
            "top_label_aware": top,
        },
    )


def _diagnose_labeled(
    word: LLMWord,
    neighbors: list[dict],
    chroma_id: str,
    target_label: str,
    own_meta: dict,
) -> DiagnosticResult:
    """Diagnose why a labeled word wasn't resolved by consensus."""

    # Find label-aware neighbors for the target label
    label_aware = []
    for n in neighbors:
        has_valid = target_label in n["valid_labels"]
        has_invalid = target_label in n["invalid_labels"]
        if not has_valid and not has_invalid:
            continue
        label_aware.append({
            "similarity": n["similarity"],
            "has_valid": has_valid,
            "has_invalid": has_invalid,
            "text": n["text"],
            "merchant": n["merchant_name"],
        })

    if not label_aware:
        return DiagnosticResult(
            word=word,
            failure_mode="no_label_aware_neighbors",
            details={
                "chroma_id": chroma_id,
                "target_label": target_label,
                "num_neighbors_above_threshold": len(neighbors),
                "top_5_neighbors": [
                    {
                        "text": n["text"],
                        "sim": round(n["similarity"], 4),
                        "valid": n["valid_labels"],
                        "invalid": n["invalid_labels"],
                        "merchant": n["merchant_name"],
                    }
                    for n in neighbors[:5]
                ],
                "own_valid_labels": own_meta.get("valid_labels_array", []),
            },
        )

    top = label_aware[:CONSENSUS_TOP_K]
    pos = sum(1 for n in top if n["has_valid"])
    neg = sum(1 for n in top if n["has_invalid"])
    total = pos + neg

    if total == 0:
        return DiagnosticResult(
            word=word,
            failure_mode="zero_label_aware_top_k",
            details={
                "chroma_id": chroma_id,
                "target_label": target_label,
            },
        )

    consensus = (pos - neg) / total
    evidence_count = pos + neg

    if evidence_count < MIN_EVIDENCE:
        return DiagnosticResult(
            word=word,
            failure_mode="insufficient_evidence",
            details={
                "chroma_id": chroma_id,
                "target_label": target_label,
                "consensus": round(consensus, 4),
                "pos": pos,
                "neg": neg,
                "evidence_count": evidence_count,
                "min_required": MIN_EVIDENCE,
                "top_label_aware": top,
            },
        )

    if abs(consensus) < THRESHOLD:
        return DiagnosticResult(
            word=word,
            failure_mode="weak_consensus",
            details={
                "chroma_id": chroma_id,
                "target_label": target_label,
                "consensus": round(consensus, 4),
                "threshold": THRESHOLD,
                "pos": pos,
                "neg": neg,
                "top_label_aware": top,
            },
        )

    # Should have been resolved!
    return DiagnosticResult(
        word=word,
        failure_mode="should_have_resolved",
        details={
            "chroma_id": chroma_id,
            "target_label": target_label,
            "consensus": round(consensus, 4),
            "pos": pos,
            "neg": neg,
            "evidence_count": evidence_count,
            "top_label_aware": top,
        },
    )


# ── Step 5: Report ────────────────────────────────────────────────────────
def print_report(diagnostics: list[DiagnosticResult]) -> None:
    """Print a summary report of diagnostic results."""
    total = len(diagnostics)

    # Group by failure mode
    by_mode: dict[str, list[DiagnosticResult]] = {}
    for d in diagnostics:
        by_mode.setdefault(d.failure_mode, []).append(d)

    print("\n" + "=" * 80)
    print(f"CHROMADB DIAGNOSTIC REPORT — {total} LLM-routed words analyzed")
    print("=" * 80)

    print("\n## Failure Mode Summary\n")
    print(f"{'Failure Mode':<40} {'Count':>6} {'%':>6}")
    print("-" * 54)
    for mode, items in sorted(by_mode.items(), key=lambda x: -len(x[1])):
        pct = 100 * len(items) / total if total else 0
        print(f"{mode:<40} {len(items):>6} {pct:>5.1f}%")

    # Group by label + failure mode
    print("\n## Failure Mode by Label\n")
    by_label: dict[str, Counter] = {}
    for d in diagnostics:
        label = d.word.current_label or "UNLABELED"
        by_label.setdefault(label, Counter())[d.failure_mode] += 1

    for label in sorted(by_label, key=lambda l: -sum(by_label[l].values())):
        total_label = sum(by_label[label].values())
        print(f"\n  {label} ({total_label} words):")
        for mode, count in by_label[label].most_common():
            print(f"    {mode:<38} {count:>5}")

    # Print sample cases for each failure mode
    print("\n## Sample Cases by Failure Mode\n")
    for mode, items in sorted(by_mode.items(), key=lambda x: -len(x[1])):
        print(f"\n### {mode} ({len(items)} words)\n")
        for d in items[:5]:
            w = d.word
            print(
                f"  Word: '{w.word_text}' | Label: {w.current_label or 'UNLABELED'}"
                f" | Merchant: {w.merchant_name}"
            )
            print(f"  LLM decision: {w.decision} | Reasoning: {w.reasoning[:120]}")
            # Print key diagnostic details
            details = d.details
            if "max_similarity" in details:
                print(f"  Max similarity: {details['max_similarity']:.4f}")
            if "consensus" in details:
                print(
                    f"  Consensus: {details['consensus']:.4f} "
                    f"(pos={details.get('pos')}, neg={details.get('neg')})"
                )
            if "candidate_label" in details:
                print(f"  Candidate label: {details['candidate_label']}")
            if "top_5_neighbors" in details:
                print("  Top neighbors:")
                for nb in details["top_5_neighbors"][:3]:
                    print(
                        f"    '{nb.get('text', '')}' sim={nb.get('sim', 0):.4f}"
                        f" valid={nb.get('valid', [])} merchant={nb.get('merchant', '')}"
                    )
            if "top_label_aware" in details:
                print("  Label-aware neighbors:")
                for nb in details["top_label_aware"][:3]:
                    print(
                        f"    '{nb.get('text', '')}' sim={nb.get('similarity', 0):.4f}"
                        f" valid={nb.get('has_valid')} invalid={nb.get('has_invalid')}"
                    )
            print()

    # Special focus: ADDRESS_LINE words
    address_words = [
        d for d in diagnostics if d.word.current_label == "ADDRESS_LINE"
    ]
    if address_words:
        print("\n## ADDRESS_LINE Deep Dive\n")
        addr_by_mode: Counter = Counter(d.failure_mode for d in address_words)
        for mode, count in addr_by_mode.most_common():
            print(f"  {mode:<40} {count:>5}")
        print()
        for d in address_words[:10]:
            w = d.word
            print(
                f"  '{w.word_text}' (L{w.line_id}:W{w.word_id}) "
                f"@ {w.merchant_name}"
            )
            print(f"    Mode: {d.failure_mode}")
            if "consensus" in d.details:
                print(
                    f"    Consensus: {d.details['consensus']:.4f} "
                    f"pos={d.details.get('pos')} neg={d.details.get('neg')}"
                )
            if "candidate_label" in d.details:
                print(f"    Candidate: {d.details['candidate_label']}")
            if "top_5_neighbors" in d.details:
                for nb in d.details["top_5_neighbors"][:2]:
                    print(
                        f"    Neighbor: '{nb.get('text', '')}' "
                        f"sim={nb.get('sim', 0):.4f} valid={nb.get('valid', [])}"
                    )
            print()


def print_reduction_opportunities(
    diagnostics: list[DiagnosticResult],
    results: list[dict],
) -> None:
    """Analyze and print actionable opportunities to reduce LLM calls."""
    total = len(diagnostics)
    if total == 0:
        return

    by_mode: dict[str, list[DiagnosticResult]] = {}
    for d in diagnostics:
        by_mode.setdefault(d.failure_mode, []).append(d)

    # Count overall routing from all results
    route_counts: Counter = Counter()
    total_decisions = 0
    for result in results:
        for dec in result.get("metadata_all_decisions", []):
            reasoning = dec.get("llm_review", {}).get("reasoning", "")
            total_decisions += 1
            route = classify_decision(reasoning)
            route_counts[route] += 1

    print("\n" + "=" * 80)
    print("LLM CALL REDUCTION OPPORTUNITIES")
    print("=" * 80)

    # ── Overall routing breakdown ──
    print("\n## Decision Routing Breakdown\n")
    print(f"  Total decisions: {total_decisions}")
    for route in ["auto_resolve", "chroma_resolve", "llm",
                   "llm_with_chroma_fallback_fail"]:
        count = route_counts.get(route, 0)
        pct = 100 * count / total_decisions if total_decisions else 0
        print(f"  {route:<40} {count:>6} ({pct:.1f}%)")

    # ── Failure mode summary with reduction potential ──
    should = by_mode.get("should_have_resolved", [])
    weak = by_mode.get("weak_consensus", [])
    insuf = by_mode.get("insufficient_evidence", [])
    no_la = by_mode.get("no_label_aware_neighbors", [])
    no_thresh = by_mode.get("no_neighbors_above_threshold", [])
    no_embed = by_mode.get("no_embedding", [])
    no_valid = by_mode.get("no_valid_labels_in_neighbors", [])
    too_rare = by_mode.get("candidate_label_too_rare", [])

    # ── Tier 1: Snapshot freshness ──
    print(f"\n## Tier 1 — Snapshot Freshness ({len(should)} words, "
          f"{100*len(should)/total:.0f}%)\n")
    print("  These words have strong ChromaDB consensus NOW but didn't at")
    print("  Lambda runtime because the Lambda used an older snapshot.")
    print("  As the compaction pipeline enriches more words, successive")
    print("  evaluator runs will resolve these automatically.\n")

    # Consensus strength
    perfect = sum(1 for d in should
                  if abs(d.details.get("consensus", 0)) == 1.0)
    print(f"  Unanimous consensus (|c|=1.0): {perfect}/{len(should)} "
          f"({100*perfect/len(should):.0f}%)")

    # Top word texts
    text_counts: Counter = Counter(d.word.word_text for d in should)
    print(f"  Most common words (appearing across many receipts):")
    for text, count in text_counts.most_common(10):
        labels = Counter(d.word.current_label or "UNLABELED"
                         for d in should if d.word.word_text == text)
        top_label = labels.most_common(1)[0][0]
        print(f"    '{text}' x{count} ({top_label})")

    # ── Tier 2: Weak consensus analysis ──
    print(f"\n## Tier 2 — Weak Consensus ({len(weak)} words, "
          f"{100*len(weak)/total:.0f}%)\n")
    print("  Neighbors disagree — consensus below 0.60 threshold.")
    print("  These are genuinely ambiguous and REQUIRE LLM judgment.\n")

    # LLM agreement with consensus direction
    agree = disagree = 0
    for d in weak:
        cons = d.details.get("consensus", 0)
        llm_dec = d.word.decision
        if cons > 0 and llm_dec == "VALID":
            agree += 1
        elif cons < 0 and llm_dec == "INVALID":
            agree += 1
        elif cons > 0 and llm_dec == "INVALID":
            disagree += 1
        elif cons < 0 and llm_dec == "VALID":
            disagree += 1
    if agree + disagree > 0:
        print(f"  LLM agrees with consensus direction: "
              f"{agree}/{agree + disagree} ({100*agree/(agree+disagree):.0f}%)")
        print(f"  → Lowering threshold would produce "
              f"{100 - 100*agree/(agree+disagree):.0f}% error rate — not viable")

    # Per-label agreement
    label_stats: dict[str, tuple[int, int]] = {}
    for d in weak:
        label = d.word.current_label or "UNLABELED"
        cons = d.details.get("consensus", 0)
        llm_dec = d.word.decision
        if cons != 0 and llm_dec in ("VALID", "INVALID"):
            a, t = label_stats.get(label, (0, 0))
            t += 1
            if (cons > 0 and llm_dec == "VALID") or \
               (cons < 0 and llm_dec == "INVALID"):
                a += 1
            label_stats[label] = (a, t)
    print(f"\n  Per-label LLM agreement with consensus direction:")
    for label in sorted(label_stats,
                        key=lambda l: -label_stats[l][1]):
        a, t = label_stats[label]
        total_label = sum(1 for d in weak
                          if (d.word.current_label or "UNLABELED") == label)
        print(f"    {label:<25} {total_label:>4} words, "
              f"LLM agrees: {100*a/t:.0f}%")

    # ── Tier 3: Coverage gaps ──
    coverage_gap = len(no_la) + len(insuf) + len(no_valid) + len(too_rare)
    print(f"\n## Tier 3 — Label Coverage Gaps ({coverage_gap} words, "
          f"{100*coverage_gap/total:.0f}%)\n")
    print("  Neighbors exist but lack label data to form consensus.")
    print("  These resolve as more receipts get evaluated and the")
    print("  compaction pipeline populates valid/invalid label arrays.\n")
    print(f"  no_label_aware_neighbors:      {len(no_la):>5}")
    print(f"  insufficient_evidence:         {len(insuf):>5}")
    print(f"  no_valid_labels_in_neighbors:  {len(no_valid):>5}")
    print(f"  candidate_label_too_rare:      {len(too_rare):>5}")

    # ── Tier 4: Embedding gaps ──
    embed_gap = len(no_thresh) + len(no_embed)
    print(f"\n## Tier 4 — Embedding Gaps ({embed_gap} words, "
          f"{100*embed_gap/total:.0f}%)\n")
    print("  Words missing from ChromaDB or too dissimilar to any neighbor.")
    print(f"  no_neighbors_above_threshold:  {len(no_thresh):>5}")
    print(f"  no_embedding:                  {len(no_embed):>5}")

    if no_thresh:
        max_sims = [d.details.get("max_similarity", 0) for d in no_thresh]
        near_miss = sum(1 for s in max_sims if s >= 0.65)
        print(f"  Near-miss (max_sim >= 0.65):   {near_miss:>5}")

    # ── Merchant hotspots ──
    print(f"\n## Merchant Hotspots\n")
    merchant_counts: Counter = Counter(d.word.merchant_name for d in diagnostics)
    for merchant, count in merchant_counts.most_common(10):
        resolvable = sum(1 for d in diagnostics
                         if d.word.merchant_name == merchant
                         and d.failure_mode == "should_have_resolved")
        pct_resolvable = 100 * resolvable / count if count else 0
        print(f"  {merchant:<45} {count:>4} LLM calls "
              f"({resolvable} resolvable, {pct_resolvable:.0f}%)")

    # ── Projected trajectory ──
    print(f"\n## Projected Trajectory\n")
    chroma_now = route_counts.get("chroma_resolve", 0)
    llm_now = route_counts.get("llm", 0) + \
        route_counts.get("llm_with_chroma_fallback_fail", 0)
    print(f"  Current ChromaDB resolutions:  {chroma_now}")
    print(f"  Current LLM calls:             {llm_now}")
    print(f"  should_have_resolved:          {len(should)}")
    projected = llm_now - len(should)
    print(f"  Projected next-run LLM calls:  ~{projected} "
          f"(if snapshot is fresh)")
    print(f"  Projected reduction:           "
          f"{100*len(should)/llm_now:.0f}%")
    irreducible = len(weak) + len(no_thresh) + len(no_embed)
    print(f"  Irreducible floor:             ~{irreducible} words")
    print(f"    weak_consensus:              {len(weak)}")
    print(f"    no_neighbors_above_threshold:{len(no_thresh)}")
    print(f"    no_embedding:                {len(no_embed)}")
    convergence = coverage_gap
    print(f"  Will converge with more runs:  ~{convergence} words")
    print(f"    (coverage gaps that shrink as more labels accumulate)")


def save_diagnostics_json(
    diagnostics: list[DiagnosticResult], output_path: str
) -> None:
    """Save full diagnostic results to JSON."""
    records = []
    for d in diagnostics:
        records.append({
            "word_text": d.word.word_text,
            "current_label": d.word.current_label,
            "image_id": d.word.image_id,
            "receipt_id": d.word.receipt_id,
            "line_id": d.word.line_id,
            "word_id": d.word.word_id,
            "merchant_name": d.word.merchant_name,
            "llm_decision": d.word.decision,
            "llm_reasoning": d.word.reasoning,
            "llm_suggested_label": d.word.suggested_label,
            "failure_mode": d.failure_mode,
            "details": d.details,
        })

    with open(output_path, "w") as f:
        json.dump(records, f, indent=2, default=str)
    logger.info("Saved %d diagnostic records to %s", len(records), output_path)


# ── Main ──────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose LLM calls not resolved by ChromaDB"
    )
    parser.add_argument(
        "--execution-id",
        default="bba5341f-8d9a-4df7-801b-491cda8c558a",
        help="Step function execution ID",
    )
    parser.add_argument(
        "--output",
        default="scripts/llm_diagnostics.json",
        help="Output JSON path",
    )
    parser.add_argument(
        "--chroma-dir",
        default=None,
        help="Local ChromaDB snapshot directory (skip S3 download)",
    )
    parser.add_argument(
        "--results-dir",
        default=None,
        help="Local directory with pre-downloaded result JSONs (skip S3)",
    )
    args = parser.parse_args()

    s3 = boto3.client("s3")

    # Step 1: Download execution results
    if args.results_dir:
        logger.info("Loading results from local directory: %s", args.results_dir)
        results = []
        for p in Path(args.results_dir).glob("*.json"):
            with open(p) as f:
                results.append(json.load(f))
    else:
        results = download_execution_results(args.execution_id, s3)

    if not results:
        logger.error("No results found!")
        sys.exit(1)

    # Step 2: Extract LLM-routed words
    llm_words = extract_llm_words(results)
    if not llm_words:
        logger.info("No LLM-routed words found. All resolved by ChromaDB!")
        sys.exit(0)

    # Step 3: Download ChromaDB snapshot
    if args.chroma_dir:
        chroma_dir = args.chroma_dir
        logger.info("Using existing ChromaDB snapshot: %s", chroma_dir)
    else:
        chroma_dir = os.path.join(tempfile.gettempdir(), "chroma_diagnosis_words")
        os.makedirs(chroma_dir, exist_ok=True)
        download_chroma_snapshot(s3, chroma_dir)

    # Step 4: Open ChromaDB and diagnose
    import chromadb

    logger.info("Opening ChromaDB at %s", chroma_dir)
    client = chromadb.PersistentClient(path=chroma_dir)
    words_collection = client.get_collection("words")
    total_vectors = words_collection.count()
    logger.info("ChromaDB words collection: %d vectors", total_vectors)

    diagnostics: list[DiagnosticResult] = []
    for i, word in enumerate(llm_words):
        if (i + 1) % 50 == 0:
            logger.info("Diagnosing word %d/%d...", i + 1, len(llm_words))
        result = diagnose_word(word, words_collection)
        diagnostics.append(result)

    # Step 5: Report
    print_report(diagnostics)
    print_reduction_opportunities(diagnostics, results)
    save_diagnostics_json(diagnostics, args.output)


if __name__ == "__main__":
    main()
