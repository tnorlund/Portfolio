"""
Issue Detection for Label Evaluator.

Provides functions to detect potential labeling issues in receipt words:
- Position anomalies (labels in unexpected y-positions)
- Unexpected label pairs (never seen in training data)
- Geometric anomalies (unusual spatial relationships between labels)
- Constellation anomalies (holistic n-tuple pattern deviations)
- Text-label conflicts (same text with different labels)
- Missing labels in clusters (unlabeled words surrounded by consistent labels)
- Missing constellation members (unlabeled words at expected positions)
"""

import logging
import math
import re
import statistics
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from receipt_agent.agents.label_evaluator.state import (
    DrillDownWord,
    EvaluationIssue,
    LabelPairGeometry,
    MerchantPatterns,
    VisualLine,
    WordContext,
)
from receipt_agent.agents.label_evaluator.word_context import (
    get_same_line_words,
)

logger = logging.getLogger(__name__)


def check_position_anomaly(
    ctx: WordContext,
    patterns: Optional[MerchantPatterns],
    std_threshold: float = 2.5,
) -> Optional[EvaluationIssue]:
    """Disabled: ~89% false positive rate. Returns None unconditionally."""
    # TODO: Re-enable after improving position normalization across receipt formats
    return None


def check_unexpected_label_pair(
    ctx: WordContext,
    all_contexts: List[WordContext],
    patterns: Optional[MerchantPatterns],
) -> Optional[EvaluationIssue]:
    """Disabled: 100% false positive rate. Returns None unconditionally."""
    # TODO: Re-enable with better pair frequency thresholds
    return None


def check_geometric_anomaly(
    ctx: WordContext,
    all_contexts: List[WordContext],
    patterns: Optional[MerchantPatterns],
) -> Optional[EvaluationIssue]:
    """Disabled: 89% false positive rate. Returns None unconditionally."""
    # TODO: Re-enable after improving geometric pattern quality thresholds
    return None


def check_constellation_anomaly(
    ctx: WordContext,
    all_contexts: List[WordContext],
    patterns: Optional[MerchantPatterns],
    threshold_std: float = 2.0,
) -> Optional[EvaluationIssue]:
    """Disabled: 89% false positive rate. Returns None unconditionally."""
    # TODO: Re-enable after improving constellation pattern quality
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
        # current_label is guaranteed non-None by the filter above
        assert other.current_label is not None
        if other.current_label.label != label:
            # Same text, different labels
            other_label = other.current_label.label

            # Check if this label pair is a learned pattern (valid combination)
            pair = tuple(sorted([label, other_label]))
            is_known_pair = patterns and pair in patterns.value_pairs

            if is_known_pair:
                # This is a known valid combination (e.g., SUBTOTAL +
                # GRAND_TOTAL)
                # Verify spatial ordering makes sense
                assert (
                    patterns is not None
                )  # Guaranteed by is_known_pair check
                y_positions = patterns.value_pair_positions.get(pair)
                if (
                    y_positions
                    and y_positions[0] is not None
                    and y_positions[1] is not None
                ):
                    expected_y1, expected_y2 = y_positions
                    actual_y1 = (
                        ctx.normalized_y
                        if label == pair[0]
                        else other.normalized_y
                    )
                    actual_y2 = (
                        ctx.normalized_y
                        if label == pair[1]
                        else other.normalized_y
                    )

                    # Check if spatial ordering roughly matches (allow small
                    # variation)
                    # Y values: 0=bottom, 1=top (receipt coordinates)
                    same_order = (actual_y1 - actual_y2) * (
                        expected_y1 - expected_y2
                    ) >= 0

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
                            f"'{ctx.word.text}' labeled {label} at "
                            f"y={ctx.normalized_y:.2f}, but same text labeled "
                            f"{other_label} at y={other.normalized_y:.2f}. "
                            "Spatial ordering doesn't match learned pattern "
                            "for this label pair."
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

                if (
                    other_fit > my_fit + 0.5
                ):  # Other position is significantly better
                    return EvaluationIssue(
                        issue_type="text_label_conflict",
                        word=ctx.word,
                        current_label=label,
                        suggested_status="NEEDS_REVIEW",
                        reasoning=(
                            f"'{ctx.word.text}' labeled {label} at "
                            f"y={ctx.normalized_y:.2f}, but same text labeled "
                            f"{other_label} at y={other.normalized_y:.2f} "
                            "which better fits merchant pattern"
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
                        f"'{ctx.word.text}' labeled {label} at "
                        f"y={ctx.normalized_y:.2f}, but same text labeled "
                        f"{other_label} at y={other.normalized_y:.2f} - "
                        "inconsistent labeling"
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
    visual_lines: List[VisualLine],
) -> Optional[EvaluationIssue]:
    """
    Check if an unlabeled word should have a label based on surrounding labels.

    Detects cases like a zip code with no label but surrounded by
    ADDRESS_LINE words.

    Args:
        ctx: WordContext to check (expected to have no current label)
        visual_lines: All visual lines for on-demand same-line lookup

    Returns:
        EvaluationIssue if missing label detected, None otherwise
    """
    if ctx.current_label is not None:
        return None  # Only check unlabeled words

    # Get labels from same visual line (computed on-demand)
    same_line_words = get_same_line_words(ctx, visual_lines)
    same_line_labels = [
        c.current_label.label for c in same_line_words if c.current_label
    ]

    if not same_line_labels:
        return None  # No labeled neighbors, probably correctly unlabeled

    # Check if surrounded by consistent labels
    label_counts = Counter(same_line_labels)
    if not label_counts:
        return None

    most_common_label, count = label_counts.most_common(1)[0]

    # Be conservative - require strong signal (at least 2 neighbors with same
    # label, and they represent at least 70% of labeled neighbors)
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


CURRENCY_PATTERN = re.compile(r"^\$?\d+\.\d{2}$")


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
    text_stripped = text.strip()

    if label == "ADDRESS_LINE":
        # Reject purely numeric text that doesn't match address patterns
        if text_stripped.isdigit():
            # Zip codes (5 or 9 digits) and street numbers are valid
            if len(text_stripped) in (5, 9):
                return True
            # Street numbers are typically 1-5 digits
            if len(text_stripped) <= 5:
                return True
            # Long pure numbers are unlikely address parts
            return False
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
        if len(text_stripped) == 2 and text_stripped.isalpha():
            return True
        # Reject if it looks like a currency value
        if CURRENCY_PATTERN.match(text_stripped):
            return False
        return True  # Be permissive for other address parts

    if label == "PHONE_NUMBER":
        # Contains multiple digits
        digits = sum(c.isdigit() for c in text)
        return digits >= 3  # Part of phone number

    if label == "PRODUCT_NAME":
        # Reject if text looks like a pure currency value
        if CURRENCY_PATTERN.match(text_stripped):
            return False
        return True  # Almost anything else can be a product name

    if label in ("UNIT_PRICE", "LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"):
        # Must contain at least one digit
        has_digit = any(c.isdigit() for c in text)
        if not has_digit:
            return False
        return True

    if label == "QUANTITY":
        # Typically a number
        return text_stripped.replace(".", "").isdigit() or text_lower in (
            "ea",
            "each",
            "lb",
        )

    # Default: be permissive
    return True


def check_missing_constellation_member(
    ctx: WordContext,
    all_contexts: List[WordContext],
    patterns: Optional[MerchantPatterns],
    position_threshold: float = 0.08,
) -> Optional[EvaluationIssue]:
    """
    Check if an unlabeled word is at a position where a constellation label
    is expected but missing.

    For each constellation pattern (e.g., SUBTOTAL + TAX + TOTAL):
    1. Check if the receipt has SOME but not ALL labels in the constellation
    2. For each missing label, compute its expected position based on:
       - The centroid of the present labels
       - The learned relative position of the missing label
    3. If this unlabeled word is near the expected position, flag it

    This catches missing labels that simple same-line heuristics miss:
    - TAX label missing between SUBTOTAL and TOTAL
    - ADDRESS_LINE missing from a partial address block
    - PRODUCT_NAME missing from a product line with price

    Args:
        ctx: WordContext to check (expected to have no current label)
        all_contexts: All WordContext objects on the receipt
        patterns: MerchantPatterns with constellation_geometry
        position_threshold: Max distance (normalized) to expected position

    Returns:
        EvaluationIssue if missing constellation member detected, None
        otherwise
    """
    if ctx.current_label is not None:
        return None  # Only check unlabeled words

    if patterns is None or not patterns.constellation_geometry:
        return None

    # Get this word's normalized position
    word_pos = (ctx.normalized_x, ctx.normalized_y)

    # Build label -> centroid mapping for present labels on this receipt
    label_centroids: Dict[str, Tuple[float, float]] = {}
    labels_by_type: Dict[str, List[WordContext]] = defaultdict(list)

    for other_ctx in all_contexts:
        if other_ctx.current_label:
            labels_by_type[other_ctx.current_label.label].append(other_ctx)

    for label_type, contexts in labels_by_type.items():
        if contexts:
            # Use normalized positions for comparison
            norm_positions = [
                (c.normalized_x, c.normalized_y) for c in contexts
            ]
            label_centroids[label_type] = (
                sum(p[0] for p in norm_positions) / len(norm_positions),
                sum(p[1] for p in norm_positions) / len(norm_positions),
            )

    present_labels = set(label_centroids.keys())

    # Check each constellation for partial matches
    best_match: Optional[Tuple[str, str, float]] = (
        None  # (missing_label, constellation_str, distance)
    )

    for constellation, geom in patterns.constellation_geometry.items():
        constellation_labels = set(constellation)

        # Check for partial constellation (some but not all labels present)
        present_in_constellation = constellation_labels & present_labels
        missing_from_constellation = constellation_labels - present_labels

        # Require at least 2 labels present and exactly 1 missing
        # (more conservative - avoids false positives)
        if (
            len(present_in_constellation) < 2
            or len(missing_from_constellation) != 1
        ):
            continue

        missing_label = next(iter(missing_from_constellation))

        # Get the expected relative position for the missing label
        expected_rel = geom.relative_positions.get(missing_label)
        if expected_rel is None:
            continue

        # Use the full constellation centroid from training patterns
        # to avoid bias when a label is missing (present-only centroid shifts)
        if (
            geom.mean_centroid_x is not None
            and geom.mean_centroid_y is not None
        ):
            # Scale the training centroid to the receipt's coordinate space
            # by computing the offset between training and present-label centroids
            present_positions = [
                label_centroids[lbl] for lbl in present_in_constellation
            ]
            present_centroid = (
                sum(p[0] for p in present_positions) / len(present_positions),
                sum(p[1] for p in present_positions) / len(present_positions),
            )

            # Compute the expected centroid of present labels from training data
            training_present_dx = []
            training_present_dy = []
            for lbl in present_in_constellation:
                rel = geom.relative_positions.get(lbl)
                if rel is not None:
                    training_present_dx.append(rel.mean_dx)
                    training_present_dy.append(rel.mean_dy)

            if training_present_dx:
                # Training centroid offset of present labels
                training_present_offset_x = sum(training_present_dx) / len(training_present_dx)
                training_present_offset_y = sum(training_present_dy) / len(training_present_dy)

                # Estimate the full constellation centroid from present labels
                constellation_centroid = (
                    present_centroid[0] - training_present_offset_x,
                    present_centroid[1] - training_present_offset_y,
                )
            else:
                constellation_centroid = present_centroid
        else:
            # Fallback: use present labels centroid
            present_positions = [
                label_centroids[lbl] for lbl in present_in_constellation
            ]
            constellation_centroid = (
                sum(p[0] for p in present_positions) / len(present_positions),
                sum(p[1] for p in present_positions) / len(present_positions),
            )

        # Compute expected absolute position for the missing label
        expected_pos = (
            constellation_centroid[0] + expected_rel.mean_dx,
            constellation_centroid[1] + expected_rel.mean_dy,
        )

        # Check distance from this unlabeled word to expected position
        distance = math.sqrt(
            (word_pos[0] - expected_pos[0]) ** 2
            + (word_pos[1] - expected_pos[1]) ** 2
        )

        # Use adaptive threshold based on learned variance
        threshold = position_threshold
        if expected_rel.std_deviation and expected_rel.std_deviation > 0:
            # Allow 2 standard deviations, but cap at position_threshold
            threshold = min(
                position_threshold, 2.0 * expected_rel.std_deviation
            )

        if distance <= threshold:
            # Check if word text is plausible for this label
            if _is_plausible_for_label(ctx.word.text, missing_label):
                # Track best match (closest distance)
                if best_match is None or distance < best_match[2]:
                    constellation_str = " + ".join(constellation)
                    best_match = (missing_label, constellation_str, distance)

    if best_match:
        missing_label, constellation_str, distance = best_match
        present_str = ", ".join(
            sorted(present_labels & set(constellation_str.split(" + ")))
        )
        return EvaluationIssue(
            issue_type="missing_constellation_member",
            word=ctx.word,
            current_label=None,
            suggested_status="NEEDS_REVIEW",
            suggested_label=missing_label,
            reasoning=(
                f"'{ctx.word.text}' has no label but is at the expected "
                f"position for {missing_label} within constellation "
                f"[{constellation_str}]. Present labels: [{present_str}]. "
                f"Distance to expected position: {distance:.3f} (threshold: "
                f"{position_threshold:.3f}). This word may be missing a "
                f"{missing_label} label."
            ),
            word_context=ctx,
        )

    return None


def evaluate_word_contexts(
    word_contexts: List[WordContext],
    patterns: Optional[MerchantPatterns],
    visual_lines: Optional[List[VisualLine]] = None,
) -> List[EvaluationIssue]:
    """
    Apply all validation rules to word contexts and collect issues.

    Args:
        word_contexts: All WordContext objects for the receipt
        patterns: MerchantPatterns from other receipts (may be None)
        visual_lines: Visual lines for on-demand same-line lookup (optional)

    Returns:
        List of EvaluationIssue objects for all detected issues
    """
    issues: List[EvaluationIssue] = []

    for ctx in word_contexts:
        if ctx.current_label:
            # Check labeled words
            # NOTE: Removed noisy checks with high false positive rates:
            # - check_position_anomaly (position_anomaly)
            # - check_unexpected_label_pair (unexpected_label_pair: 100% FP)
            # - check_geometric_anomaly (geometric_anomaly: 89% FP)
            # - check_constellation_anomaly (constellation_anomaly: 89% FP)
            # - check_unexpected_label_multiplicity (89% FP, inside check_unexpected_label_pair)

            # Keep text-label conflict check (catches same text with different labels)
            issue = check_text_label_conflict(ctx, word_contexts, patterns)
            if issue:
                issues.append(issue)
                continue
        else:
            # Check unlabeled words
            # First check same-line cluster heuristic (requires visual_lines)
            # This has the best signal: 46% true positive rate
            if visual_lines:
                issue = check_missing_label_in_cluster(ctx, visual_lines)
                if issue:
                    issues.append(issue)
                    continue

            # Then check constellation-based missing label detection
            # This has 22% true positive rate - decent signal
            issue = check_missing_constellation_member(
                ctx, word_contexts, patterns
            )
            if issue:
                issues.append(issue)

    # Cap issues per type to prevent runaway processing on abnormal receipts
    # (e.g., 200 geometric_anomaly issues all for the same systematic reason)
    MAX_ISSUES_PER_TYPE = 20

    from collections import Counter

    type_counts = Counter(issue.issue_type for issue in issues)
    if any(count > MAX_ISSUES_PER_TYPE for count in type_counts.values()):
        capped_issues: List[EvaluationIssue] = []
        type_seen: dict[str, int] = {}

        for issue in issues:
            issue_type = issue.issue_type
            type_seen[issue_type] = type_seen.get(issue_type, 0) + 1

            if type_seen[issue_type] <= MAX_ISSUES_PER_TYPE:
                capped_issues.append(issue)

        logger.info(
            "Capped issues from %d to %d (max %d per type). Original counts: %s",
            len(issues),
            len(capped_issues),
            MAX_ISSUES_PER_TYPE,
            dict(type_counts),
        )
        return capped_issues

    return issues
