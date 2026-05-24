"""
OCR-outlier prototype: find words at a merchant that look like OCR
misreads of a popular text at the same merchant.

Two signals, both read-only against prod:

1. PER-WORD: singleton PRODUCT_NAME texts that are 1-2 edits away from
   a popular PRODUCT_NAME text at the same merchant. Catches things
   like "ricing-" → "PRICING".

2. PER-PAIR: singleton (left-neighbor, right-neighbor) text pairs that
   are 1-2 edits from a popular pair at the same merchant. Catches
   "RAN MILK" at Sprouts when "RAW MILK" appears 30+ times — the
   suspect word may not even be labeled PRODUCT_NAME (in fact the
   labeler often correctly marks the malformed token as OTHER).

The same `label_PRODUCT_NAME` / `label_status` filters are how
`validate_word_similarity` already navigates the collection (see
scripts/receipt_mcp_server.py:1844).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter, defaultdict
from difflib import get_close_matches
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _load_clients() -> tuple[Any, Any]:
    """Load read-only Chroma + Dynamo clients for the configured env."""
    from receipt_chroma.data.chroma_client import ChromaClient
    from receipt_dynamo.data._pulumi import load_env, load_secrets
    from receipt_dynamo.data.dynamo_client import DynamoClient

    env = os.environ.get("PORTFOLIO_ENV", "prod")
    logger.info("Loading %s Pulumi env...", env)

    config = load_env(env=env)
    secrets = load_secrets(env=env)
    for key, value in secrets.items():
        normalized_key = key.replace("portfolio:", "").lower().replace("-", "_")
        config[normalized_key] = value

    table_name = config["dynamodb_table_name"]
    logger.info("DynamoDB table: %s", table_name)
    dynamo = DynamoClient(table_name=table_name)

    chroma_api_key = config.get("chroma_cloud_api_key")
    if not chroma_api_key:
        raise RuntimeError("Chroma Cloud API key not found in Pulumi config")

    chroma = ChromaClient(
        cloud_api_key=chroma_api_key,
        cloud_tenant=config.get("chroma_cloud_tenant"),
        cloud_database=config.get("chroma_cloud_database"),
        mode="read",
    )
    logger.info("Chroma cloud client created (tenant=%s)", config.get("chroma_cloud_tenant"))

    return dynamo, chroma


def _paginate(
    words_collection: Any,
    *,
    where: dict[str, Any],
    page_size: int = 300,
    max_words: int = 50_000,
    label: str = "words",
) -> list[dict[str, Any]]:
    """
    Page through the words collection with a metadata filter.

    Chroma Cloud caps individual `.get()` calls at 300 items, so we
    offset-paginate until we either hit max_words or exhaust the filter.
    """
    all_metas: list[dict[str, Any]] = []
    offset = 0
    while len(all_metas) < max_words:
        page = words_collection.get(
            where=where,
            limit=page_size,
            offset=offset,
            include=["metadatas"],
        )
        metas = page.get("metadatas") or []
        if not metas:
            break
        all_metas.extend(metas)
        if len(metas) < page_size:
            break
        offset += len(metas)
        logger.info("Paged %d %s so far...", len(all_metas), label)
    return all_metas


def _paginate_product_words(
    words_collection: Any,
    *,
    page_size: int = 300,
    max_words: int = 50_000,
) -> list[dict[str, Any]]:
    """Validated PRODUCT_NAME words — used for the per-word signal."""
    return _paginate(
        words_collection,
        where={
            "$and": [
                {"label_PRODUCT_NAME": True},
                {"label_status": "validated"},
            ]
        },
        page_size=page_size,
        max_words=max_words,
        label="PRODUCT_NAME words",
    )


def _paginate_all_validated_words(
    words_collection: Any,
    *,
    page_size: int = 300,
    max_words: int = 50_000,
) -> list[dict[str, Any]]:
    """
    Every validated word, regardless of label. Used for the per-pair
    signal because the suspect token in OCR errors is often labeled
    OTHER (or unlabeled) — see the "RAN MILK" case at Sprouts.
    """
    return _paginate(
        words_collection,
        where={"label_status": "validated"},
        page_size=page_size,
        max_words=max_words,
        label="validated words",
    )


def find_pair_outliers(
    metas: list[dict[str, Any]],
    *,
    min_popular_count: int,
    cutoff: float,
) -> list[dict[str, Any]]:
    """
    Find rare adjacency pairs at a merchant that are close to a popular
    adjacency pair.

    Word adjacencies in Chroma are recorded asymmetrically — the right
    of word A is not guaranteed to be word B even when B.left == A.text
    (left/right are computed from spatial proximity per-word). So we
    enumerate two pair views:

    * (text, right): the source word IS the left-side of the pair.
    * (left, text): the source word is the right-side; the left-side
      word is identifiable only by image_id (a same-line word with
      text == 'left').

    Both views feed a single pair_counts. For each rare pair we ask:
    is there a popular pair sharing one slot whose other slot is
    edit-close to the rare slot?
    """
    by_merchant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for meta in metas:
        merchant = (meta.get("merchant_name") or "").strip()
        if not merchant:
            continue
        by_merchant[merchant].append(meta)

    candidates: list[dict[str, Any]] = []

    for merchant, words in by_merchant.items():
        # Each pair maps to the source word records that generated it.
        # The same physical adjacency can be recorded from both sides.
        pair_sources: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = (
            defaultdict(list)
        )
        for word in words:
            text = (word.get("text") or "").strip().upper()
            left = (word.get("left") or "").strip().upper()
            right = (word.get("right") or "").strip().upper()
            if not text:
                continue
            if right and right != "<EDGE>":
                pair_sources[(text, right)].append(("self", word))
            if left and left != "<EDGE>":
                pair_sources[(left, text)].append(("right-of", word))

        pair_counts = {p: len(srcs) for p, srcs in pair_sources.items()}
        popular_by_left: dict[str, list[str]] = defaultdict(list)
        popular_by_right: dict[str, list[str]] = defaultdict(list)
        for (l, r), c in pair_counts.items():
            if c >= min_popular_count:
                popular_by_left[l].append(r)
                popular_by_right[r].append(l)

        if not popular_by_left and not popular_by_right:
            continue

        seen: set[tuple[str, str, str, str]] = set()

        for (l, r), sources in pair_sources.items():
            if pair_counts[(l, r)] > 1:
                continue

            # Vary the LEFT slot: any popular (l', r) with l' close to l?
            same_right_options = [
                opt for opt in popular_by_right.get(r, []) if opt != l
            ]
            l_matches = get_close_matches(l, same_right_options, n=1, cutoff=cutoff)
            if l_matches:
                l_prime = l_matches[0]
                key = (merchant, "left", f"{l}|{r}", l_prime)
                if key not in seen:
                    seen.add(key)
                    for role, src in sources:
                        candidates.append({
                            "merchant": merchant,
                            "suspect_text": l if role == "right-of" else (src.get("text") or ""),
                            "context_field": "right",
                            "context_value": r,
                            "suggested_text": l_prime,
                            "suggested_count": pair_counts[(l_prime, r)],
                            "confidence": src.get("confidence"),
                            "image_id": src.get("image_id"),
                            "receipt_id": src.get("receipt_id"),
                            "line_id": src.get("line_id"),
                            "word_id": src.get("word_id"),
                            "source_role": role,
                        })

            # Vary the RIGHT slot: any popular (l, r') with r' close to r?
            same_left_options = [
                opt for opt in popular_by_left.get(l, []) if opt != r
            ]
            r_matches = get_close_matches(r, same_left_options, n=1, cutoff=cutoff)
            if r_matches:
                r_prime = r_matches[0]
                key = (merchant, "right", f"{l}|{r}", r_prime)
                if key not in seen:
                    seen.add(key)
                    for role, src in sources:
                        candidates.append({
                            "merchant": merchant,
                            "suspect_text": r if role == "self" else (src.get("text") or ""),
                            "context_field": "left",
                            "context_value": l,
                            "suggested_text": r_prime,
                            "suggested_count": pair_counts[(l, r_prime)],
                            "confidence": src.get("confidence"),
                            "image_id": src.get("image_id"),
                            "receipt_id": src.get("receipt_id"),
                            "line_id": src.get("line_id"),
                            "word_id": src.get("word_id"),
                            "source_role": role,
                        })
    return candidates


def find_outliers(
    metas: list[dict[str, Any]],
    *,
    min_popular_count: int,
    cutoff: float,
) -> list[dict[str, Any]]:
    """
    For each merchant, find singleton texts that are close to a
    popular text at the same merchant.

    Returns one row per candidate with the merchant, the suspect word
    text, the likely correct text, occurrence count of the correct
    text, and the suspect word's OCR confidence.
    """
    by_merchant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for meta in metas:
        merchant = (meta.get("merchant_name") or "").strip()
        if not merchant:
            continue
        by_merchant[merchant].append(meta)

    candidates: list[dict[str, Any]] = []
    for merchant, words in by_merchant.items():
        text_counts: Counter[str] = Counter()
        for word in words:
            text = (word.get("text") or "").strip().upper()
            if text:
                text_counts[text] += 1

        popular = [t for t, c in text_counts.items() if c >= min_popular_count]
        if not popular:
            continue

        seen_pairs: set[tuple[str, str]] = set()
        for word in words:
            raw_text = (word.get("text") or "").strip()
            text = raw_text.upper()
            if not text or text_counts[text] > 1:
                continue
            matches = get_close_matches(text, popular, n=1, cutoff=cutoff)
            if not matches:
                continue
            suggested = matches[0]
            if suggested == text:
                continue
            pair_key = (text, suggested)
            if pair_key in seen_pairs:
                # Same suspect text already reported for this merchant
                continue
            seen_pairs.add(pair_key)
            candidates.append({
                "merchant": merchant,
                "suspect_text": raw_text,
                "suggested_text": suggested,
                "suggested_count": text_counts[suggested],
                "confidence": word.get("confidence"),
                "image_id": word.get("image_id"),
                "receipt_id": word.get("receipt_id"),
                "line_id": word.get("line_id"),
                "word_id": word.get("word_id"),
            })
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-popular-count",
        type=int,
        default=3,
        help="A 'popular' text must appear at least this many times at the merchant",
    )
    parser.add_argument(
        "--cutoff",
        type=float,
        default=0.7,
        help="difflib similarity cutoff for close matches (0..1)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Print at most this many candidates",
    )
    parser.add_argument(
        "--max-words",
        type=int,
        default=50_000,
        help="Page at most this many words from Chroma per signal",
    )
    parser.add_argument(
        "--signal",
        choices=["word", "pair", "both"],
        default="both",
        help="Which signal(s) to run",
    )
    args = parser.parse_args()

    _, chroma = _load_clients()
    words_col = chroma.get_collection("words")

    word_candidates: list[dict[str, Any]] = []
    pair_candidates: list[dict[str, Any]] = []

    if args.signal in ("word", "both"):
        product_metas = _paginate_product_words(words_col, max_words=args.max_words)
        logger.info("Loaded %d PRODUCT_NAME words from Chroma", len(product_metas))
        word_candidates = find_outliers(
            product_metas,
            min_popular_count=args.min_popular_count,
            cutoff=args.cutoff,
        )
        word_candidates.sort(
            key=lambda c: (
                -(c["suggested_count"] or 0),
                (c.get("confidence") or 0.0),
                c["merchant"],
            )
        )

    if args.signal in ("pair", "both"):
        all_metas = _paginate_all_validated_words(words_col, max_words=args.max_words)
        logger.info("Loaded %d validated words from Chroma", len(all_metas))
        pair_candidates = find_pair_outliers(
            all_metas,
            min_popular_count=args.min_popular_count,
            cutoff=args.cutoff,
        )
        pair_candidates.sort(
            key=lambda c: (
                -(c["suggested_count"] or 0),
                (c.get("confidence") or 0.0),
                c["merchant"],
            )
        )

    if word_candidates:
        logger.info("Found %d per-word candidates", len(word_candidates))
        print("\n=== PER-WORD (singleton PRODUCT_NAME close to popular text) ===\n")
        print(
            f"{'MERCHANT':<25} {'SUSPECT':<20} {'SUGGESTED':<20} "
            f"{'POP':>5} {'CONF':>6}  {'IMAGE_ID':<36}"
        )
        print("-" * 130)
        for row in word_candidates[: args.limit]:
            conf = row.get("confidence")
            conf_str = f"{conf:.3f}" if isinstance(conf, (int, float)) else " -"
            print(
                f"{row['merchant'][:24]:<25} "
                f"{row['suspect_text'][:19]:<20} "
                f"{row['suggested_text'][:19]:<20} "
                f"{row['suggested_count']:>5} "
                f"{conf_str:>6}  "
                f"{(row.get('image_id') or ''):<36}"
            )

    if pair_candidates:
        logger.info("Found %d per-pair candidates", len(pair_candidates))
        print("\n=== PER-PAIR (singleton neighbor-pair close to popular pair) ===\n")
        print(
            f"{'MERCHANT':<25} {'SUSPECT-CONTEXT':<28} {'SUGGESTED':<14} "
            f"{'POP':>5} {'CONF':>6}  {'IMAGE_ID':<36}"
        )
        print("-" * 130)
        for row in pair_candidates[: args.limit]:
            conf = row.get("confidence")
            conf_str = f"{conf:.3f}" if isinstance(conf, (int, float)) else " -"
            if row["context_field"] == "right":
                ctx = f"{row['suspect_text']} {row['context_value']}"
            else:
                ctx = f"{row['context_value']} {row['suspect_text']}"
            print(
                f"{row['merchant'][:24]:<25} "
                f"{ctx[:27]:<28} "
                f"{row['suggested_text'][:13]:<14} "
                f"{row['suggested_count']:>5} "
                f"{conf_str:>6}  "
                f"{(row.get('image_id') or ''):<36}"
            )
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
