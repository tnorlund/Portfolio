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
import math
import os
import sys
from collections import Counter, defaultdict
from difflib import get_close_matches
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _load_clients() -> tuple[Any, Any]:
    """Load read-only Chroma + Dynamo clients for the configured env."""
    from receipt_chroma import ChromaClient
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


def _levenshtein(a: str, b: str) -> int:
    """Standard Levenshtein edit distance — used for tight correction gating."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


# ---------------------------------------------------------------------------
# Signal #3: character-trigram plausibility
# ---------------------------------------------------------------------------


def build_trigram_model(
    texts: list[str],
) -> tuple[Counter, Counter, float]:
    """
    Char-trigram model with Laplace smoothing.

    Returns (bigram_counts, trigram_counts, vocab_size). vocab_size is
    the effective alphabet size used in the smoothing denominator.
    """
    bigram: Counter = Counter()
    trigram: Counter = Counter()
    vocab: set[str] = set()
    for raw in texts:
        if not raw:
            continue
        t = "^^" + raw.upper() + "$"
        for ch in t:
            vocab.add(ch)
        for i in range(len(t) - 2):
            bigram[t[i : i + 2]] += 1
            trigram[t[i : i + 3]] += 1
    return bigram, trigram, float(max(len(vocab), 2))


def trigram_logprob(
    word: str, bigram: Counter, trigram: Counter, vocab_size: float
) -> float:
    """Average log-probability per char-trigram under the model."""
    if not word:
        return 0.0
    t = "^^" + word.upper() + "$"
    score = 0.0
    n = 0
    for i in range(len(t) - 2):
        tg = t[i : i + 3]
        bg = t[i : i + 2]
        p = (trigram[tg] + 1) / (bigram[bg] + vocab_size)
        score += math.log(p)
        n += 1
    return score / n if n > 0 else 0.0


# ---------------------------------------------------------------------------
# Signal #5: label-bracket — OTHER token surrounded by labeled tokens
# ---------------------------------------------------------------------------


def _is_labeled_other(meta: dict[str, Any]) -> bool:
    """True if the word is labeled OTHER and no other CORE_LABEL is True."""
    other = meta.get("label_OTHER") is True
    has_core = any(
        v is True
        for k, v in meta.items()
        if k.startswith("label_") and k != "label_OTHER" and k != "label_status"
    )
    return other and not has_core


def _word_label_summary(meta: dict[str, Any]) -> str:
    labels = [
        k.removeprefix("label_")
        for k, v in meta.items()
        if k.startswith("label_") and k != "label_status" and v is True
    ]
    return ",".join(sorted(labels)) or "-"


def find_label_bracket(
    metas: list[dict[str, Any]],
    *,
    min_labeled_siblings: int = 1,
) -> list[dict[str, Any]]:
    """
    Find OTHER-labeled words on lines where >=N other words are
    labeled with a CORE_LABEL (e.g. PRODUCT_NAME). These are tokens
    the labeler rejected mid-product-line — the prototype "RAN" case
    fits: line has DAIRY/RAW/WHOLE/MILK labeled PRODUCT_NAME and
    "RAN" labeled OTHER right between them.
    """
    by_line: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for meta in metas:
        image_id = meta.get("image_id")
        receipt_id = meta.get("receipt_id")
        line_id = meta.get("line_id")
        if image_id is None or receipt_id is None or line_id is None:
            continue
        by_line[(image_id, int(receipt_id), int(line_id))].append(meta)

    candidates: list[dict[str, Any]] = []
    for (image_id, receipt_id, line_id), words in by_line.items():
        labeled = [w for w in words if not _is_labeled_other(w) and any(
            k.startswith("label_") and k != "label_status" and v is True
            for k, v in w.items()
        )]
        if len(labeled) < min_labeled_siblings:
            continue
        for w in words:
            if not _is_labeled_other(w):
                continue
            candidates.append({
                "image_id": image_id,
                "receipt_id": receipt_id,
                "line_id": line_id,
                "word_id": w.get("word_id"),
                "merchant": w.get("merchant_name") or "",
                "suspect_text": w.get("text") or "",
                "confidence": w.get("confidence"),
                "left": w.get("left") or "",
                "right": w.get("right") or "",
                "sibling_labels": ",".join(sorted({
                    label
                    for sib in labeled
                    for label in (_word_label_summary(sib).split(",") if sib else [])
                    if label and label != "-"
                })),
                "sibling_count": len(labeled),
            })
    return candidates


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
        choices=["word", "pair", "label-bracket", "both"],
        default="both",
        help="Which signal(s) to run",
    )
    parser.add_argument(
        "--ngram-floor",
        type=float,
        default=None,
        help=(
            "Post-filter: only keep per-pair candidates whose suspect text "
            "has avg trigram log-prob below this floor (more implausible). "
            "Try -5.0 as a starting threshold."
        ),
    )
    parser.add_argument(
        "--max-confidence",
        type=float,
        default=None,
        help=(
            "Post-filter: only keep candidates whose source word.confidence "
            "is <= this. Useful for sanity-checking the hypothesis that "
            "Apple Vision confidence correlates with OCR errors."
        ),
    )
    parser.add_argument(
        "--dry-run-corrections",
        action="store_true",
        help=(
            "Apply tight thresholds (suggested_count >= "
            "--correction-min-count, Levenshtein <= --correction-max-edit, "
            "abs(len-diff) <= --correction-max-length-diff) to per-pair "
            "candidates and print the proposed text patches. No writes."
        ),
    )
    parser.add_argument("--correction-min-count", type=int, default=10)
    parser.add_argument("--correction-max-edit", type=int, default=1)
    parser.add_argument("--correction-max-length-diff", type=int, default=2)
    args = parser.parse_args()

    _, chroma = _load_clients()
    words_col = chroma.get_collection("words")

    word_candidates: list[dict[str, Any]] = []
    pair_candidates: list[dict[str, Any]] = []
    bracket_candidates: list[dict[str, Any]] = []
    all_metas: list[dict[str, Any]] = []

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

    if args.signal in ("pair", "label-bracket", "both"):
        all_metas = _paginate_all_validated_words(words_col, max_words=args.max_words)
        logger.info("Loaded %d validated words from Chroma", len(all_metas))

    if args.signal in ("pair", "both"):
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

    if args.signal == "label-bracket":
        bracket_candidates = find_label_bracket(all_metas)
        bracket_candidates.sort(
            key=lambda c: (-(c["sibling_count"] or 0), c["merchant"])
        )

    # Build the trigram model from all validated text (used for both
    # the --ngram-floor filter and the dry-run-corrections column).
    trigram_inputs = (
        [m.get("text") or "" for m in all_metas]
        if all_metas
        else [m.get("text") or "" for m in product_metas]
        if args.signal == "word"
        else []
    )
    bigram_counts: Counter = Counter()
    trigram_counts: Counter = Counter()
    vocab_size = 2.0
    if trigram_inputs and (
        args.ngram_floor is not None
        or args.dry_run_corrections
        or args.signal == "label-bracket"
    ):
        bigram_counts, trigram_counts, vocab_size = build_trigram_model(trigram_inputs)
        logger.info(
            "Built trigram model: %d bigrams, %d trigrams, vocab=%d",
            len(bigram_counts),
            len(trigram_counts),
            int(vocab_size),
        )

    def _score(text: str) -> float:
        return trigram_logprob(text, bigram_counts, trigram_counts, vocab_size)

    if args.ngram_floor is not None:
        before_pair = len(pair_candidates)
        pair_candidates = [
            c
            for c in pair_candidates
            if _score(c["suspect_text"]) < args.ngram_floor
        ]
        before_bracket = len(bracket_candidates)
        bracket_candidates = [
            c
            for c in bracket_candidates
            if _score(c["suspect_text"]) < args.ngram_floor
        ]
        logger.info(
            "ngram-floor=%.2f kept %d/%d pair, %d/%d label-bracket",
            args.ngram_floor,
            len(pair_candidates),
            before_pair,
            len(bracket_candidates),
            before_bracket,
        )

    if args.max_confidence is not None:
        def _keep(c: dict[str, Any]) -> bool:
            conf = c.get("confidence")
            return isinstance(conf, (int, float)) and conf <= args.max_confidence

        before = len(pair_candidates)
        pair_candidates = [c for c in pair_candidates if _keep(c)]
        word_candidates = [c for c in word_candidates if _keep(c)]
        bracket_candidates = [c for c in bracket_candidates if _keep(c)]
        logger.info(
            "max-confidence=%.2f kept %d/%d pair candidates",
            args.max_confidence,
            len(pair_candidates),
            before,
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

    if pair_candidates and not args.dry_run_corrections:
        logger.info("Found %d per-pair candidates", len(pair_candidates))
        print("\n=== PER-PAIR (singleton neighbor-pair close to popular pair) ===\n")
        header = (
            f"{'MERCHANT':<25} {'SUSPECT-CONTEXT':<28} {'SUGGESTED':<14} "
            f"{'POP':>5} {'CONF':>6}"
        )
        if bigram_counts:
            header += f" {'NGRAM':>7}"
        header += f"  {'IMAGE_ID':<36}"
        print(header)
        print("-" * (len(header) + 4))
        for row in pair_candidates[: args.limit]:
            conf = row.get("confidence")
            conf_str = f"{conf:.3f}" if isinstance(conf, (int, float)) else " -"
            if row["context_field"] == "right":
                ctx = f"{row['suspect_text']} {row['context_value']}"
            else:
                ctx = f"{row['context_value']} {row['suspect_text']}"
            line = (
                f"{row['merchant'][:24]:<25} "
                f"{ctx[:27]:<28} "
                f"{row['suggested_text'][:13]:<14} "
                f"{row['suggested_count']:>5} "
                f"{conf_str:>6}"
            )
            if bigram_counts:
                line += f" {_score(row['suspect_text']):>7.2f}"
            line += f"  {(row.get('image_id') or ''):<36}"
            print(line)

    if bracket_candidates:
        logger.info("Found %d label-bracket candidates", len(bracket_candidates))
        print("\n=== LABEL-BRACKET (OTHER token among labeled siblings) ===\n")
        print(
            f"{'MERCHANT':<25} {'LEFT':<10} {'SUSPECT':<14} {'RIGHT':<10} "
            f"{'#SIB':>4} {'CONF':>6} {'NGRAM':>7}  {'IMAGE_ID':<36} "
            f"{'LINE':>5} {'WORD':>5}"
        )
        print("-" * 140)
        for row in bracket_candidates[: args.limit]:
            conf = row.get("confidence")
            conf_str = f"{conf:.3f}" if isinstance(conf, (int, float)) else " -"
            ngram_str = (
                f"{_score(row['suspect_text']):>7.2f}" if bigram_counts else "   -"
            )
            print(
                f"{(row['merchant'] or '')[:24]:<25} "
                f"{(row['left'] or '')[:9]:<10} "
                f"{row['suspect_text'][:13]:<14} "
                f"{(row['right'] or '')[:9]:<10} "
                f"{row['sibling_count']:>4} "
                f"{conf_str:>6} "
                f"{ngram_str} "
                f" {(row.get('image_id') or ''):<36} "
                f"{row['line_id']:>5} "
                f"{row.get('word_id') or 0:>5}"
            )

    if args.dry_run_corrections and pair_candidates:
        kept = []
        for c in pair_candidates:
            suspect = (c["suspect_text"] or "").upper()
            suggested = (c["suggested_text"] or "").upper()
            if (c["suggested_count"] or 0) < args.correction_min_count:
                continue
            if _levenshtein(suspect, suggested) > args.correction_max_edit:
                continue
            if abs(len(suspect) - len(suggested)) > args.correction_max_length_diff:
                continue
            kept.append(c)
        logger.info(
            "Dry-run-corrections: %d/%d candidates pass tight thresholds "
            "(count>=%d, edit<=%d, len-diff<=%d)",
            len(kept),
            len(pair_candidates),
            args.correction_min_count,
            args.correction_max_edit,
            args.correction_max_length_diff,
        )
        print(
            "\n=== DRY-RUN CORRECTIONS (no writes; apply via separate script) ===\n"
        )
        print(
            f"{'MERCHANT':<25} {'OLD':<14} {'NEW':<14} {'POP':>5} {'CONF':>6} "
            f"{'NGRAM':>7}  {'IMAGE_ID':<36} {'R':>3} {'L':>4} {'W':>4}"
        )
        print("-" * 140)
        for row in kept[: args.limit]:
            conf = row.get("confidence")
            conf_str = f"{conf:.3f}" if isinstance(conf, (int, float)) else " -"
            ngram_str = (
                f"{_score(row['suspect_text']):>7.2f}" if bigram_counts else "   -"
            )
            print(
                f"{row['merchant'][:24]:<25} "
                f"{row['suspect_text'][:13]:<14} "
                f"{row['suggested_text'][:13]:<14} "
                f"{row['suggested_count']:>5} "
                f"{conf_str:>6} "
                f"{ngram_str}  "
                f"{(row.get('image_id') or ''):<36} "
                f"{row.get('receipt_id') or 0:>3} "
                f"{row.get('line_id') or 0:>4} "
                f"{row.get('word_id') or 0:>4}"
            )
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
