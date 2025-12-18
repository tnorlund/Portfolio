"""
Helper functions for the Label Evaluator agent.

Provides spatial analysis utilities for grouping words into visual lines,
computing label patterns across receipts, and applying validation rules.
"""

import logging
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel

from receipt_agent.agents.label_evaluator.state import (
    EvaluationIssue,
    MerchantPatterns,
    OtherReceiptData,
    ReviewContext,
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
        value_pairs=defaultdict(int),
        value_pair_positions=defaultdict(lambda: (None, None)),
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

        # Track value pairs: when the same text has different labels
        # Group words by text to find duplicates
        words_by_text: Dict[str, List[Tuple[ReceiptWord, str]]] = defaultdict(list)
        for key, label in current_labels.items():
            word = word_by_id.get(key)
            if word:
                words_by_text[word.text].append((word, label.label))

        # For each text value that appears multiple times with different labels
        for text, word_label_pairs in words_by_text.items():
            if len(word_label_pairs) > 1:
                # Check for same text with different labels
                unique_label_pairs = set()
                label_positions_by_label = {}

                for word, label in word_label_pairs:
                    y = word.calculate_centroid()[1]
                    label_positions_by_label[label] = y
                    for other_word, other_label in word_label_pairs:
                        if label != other_label:
                            pair = tuple(sorted([label, other_label]))
                            unique_label_pairs.add(pair)

                # Record each value pair and their positions
                for pair in unique_label_pairs:
                    patterns.value_pairs[pair] += 1
                    # Store y-positions: (label1_y, label2_y) where labels are sorted
                    label1, label2 = pair
                    y1 = label_positions_by_label.get(label1)
                    y2 = label_positions_by_label.get(label2)
                    if y1 is not None and y2 is not None:
                        patterns.value_pair_positions[pair] = (y1, y2)

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
            word_context=ctx,
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
            word_context=ctx,
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
                word_context=ctx,
            )

    return None


def check_text_label_conflict(
    ctx: WordContext,
    all_contexts: List[WordContext],
    patterns: Optional[MerchantPatterns],
) -> Optional[EvaluationIssue]:
    """
    Check if the same text appears elsewhere with a different label.

    Uses learned patterns to distinguish between:
    - Valid value pairs (e.g., SUBTOTAL+GRAND_TOTAL with no tax)
    - Genuine conflicts (e.g., MERCHANT_NAME appearing as PRODUCT_NAME)

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
            other_label = other.current_label.label

            # Check if this label pair is a learned pattern (valid combination)
            pair = tuple(sorted([label, other_label]))
            is_known_pair = patterns and pair in patterns.value_pairs

            if is_known_pair:
                # This is a known valid combination (e.g., SUBTOTAL + GRAND_TOTAL)
                # Verify spatial ordering makes sense
                y_positions = patterns.value_pair_positions.get(pair)
                if y_positions and y_positions[0] is not None and y_positions[1] is not None:
                    expected_y1, expected_y2 = y_positions
                    actual_y1 = ctx.normalized_y if label == pair[0] else other.normalized_y
                    actual_y2 = ctx.normalized_y if label == pair[1] else other.normalized_y

                    # Check if spatial ordering roughly matches (allow small variation)
                    # Y values: 0=bottom, 1=top (receipt coordinates)
                    same_order = (actual_y1 - actual_y2) * (expected_y1 - expected_y2) >= 0

                    if same_order or abs(actual_y1 - actual_y2) < 0.1:
                        # Spatial ordering is correct or very close, no issue
                        continue
                    # If spatial ordering is wrong, flag it
                    return EvaluationIssue(
                        issue_type="text_label_conflict",
                        word=ctx.word,
                        current_label=label,
                        suggested_status="NEEDS_REVIEW",
                        reasoning=(
                            f"'{ctx.word.text}' labeled {label} at y={ctx.normalized_y:.2f}, "
                            f"but same text labeled {other_label} at y={other.normalized_y:.2f}. "
                            f"Spatial ordering doesn't match learned pattern for this label pair."
                        ),
                        word_context=ctx,
                    )
                # Spatial ordering looks good, no issue
                continue

            # Not a known pair - check which position makes more sense
            if patterns:
                my_fit = _position_fit_score(ctx.normalized_y, label, patterns)
                other_fit = _position_fit_score(
                    other.normalized_y, other_label, patterns
                )

                if other_fit > my_fit + 0.5:  # Other position is significantly better
                    return EvaluationIssue(
                        issue_type="text_label_conflict",
                        word=ctx.word,
                        current_label=label,
                        suggested_status="NEEDS_REVIEW",
                        reasoning=(
                            f"'{ctx.word.text}' labeled {label} at y={ctx.normalized_y:.2f}, "
                            f"but same text labeled {other_label} at "
                            f"y={other.normalized_y:.2f} which better fits merchant pattern"
                        ),
                        word_context=ctx,
                    )
            else:
                # No patterns, flag unknown conflicts
                return EvaluationIssue(
                    issue_type="text_label_conflict",
                    word=ctx.word,
                    current_label=label,
                    suggested_status="NEEDS_REVIEW",
                    reasoning=(
                        f"'{ctx.word.text}' labeled {label} at y={ctx.normalized_y:.2f}, "
                        f"but same text labeled {other_label} at "
                        f"y={other.normalized_y:.2f} - inconsistent labeling"
                    ),
                    word_context=ctx,
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
                word_context=ctx,
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


# -----------------------------------------------------------------------------
# Review Context Building Functions
# -----------------------------------------------------------------------------


def format_receipt_text(
    visual_lines: List[VisualLine],
    target_word: Optional[ReceiptWord] = None,
) -> str:
    """
    Format receipt as readable text with optional target word marked.

    Args:
        visual_lines: Visual lines from assemble_visual_lines()
        target_word: Optional word to mark with [brackets]

    Returns:
        Receipt text in reading order, one line per visual line
    """
    lines = []
    for visual_line in visual_lines:
        line_parts = []
        for word_ctx in visual_line.words:
            word_text = word_ctx.word.text
            # Mark target word with brackets
            if target_word and (
                word_ctx.word.line_id == target_word.line_id
                and word_ctx.word.word_id == target_word.word_id
            ):
                word_text = f"[{word_text}]"
            line_parts.append(word_text)
        lines.append(" ".join(line_parts))
    return "\n".join(lines)


def get_visual_line_text(issue: EvaluationIssue) -> str:
    """
    Get the text of the visual line containing the issue word.

    Args:
        issue: EvaluationIssue with word_context

    Returns:
        Visual line text as a string
    """
    if not issue.word_context:
        return issue.word.text

    # Get words on same line including this word
    same_line = [issue.word_context] + issue.word_context.same_line_words
    # Sort by x position
    same_line.sort(key=lambda c: c.normalized_x)
    return " ".join(c.word.text for c in same_line)


def get_visual_line_labels(issue: EvaluationIssue) -> List[str]:
    """
    Get the labels of other words on the same visual line.

    Args:
        issue: EvaluationIssue with word_context

    Returns:
        List of labels (excluding the target word)
    """
    if not issue.word_context:
        return []

    labels = []
    for ctx in issue.word_context.same_line_words:
        if ctx.current_label:
            labels.append(ctx.current_label.label)
        else:
            labels.append("-")
    return labels


def format_label_history(word_context: Optional[WordContext]) -> List[Dict]:
    """
    Format label history for a word as a list of dicts.

    Args:
        word_context: WordContext with label_history

    Returns:
        List of dicts with label, status, proposed_by, timestamp
    """
    if not word_context or not word_context.label_history:
        return []

    history = []
    for label in word_context.label_history:
        history.append(
            {
                "label": label.label,
                "status": label.validation_status,
                "proposed_by": label.label_proposed_by,
                "timestamp": (
                    label.timestamp_added.isoformat()
                    if hasattr(label.timestamp_added, "isoformat")
                    else str(label.timestamp_added)
                ),
            }
        )
    return history


def build_review_context(
    issue: EvaluationIssue,
    visual_lines: List[VisualLine],
    merchant_name: str,
) -> ReviewContext:
    """
    Build complete context for LLM review of an issue.

    Args:
        issue: The evaluation issue to review
        visual_lines: All visual lines from the receipt
        merchant_name: Name of the merchant

    Returns:
        ReviewContext with all information needed for LLM review
    """
    return ReviewContext(
        word_text=issue.word.text,
        current_label=issue.current_label,
        issue_type=issue.issue_type,
        evaluator_reasoning=issue.reasoning,
        receipt_text=format_receipt_text(visual_lines, target_word=issue.word),
        visual_line_text=get_visual_line_text(issue),
        visual_line_labels=get_visual_line_labels(issue),
        label_history=format_label_history(issue.word_context),
        merchant_name=merchant_name,
    )


# -----------------------------------------------------------------------------
# ChromaDB Similar Words Query
# -----------------------------------------------------------------------------


@dataclass
class SimilarWordResult:
    """Result from ChromaDB similarity search for a word."""

    word_text: str
    similarity_score: float
    label: Optional[str]
    validation_status: Optional[str]
    valid_labels: List[str] = field(default_factory=list)
    invalid_labels: List[str] = field(default_factory=list)
    merchant_name: Optional[str] = None


def build_word_chroma_id(
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
) -> str:
    """
    Build the ChromaDB document ID for a word.

    Args:
        image_id: Image UUID
        receipt_id: Receipt number
        line_id: Line number
        word_id: Word number

    Returns:
        ChromaDB document ID in format IMAGE#...#RECEIPT#...#LINE#...#WORD#...
    """
    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}"


def query_similar_validated_words(
    word: ReceiptWord,
    chroma_client: Any,
    n_results: int = 10,
    min_similarity: float = 0.7,
) -> List[SimilarWordResult]:
    """
    Query ChromaDB for similar words that have validated labels.

    Uses the word's existing embedding from ChromaDB instead of generating
    a new one. This ensures we use the same contextual embedding that was
    created during the embedding pipeline.

    Args:
        word: The ReceiptWord to find similar words for
        chroma_client: ChromaDB client (DualChromaClient or similar)
        n_results: Maximum number of results to return
        min_similarity: Minimum similarity score (0.0-1.0) to include

    Returns:
        List of SimilarWordResult objects, sorted by similarity descending
    """
    if not chroma_client:
        logger.warning("ChromaDB client not provided")
        return []

    try:
        # Build the word's ChromaDB ID
        word_chroma_id = build_word_chroma_id(
            word.image_id, word.receipt_id, word.line_id, word.word_id
        )

        # Get the word's existing embedding from ChromaDB
        get_result = chroma_client.get(
            collection_name="words",
            ids=[word_chroma_id],
            include=["embeddings"],
        )

        if not get_result:
            logger.warning(f"Word not found in ChromaDB: {word_chroma_id}")
            return []

        embeddings = get_result.get("embeddings")
        if embeddings is None or len(embeddings) == 0:
            logger.warning(f"No embeddings found for word: {word_chroma_id}")
            return []

        if embeddings[0] is None:
            logger.warning(f"No embedding found for word: {word_chroma_id}")
            return []

        # Convert numpy array to list
        try:
            query_embedding = list(embeddings[0])
        except (TypeError, ValueError):
            logger.warning(f"Invalid embedding format for word: {word_chroma_id}")
            return []

        if not query_embedding:
            logger.warning(f"Empty embedding found for word: {word_chroma_id}")
            return []

        # Query ChromaDB words collection using the existing embedding
        results = chroma_client.query(
            collection_name="words",
            query_embeddings=[query_embedding],
            n_results=n_results * 2 + 1,  # Fetch more, filter later (+1 for self)
            include=["documents", "metadatas", "distances"],
        )

        if not results or not results.get("ids"):
            return []

        ids = list(results.get("ids", [[]])[0])
        documents = list(results.get("documents", [[]])[0])
        metadatas = list(results.get("metadatas", [[]])[0])
        distances = list(results.get("distances", [[]])[0])

        similar_words: List[SimilarWordResult] = []

        for doc_id, doc, meta, dist in zip(ids, documents, metadatas, distances):
            # Skip self (the query word)
            if doc_id == word_chroma_id:
                continue

            # Convert distance to Python float and compute similarity
            try:
                dist_float = float(dist)
            except (TypeError, ValueError):
                logger.debug(f"Invalid distance value: {dist}")
                continue

            # Convert L2 distance to similarity (0.0-1.0)
            similarity = max(0.0, 1.0 - (dist_float / 2))

            if similarity < min_similarity:
                continue

            # Parse valid/invalid labels from metadata
            valid_labels_str = meta.get("valid_labels", "")
            invalid_labels_str = meta.get("invalid_labels", "")
            valid_labels = (
                [lbl.strip() for lbl in valid_labels_str.split(",") if lbl.strip()]
                if valid_labels_str
                else []
            )
            invalid_labels = (
                [lbl.strip() for lbl in invalid_labels_str.split(",") if lbl.strip()]
                if invalid_labels_str
                else []
            )

            similar_words.append(
                SimilarWordResult(
                    word_text=doc or meta.get("text", ""),
                    similarity_score=similarity,
                    label=meta.get("label"),
                    validation_status=meta.get("validation_status"),
                    valid_labels=valid_labels,
                    invalid_labels=invalid_labels,
                    merchant_name=meta.get("merchant_name"),
                )
            )

        # Sort by similarity descending and limit results
        similar_words.sort(key=lambda w: -w.similarity_score)
        return similar_words[:n_results]

    except Exception as e:
        logger.error(f"Error querying ChromaDB for similar words: {e}", exc_info=True)
        return []


def format_similar_words_for_prompt(
    similar_words: List[SimilarWordResult],
    max_examples: int = 5,
) -> str:
    """
    Format similar validated words for inclusion in LLM prompt.

    Prioritizes words with VALID validation status and groups by label.

    Args:
        similar_words: Results from query_similar_validated_words()
        max_examples: Maximum number of examples to include

    Returns:
        Formatted string for LLM prompt
    """
    if not similar_words:
        return "No similar validated words found in database."

    # Prioritize validated words
    validated = [w for w in similar_words if w.validation_status == "VALID"]
    other = [w for w in similar_words if w.validation_status != "VALID"]

    # Take validated first, then fill with others
    examples = validated[:max_examples]
    if len(examples) < max_examples:
        examples.extend(other[: max_examples - len(examples)])

    if not examples:
        return "No similar validated words found in database."

    lines = []
    for w in examples:
        status = f"[{w.validation_status}]" if w.validation_status else "[unvalidated]"
        label = w.label or "no label"
        lines.append(
            f"- \"{w.word_text}\" → {label} {status} (similarity: {w.similarity_score:.2f})"
        )

        # Add valid/invalid labels if available
        if w.valid_labels:
            lines.append(f"    Valid labels: {', '.join(w.valid_labels)}")
        if w.invalid_labels:
            lines.append(f"    Invalid labels: {', '.join(w.invalid_labels)}")

    return "\n".join(lines)
