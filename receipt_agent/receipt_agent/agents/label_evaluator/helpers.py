"""
Helper functions for the Label Evaluator agent.

Provides spatial analysis utilities for grouping words into visual lines,
computing label patterns across receipts, and applying validation rules.
"""

import logging
import statistics
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel

from receipt_agent.agents.label_evaluator.state import (
    EvaluationIssue,
    MerchantPatterns,
    OtherReceiptData,
    VisualLine,
    WordContext,
)

logger = logging.getLogger(__name__)


def build_word_contexts(
    words: List[ReceiptWord],
    labels: List[ReceiptWordLabel],
) -> List[WordContext]:
    """
    Build WordContext objects for each word, linking to their label history.

    Args:
        words: All ReceiptWord entities for the receipt
        labels: All ReceiptWordLabel entities for the receipt

    Returns:
        List of WordContext objects with label history populated
    """
    # Group labels by word (line_id, word_id)
    labels_by_word: Dict[Tuple[int, int], List[ReceiptWordLabel]] = defaultdict(list)
    for label in labels:
        key = (label.line_id, label.word_id)
        labels_by_word[key].append(label)

    # Sort labels by timestamp to find "current" (most recent)
    for key in labels_by_word:
        labels_by_word[key].sort(
            key=lambda lbl: (
                lbl.timestamp_added.isoformat()
                if hasattr(lbl.timestamp_added, "isoformat")
                else str(lbl.timestamp_added)
            ),
            reverse=True,
        )

    # Build WordContext for each word
    word_contexts = []
    for word in words:
        key = (word.line_id, word.word_id)
        history = labels_by_word.get(key, [])
        current = history[0] if history else None

        centroid = word.calculate_centroid()
        ctx = WordContext(
            word=word,
            current_label=current,
            label_history=history,
            normalized_y=centroid[1],
            normalized_x=centroid[0],
        )
        word_contexts.append(ctx)

    return word_contexts


def assemble_visual_lines(
    word_contexts: List[WordContext],
    y_tolerance: Optional[float] = None,
) -> List[VisualLine]:
    """
    Group words into visual lines by y-coordinate proximity.

    Since OCR line_id can split visual lines (e.g., product name and price
    on the same row become separate line_ids), this function groups words
    by their actual y-position using the same tolerance logic as
    extract_pricing_table_from_words in helpers.py.

    Args:
        word_contexts: List of WordContext objects to group
        y_tolerance: Optional explicit tolerance. If None, computed from
            median word height (median_height * 0.75)

    Returns:
        List of VisualLine objects, sorted top-to-bottom (y descending)
    """
    if not word_contexts:
        return []

    # Sort by y descending (top first, since y=1 is top, y=0 is bottom)
    sorted_contexts = sorted(
        word_contexts,
        key=lambda c: -c.normalized_y,
    )

    # Calculate row tolerance from median word height if not provided
    if y_tolerance is None:
        heights = [
            w.word.bounding_box.get("height", 0.02)
            for w in sorted_contexts
            if w.word.bounding_box.get("height")
        ]
        if heights:
            median_height = sorted(heights)[len(heights) // 2]
            y_tolerance = max(0.01, median_height * 0.75)
        else:
            y_tolerance = 0.015  # Fallback

    # Group by y-proximity
    visual_lines: List[VisualLine] = []
    current_line_words: List[WordContext] = [sorted_contexts[0]]
    current_y = sorted_contexts[0].normalized_y

    for ctx in sorted_contexts[1:]:
        if abs(ctx.normalized_y - current_y) <= y_tolerance:
            current_line_words.append(ctx)
            # Update running average y
            current_y = sum(c.normalized_y for c in current_line_words) / len(
                current_line_words
            )
        else:
            # Sort current line left-to-right and save
            current_line_words.sort(key=lambda c: c.normalized_x)
            visual_lines.append(
                VisualLine(
                    line_index=len(visual_lines),
                    words=current_line_words,
                    y_center=current_y,
                )
            )
            current_line_words = [ctx]
            current_y = ctx.normalized_y

    # Don't forget last line
    current_line_words.sort(key=lambda c: c.normalized_x)
    visual_lines.append(
        VisualLine(
            line_index=len(visual_lines),
            words=current_line_words,
            y_center=current_y,
        )
    )

    # Populate position_in_line and same_line_words for each context
    for line in visual_lines:
        for i, ctx in enumerate(line.words):
            ctx.visual_line_index = line.line_index
            ctx.position_in_line = i
            ctx.same_line_words = [c for c in line.words if c is not ctx]

    return visual_lines


def compute_merchant_patterns(
    other_receipt_data: List[OtherReceiptData],
    merchant_name: str,
) -> Optional[MerchantPatterns]:
    """
    Compute label patterns from other receipts of the same merchant.

    Analyzes validated labels from other receipts to build a statistical
    model of expected label positions and co-occurrence patterns.

    Args:
        other_receipt_data: Data from other receipts of same merchant
        merchant_name: Name of the merchant

    Returns:
        MerchantPatterns object, or None if insufficient data
    """
    if not other_receipt_data:
        return None

    patterns = MerchantPatterns(
        merchant_name=merchant_name,
        receipt_count=len(other_receipt_data),
        label_positions=defaultdict(list),
        label_texts=defaultdict(set),
        same_line_pairs=defaultdict(int),
    )

    for receipt_data in other_receipt_data:
        words = receipt_data.words
        labels = receipt_data.labels

        # Build word lookup
        word_by_id: Dict[Tuple[int, int], ReceiptWord] = {
            (w.line_id, w.word_id): w for w in words
        }

        # Get most recent label per word, preferring VALID ones
        labels_by_word: Dict[Tuple[int, int], List[ReceiptWordLabel]] = defaultdict(
            list
        )
        for label in labels:
            key = (label.line_id, label.word_id)
            labels_by_word[key].append(label)

        current_labels: Dict[Tuple[int, int], ReceiptWordLabel] = {}
        for key, label_list in labels_by_word.items():
            # Sort by timestamp descending
            label_list.sort(
                key=lambda lbl: (
                    lbl.timestamp_added.isoformat()
                    if hasattr(lbl.timestamp_added, "isoformat")
                    else str(lbl.timestamp_added)
                ),
                reverse=True,
            )
            # Prefer VALID labels for pattern learning
            valid_labels = [
                lbl for lbl in label_list if lbl.validation_status == "VALID"
            ]
            if valid_labels:
                current_labels[key] = valid_labels[0]
            elif label_list:
                # Fall back to most recent if no VALID labels
                current_labels[key] = label_list[0]

        # Record label positions and texts
        for key, label in current_labels.items():
            word = word_by_id.get(key)
            if word:
                y = word.calculate_centroid()[1]
                patterns.label_positions[label.label].append(y)
                patterns.label_texts[label.label].add(word.text)

        # Record same-line pairs using line_id as proxy
        # (imperfect but fast - visual line grouping would be more accurate)
        labels_by_line: Dict[int, List[str]] = defaultdict(list)
        for key, label in current_labels.items():
            labels_by_line[key[0]].append(label.label)

        for line_labels in labels_by_line.values():
            unique_labels = list(set(line_labels))
            for i, label_a in enumerate(unique_labels):
                for label_b in unique_labels[i + 1 :]:
                    pair = tuple(sorted([label_a, label_b]))
                    patterns.same_line_pairs[pair] += 1

    return patterns


def check_position_anomaly(
    ctx: WordContext,
    patterns: Optional[MerchantPatterns],
    std_threshold: float = 2.5,
) -> Optional[EvaluationIssue]:
    """
    Check if a labeled word's position is anomalous for its label type.

    Compares the word's y-position against the expected distribution
    from other receipts of the same merchant.

    Args:
        ctx: WordContext to check
        patterns: MerchantPatterns from other receipts (may be None)
        std_threshold: Number of standard deviations for anomaly detection

    Returns:
        EvaluationIssue if anomaly detected, None otherwise
    """
    if ctx.current_label is None or patterns is None:
        return None

    label = ctx.current_label.label
    if label not in patterns.label_positions:
        return None

    positions = patterns.label_positions[label]
    if len(positions) < 2:
        return None  # Not enough data for statistics

    mean_y = statistics.mean(positions)
    std_y = statistics.stdev(positions)

    if std_y == 0:
        return None  # All positions identical, can't detect anomaly

    z_score = abs(ctx.normalized_y - mean_y) / std_y
    if z_score > std_threshold:
        return EvaluationIssue(
            issue_type="position_anomaly",
            word=ctx.word,
            current_label=label,
            suggested_status="NEEDS_REVIEW",
            reasoning=(
                f"{label} at y={ctx.normalized_y:.2f} but typically "
                f"appears at y={mean_y:.2f}\u00b1{std_y:.2f} for this merchant "
                f"(z-score={z_score:.1f})"
            ),
        )

    return None


def check_same_line_conflict(
    ctx: WordContext,
) -> Optional[EvaluationIssue]:
    """
    Check if a labeled word has conflicting labels on the same visual line.

    Detects cases like MERCHANT_NAME appearing on the same line as PRODUCT_NAME,
    which suggests one of them is mislabeled.

    Args:
        ctx: WordContext to check

    Returns:
        EvaluationIssue if conflict detected, None otherwise
    """
    if ctx.current_label is None:
        return None

    label = ctx.current_label.label
    same_line_labels = [
        c.current_label.label for c in ctx.same_line_words if c.current_label
    ]

    # MERCHANT_NAME should not appear on same line as PRODUCT_NAME
    if label == "MERCHANT_NAME" and "PRODUCT_NAME" in same_line_labels:
        return EvaluationIssue(
            issue_type="same_line_conflict",
            word=ctx.word,
            current_label=label,
            suggested_status="INVALID",
            suggested_label="PRODUCT_NAME",
            reasoning=(
                f"'{ctx.word.text}' labeled {label} but on same visual line "
                f"as PRODUCT_NAME words - likely part of product name"
            ),
        )

    # ADDRESS_LINE words should generally be together, not mixed with other header labels
    if label == "ADDRESS_LINE":
        conflicting = [
            lbl
            for lbl in same_line_labels
            if lbl in ("PRODUCT_NAME", "LINE_TOTAL", "QUANTITY", "UNIT_PRICE")
        ]
        if conflicting:
            return EvaluationIssue(
                issue_type="same_line_conflict",
                word=ctx.word,
                current_label=label,
                suggested_status="NEEDS_REVIEW",
                reasoning=(
                    f"'{ctx.word.text}' labeled {label} but on same visual line "
                    f"as {', '.join(set(conflicting))} - unusual combination"
                ),
            )

    return None


def check_text_label_conflict(
    ctx: WordContext,
    all_contexts: List[WordContext],
    patterns: Optional[MerchantPatterns],
) -> Optional[EvaluationIssue]:
    """
    Check if the same text appears elsewhere with a different label.

    Detects cases like "Sprouts" appearing at the top (MERCHANT_NAME) and
    middle (should be PRODUCT_NAME) of the receipt.

    Args:
        ctx: WordContext to check
        all_contexts: All WordContexts on the receipt
        patterns: MerchantPatterns for position comparison (may be None)

    Returns:
        EvaluationIssue if conflict detected, None otherwise
    """
    if ctx.current_label is None:
        return None

    label = ctx.current_label.label

    # Find other words with the same text (case-insensitive)
    same_text_contexts = [
        c
        for c in all_contexts
        if c.word.text.lower() == ctx.word.text.lower()
        and c is not ctx
        and c.current_label is not None
    ]

    for other in same_text_contexts:
        if other.current_label.label != label:
            # Same text, different labels
            # Check which position makes more sense using patterns
            if patterns:
                my_fit = _position_fit_score(ctx.normalized_y, label, patterns)
                other_fit = _position_fit_score(
                    other.normalized_y, other.current_label.label, patterns
                )

                if other_fit > my_fit + 0.5:  # Other position is significantly better
                    return EvaluationIssue(
                        issue_type="text_label_conflict",
                        word=ctx.word,
                        current_label=label,
                        suggested_status="NEEDS_REVIEW",
                        reasoning=(
                            f"'{ctx.word.text}' labeled {label} at y={ctx.normalized_y:.2f}, "
                            f"but same text labeled {other.current_label.label} at "
                            f"y={other.normalized_y:.2f} which better fits merchant pattern"
                        ),
                    )
            else:
                # No patterns, just flag the conflict
                return EvaluationIssue(
                    issue_type="text_label_conflict",
                    word=ctx.word,
                    current_label=label,
                    suggested_status="NEEDS_REVIEW",
                    reasoning=(
                        f"'{ctx.word.text}' labeled {label} at y={ctx.normalized_y:.2f}, "
                        f"but same text labeled {other.current_label.label} at "
                        f"y={other.normalized_y:.2f} - inconsistent labeling"
                    ),
                )

    return None


def _position_fit_score(
    y_position: float,
    label: str,
    patterns: MerchantPatterns,
) -> float:
    """
    Calculate how well a y-position fits the expected position for a label.

    Returns a score where higher is better (0.0 to 1.0).
    Uses inverse of z-score, clamped to [0, 1].

    Args:
        y_position: Normalized y position (0=bottom, 1=top)
        label: Label type
        patterns: MerchantPatterns with position data

    Returns:
        Fit score between 0.0 (poor fit) and 1.0 (perfect fit)
    """
    if label not in patterns.label_positions:
        return 0.5  # Unknown label, neutral score

    positions = patterns.label_positions[label]
    if len(positions) < 2:
        return 0.5  # Not enough data

    mean_y = statistics.mean(positions)
    std_y = statistics.stdev(positions)

    if std_y == 0:
        # All positions identical
        return 1.0 if abs(y_position - mean_y) < 0.01 else 0.0

    z_score = abs(y_position - mean_y) / std_y
    # Convert z-score to fit score: z=0 -> 1.0, z=3 -> 0.0
    fit_score = max(0.0, 1.0 - (z_score / 3.0))
    return fit_score


def check_missing_label_in_cluster(
    ctx: WordContext,
) -> Optional[EvaluationIssue]:
    """
    Check if an unlabeled word should have a label based on surrounding labels.

    Detects cases like a zip code with no label but surrounded by ADDRESS_LINE words.

    Args:
        ctx: WordContext to check (expected to have no current label)

    Returns:
        EvaluationIssue if missing label detected, None otherwise
    """
    if ctx.current_label is not None:
        return None  # Only check unlabeled words

    # Get labels from same visual line
    same_line_labels = [
        c.current_label.label for c in ctx.same_line_words if c.current_label
    ]

    if not same_line_labels:
        return None  # No labeled neighbors, probably correctly unlabeled

    # Check if surrounded by consistent labels
    label_counts = Counter(same_line_labels)
    if not label_counts:
        return None

    most_common_label, count = label_counts.most_common(1)[0]

    # Be conservative - require strong signal (at least 2 neighbors with same label,
    # and they represent at least 70% of labeled neighbors)
    if count >= 2 and count / len(same_line_labels) >= 0.7:
        # Additional check: does word text look plausible for this label?
        if _is_plausible_for_label(ctx.word.text, most_common_label):
            return EvaluationIssue(
                issue_type="missing_label_cluster",
                word=ctx.word,
                current_label=None,
                suggested_status="NEEDS_REVIEW",
                suggested_label=most_common_label,
                reasoning=(
                    f"'{ctx.word.text}' has no label but is surrounded by "
                    f"{count} {most_common_label} words on same visual line"
                ),
            )

    return None


def _is_plausible_for_label(text: str, label: str) -> bool:
    """
    Quick heuristic check if text could plausibly have a specific label.

    Args:
        text: Word text to check
        label: Proposed label type

    Returns:
        True if the text is plausibly this label type
    """
    text_lower = text.lower().strip()

    if label == "ADDRESS_LINE":
        # Zip codes (5 or 9 digits)
        if text.isdigit() and len(text) in (5, 9):
            return True
        # Street numbers
        if text.isdigit():
            return True
        # Common address abbreviations
        if text_lower in (
            "st",
            "ave",
            "rd",
            "blvd",
            "dr",
            "ln",
            "ct",
            "way",
            "ste",
            "apt",
            "unit",
            "fl",
            "n",
            "s",
            "e",
            "w",
            "ne",
            "nw",
            "se",
            "sw",
        ):
            return True
        # State abbreviations (2 letters)
        if len(text) == 2 and text.isalpha():
            return True
        return True  # Be permissive for address parts

    if label == "PHONE_NUMBER":
        # Contains multiple digits
        digits = sum(c.isdigit() for c in text)
        return digits >= 3  # Part of phone number

    if label == "PRODUCT_NAME":
        return True  # Almost anything can be a product name

    if label in ("UNIT_PRICE", "LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"):
        # Should contain digits or currency symbols
        has_digit = any(c.isdigit() for c in text)
        has_currency = "$" in text or "." in text
        return has_digit or has_currency

    if label == "QUANTITY":
        # Typically a number
        return text.replace(".", "").isdigit() or text_lower in ("ea", "each", "lb")

    # Default: be permissive
    return True


def evaluate_word_contexts(
    word_contexts: List[WordContext],
    patterns: Optional[MerchantPatterns],
) -> List[EvaluationIssue]:
    """
    Apply all validation rules to word contexts and collect issues.

    Args:
        word_contexts: All WordContext objects for the receipt
        patterns: MerchantPatterns from other receipts (may be None)

    Returns:
        List of EvaluationIssue objects for all detected issues
    """
    issues: List[EvaluationIssue] = []

    for ctx in word_contexts:
        if ctx.current_label:
            # Check labeled words
            issue = check_position_anomaly(ctx, patterns)
            if issue:
                issues.append(issue)
                continue  # One issue per word

            issue = check_same_line_conflict(ctx)
            if issue:
                issues.append(issue)
                continue

            issue = check_text_label_conflict(ctx, word_contexts, patterns)
            if issue:
                issues.append(issue)
                continue
        else:
            # Check unlabeled words
            issue = check_missing_label_in_cluster(ctx)
            if issue:
                issues.append(issue)

    return issues
