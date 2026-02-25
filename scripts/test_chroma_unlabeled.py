#!/usr/bin/env python3
"""
Test ChromaDB consensus for unlabeled words.

Picks unlabeled words from the latest execution's S3 results and queries
both the `words` and `lines` ChromaDB collections to see if nearest-neighbor
consensus could assign labels without an LLM.

Usage:
    /usr/local/bin/python3.12 scripts/test_chroma_unlabeled.py --env dev \
        --results-dir /tmp/unified_results_tier1

Requires Python 3.12 (chromadb pydantic v1 broken on 3.14).
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter

script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data._pulumi import load_secrets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_chroma_client(secrets: dict):
    import chromadb

    return chromadb.CloudClient(
        api_key=secrets["portfolio:CHROMA_CLOUD_API_KEY"],
        tenant=secrets.get("portfolio:CHROMA_CLOUD_TENANT", "default"),
        database=secrets.get("portfolio:CHROMA_CLOUD_DATABASE", "default"),
    )


def load_unlabeled_words(results_dir: str) -> list[dict]:
    """Load unlabeled words that went to LLM from S3 result files."""
    words = []
    for fname in os.listdir(results_dir):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(results_dir, fname)) as f:
            data = json.load(f)

        for d in data.get("metadata_all_decisions", []):
            issue = d.get("issue", {})
            review = d.get("llm_review", {})
            reasoning = review.get("reasoning", "")
            current_label = issue.get("current_label")

            if current_label or "Tier-1" in reasoning:
                continue

            words.append({
                "image_id": d.get("image_id", ""),
                "receipt_id": d.get("receipt_id", 0),
                "line_id": issue.get("line_id", 0),
                "word_id": issue.get("word_id", 0),
                "text": issue.get("word_text", ""),
                "llm_decision": review.get("decision", ""),
                "llm_suggested": review.get("suggested_label", ""),
            })
    return words


def build_word_chroma_id(image_id: str, receipt_id: int, line_id: int, word_id: int) -> str:
    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}"


def build_line_chroma_id(image_id: str, receipt_id: int, line_id: int) -> str:
    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"


def query_words_collection(client, collection, word: dict, n_results: int = 10):
    """Query words collection for nearest neighbors and extract label votes."""
    chroma_id = build_word_chroma_id(
        word["image_id"], word["receipt_id"], word["line_id"], word["word_id"]
    )

    # Get the query word's embedding
    try:
        result = collection.get(ids=[chroma_id], include=["embeddings", "metadatas"])
    except Exception as e:
        return {"error": f"get failed: {e}"}

    embeddings = result.get("embeddings")
    if embeddings is None or (hasattr(embeddings, "__len__") and len(embeddings) == 0):
        return {"error": "no embedding found"}

    embedding = embeddings[0]
    if hasattr(embedding, "tolist"):
        embedding = embedding.tolist()

    # Query nearest neighbors
    try:
        results = collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            include=["metadatas", "distances"],
        )
    except Exception as e:
        return {"error": f"query failed: {e}"}

    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    # Count label votes from neighbors
    label_votes = Counter()
    neighbors = []
    for meta, dist in zip(metadatas, distances):
        if not isinstance(meta, dict):
            continue
        # Skip self
        neighbor_id = meta.get("chroma_id", meta.get("id", ""))
        if neighbor_id == chroma_id:
            continue

        similarity = max(0.0, 1.0 - (float(dist) / 2.0))
        if similarity < 0.70:
            continue

        v_arr = meta.get("valid_labels_array", []) or []
        if isinstance(v_arr, list):
            for label in v_arr:
                label_votes[label] += 1

        neighbors.append({
            "similarity": round(similarity, 3),
            "text": meta.get("text", ""),
            "valid_labels": v_arr,
            "merchant": meta.get("merchant_name", ""),
        })

    return {
        "chroma_id": chroma_id,
        "found_embedding": True,
        "neighbors": neighbors[:5],
        "label_votes": dict(label_votes.most_common(5)),
        "top_label": label_votes.most_common(1)[0] if label_votes else None,
    }


def query_lines_collection(client, collection, word: dict, n_results: int = 10):
    """Query lines collection for nearest neighbors and extract label votes."""
    chroma_id = build_line_chroma_id(
        word["image_id"], word["receipt_id"], word["line_id"]
    )

    # Get the line's embedding
    try:
        result = collection.get(ids=[chroma_id], include=["embeddings", "metadatas"])
    except Exception as e:
        # Try row-based ID fallback
        try:
            result = collection.get(
                ids=[f"{word['image_id']}_{word['receipt_id']}_row_{word['line_id']}"],
                include=["embeddings", "metadatas"],
            )
        except Exception:
            return {"error": f"get failed: {e}"}

    embeddings = result.get("embeddings")
    if embeddings is None or (hasattr(embeddings, "__len__") and len(embeddings) == 0):
        return {"error": "no embedding found"}

    embedding = embeddings[0]
    if hasattr(embedding, "tolist"):
        embedding = embedding.tolist()

    # Query nearest neighbors
    try:
        results = collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            include=["metadatas", "distances"],
        )
    except Exception as e:
        return {"error": f"query failed: {e}"}

    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    # Lines store boolean label flags: label_PAYMENT_METHOD: True, etc.
    label_votes = Counter()
    neighbors = []
    for meta, dist in zip(metadatas, distances):
        if not isinstance(meta, dict):
            continue
        neighbor_id = meta.get("chroma_id", meta.get("id", ""))
        if neighbor_id == chroma_id:
            continue

        similarity = max(0.0, 1.0 - (float(dist) / 2.0))
        if similarity < 0.70:
            continue

        # Extract labels from boolean flags
        line_labels = []
        for key, val in meta.items():
            if key.startswith("label_") and val is True:
                line_labels.append(key.replace("label_", ""))
                label_votes[key.replace("label_", "")] += 1

        neighbors.append({
            "similarity": round(similarity, 3),
            "text": meta.get("text", ""),
            "labels": line_labels,
            "merchant": meta.get("merchant_name", ""),
        })

    return {
        "chroma_id": chroma_id,
        "found_embedding": True,
        "neighbors": neighbors[:5],
        "label_votes": dict(label_votes.most_common(5)),
        "top_label": label_votes.most_common(1)[0] if label_votes else None,
    }


def main():
    parser = argparse.ArgumentParser(description="Test ChromaDB for unlabeled words")
    parser.add_argument("--env", required=True, choices=["dev", "prod"])
    parser.add_argument("--results-dir", required=True, help="Path to S3 result JSONs")
    parser.add_argument("--sample", type=int, default=50, help="Number of words to test")
    args = parser.parse_args()

    # Load unlabeled words
    logger.info("Loading unlabeled words from %s", args.results_dir)
    all_words = load_unlabeled_words(args.results_dir)
    logger.info("Found %d unlabeled LLM-bound words", len(all_words))

    # Sample: pick the most frequent texts to cover breadth
    text_counts = Counter(w["text"] for w in all_words)
    logger.info("Top texts: %s", text_counts.most_common(10))

    # Pick one representative word per top text, up to sample limit
    seen_texts = set()
    sample = []
    for w in all_words:
        if w["text"] not in seen_texts and len(sample) < args.sample:
            seen_texts.add(w["text"])
            sample.append(w)
    logger.info("Sampled %d unique texts to query", len(sample))

    # Connect to ChromaDB
    logger.info("Loading Pulumi secrets for %s...", args.env)
    secrets = load_secrets(env=args.env)
    logger.info("Connecting to ChromaDB Cloud...")
    client = get_chroma_client(secrets)

    # Get collections
    collections = [c.name for c in client.list_collections()]
    logger.info("Available collections: %s", collections)

    words_coll = client.get_collection("words") if "words" in collections else None
    lines_coll = client.get_collection("lines") if "lines" in collections else None

    if words_coll:
        logger.info("Words collection: %d embeddings", words_coll.count())
    if lines_coll:
        logger.info("Lines collection: %d embeddings", lines_coll.count())

    # Query each sample word against both collections
    words_results = []
    lines_results = []

    for i, w in enumerate(sample):
        logger.info(
            "[%d/%d] Text=%r  LLM=%s->%s",
            i + 1, len(sample), w["text"], w["llm_decision"], w["llm_suggested"],
        )

        if words_coll:
            wr = query_words_collection(client, words_coll, w)
            wr["word"] = w
            words_results.append(wr)
            if "error" not in wr:
                top = wr.get("top_label")
                logger.info(
                    "  WORDS: top_label=%s, votes=%s, neighbors=%d",
                    top, wr.get("label_votes", {}), len(wr.get("neighbors", [])),
                )
            else:
                logger.info("  WORDS: %s", wr["error"])

        if lines_coll:
            lr = query_lines_collection(client, lines_coll, w)
            lr["word"] = w
            lines_results.append(lr)
            if "error" not in lr:
                top = lr.get("top_label")
                logger.info(
                    "  LINES: top_label=%s, votes=%s, neighbors=%d",
                    top, lr.get("label_votes", {}), len(lr.get("neighbors", [])),
                )
            else:
                logger.info("  LINES: %s", lr["error"])

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    for name, results in [("WORDS", words_results), ("LINES", lines_results)]:
        if not results:
            continue
        print(f"\n--- {name} Collection ---")
        found = [r for r in results if "error" not in r]
        errors = [r for r in results if "error" in r]
        has_top = [r for r in found if r.get("top_label")]
        print(f"  Queried: {len(results)}")
        print(f"  Embedding found: {len(found)}")
        print(f"  Embedding missing: {len(errors)}")
        print(f"  Has label consensus: {len(has_top)}")

        # Check accuracy: does ChromaDB top label agree with LLM?
        correct = 0
        wrong = 0
        for r in has_top:
            w = r["word"]
            chroma_label = r["top_label"][0]  # (label, count) tuple
            chroma_count = r["top_label"][1]
            if w["llm_decision"] == "INVALID" and w["llm_suggested"] == chroma_label:
                correct += 1
            elif w["llm_decision"] == "VALID" and chroma_count >= 2:
                # ChromaDB suggests a label but LLM says stay unlabeled
                wrong += 1

        if has_top:
            print(f"  Agrees with LLM INVALID: {correct}")
            print(f"  Disagrees (ChromaDB suggests, LLM says VALID): {wrong}")

        print(f"\n  Per-word detail:")
        print(f"  {'Text':<25} {'LLM':<8} {'Suggested':<18} {'Chroma Top':<18} {'Votes'}")
        print(f"  {'-'*90}")
        for r in found[:30]:
            w = r["word"]
            top = r.get("top_label")
            top_str = f"{top[0]}({top[1]})" if top else "-"
            votes = r.get("label_votes", {})
            votes_str = ", ".join(f"{k}:{v}" for k, v in list(votes.items())[:3])
            print(
                f"  {w['text']:<25} {w['llm_decision']:<8} {w['llm_suggested'] or '-':<18} "
                f"{top_str:<18} {votes_str}"
            )


if __name__ == "__main__":
    main()
