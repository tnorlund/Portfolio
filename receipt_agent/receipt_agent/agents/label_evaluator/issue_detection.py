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
import statistics
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from receipt_agent.agents.label_evaluator.state import (
    ConstellationGeometry,
    EvaluationIssue,
    LabelPairGeometry,
    MerchantPatterns,
    VisualLine,
    WordContext,
)
from receipt_agent.agents.label_evaluator.word_context import get_same_line_words
from receipt_agent.agents.label_evaluator.patterns import (
    _calculate_angle_degrees,
    _calculate_distance,
    _convert_polar_to_cartesian,
)

logger = logging.getLogger(__name__)


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
                f"appears at y={mean_y:.2f}\u00b1{std_y:.2f} for this "
                f"merchant (z-score={z_score:.1f})"
            ),
            word_context=ctx,
        )

    return None


def check_unexpected_label_pair(
    ctx: WordContext,
    all_contexts: List[WordContext],
    patterns: Optional[MerchantPatterns],
) -> Optional[EvaluationIssue]:
    """
    Check if a labeled word appears in an unexpected label pair combination.

    Flags when a label pair exists on the receipt but never appeared together
    in any of the training receipts from the same merchant. This can indicate
    mislabeling or unusual receipt structure.

    Args:
        ctx: WordContext to check
        all_contexts: All WordContext objects on the receipt
        patterns: MerchantPatterns from other receipts

    Returns:
        EvaluationIssue if unexpected label pair detected, None otherwise
    """
    if ctx.current_label is None or patterns is None:
        return None

    # Skip if we have very few training receipts (need confidence)
    if patterns.receipt_count < 2:
        return None

    label = ctx.current_label.label

    # Find all other labels present on this receipt
    other_labels = set()
    for other_ctx in all_contexts:
        if other_ctx is not ctx and other_ctx.current_label:
            other_labels.add(other_ctx.current_label.label)

    if not other_labels:
        return None

    # Check if any label pair is unexpected (never seen in training data)
    for other_label in other_labels:
        # Special handling for self-comparisons (same label appearing multiple
        # times)
        if label == other_label:
            # If this label type never appears multiple times in training data,
            # flag it
            if label not in patterns.labels_with_same_line_multiplicity:
                # Only flag if we have good training data
                if patterns.receipt_count >= 5:
                    return EvaluationIssue(
                        issue_type="unexpected_label_multiplicity",
                        word=ctx.word,
                        current_label=label,
                        suggested_status="NEEDS_REVIEW",
                        reasoning=(
                            f"'{ctx.word.text}' labeled {label} appears "
                            "multiple times on receipt, but "
                            f"{label} never appears multiple times in "
                            f"{patterns.receipt_count} training receipts for "
                            "this merchant. This may indicate mislabeling."
                        ),
                        word_context=ctx,
                    )
            # Label appears multiple times in training data, so this is
            # expected
            continue

        pair = tuple(sorted([label, other_label]))

        # If this pair never appeared in training receipts, it's unexpected
        if pair not in patterns.all_observed_pairs:
            # But be permissive: only flag if we have good training data
            # coverage
            # (need confidence from multiple receipts)
            if patterns.receipt_count >= 5:
                return EvaluationIssue(
                    issue_type="unexpected_label_pair",
                    word=ctx.word,
                    current_label=label,
                    suggested_status="NEEDS_REVIEW",
                    reasoning=(
                        f"'{ctx.word.text}' labeled {label} appears with "
                        f"{other_label}, a combination never seen in "
                        f"{patterns.receipt_count} training receipts for this "
                        "merchant. This may indicate mislabeling or unusual "
                        "structure."
                    ),
                    word_context=ctx,
                )

    return None


def _get_adaptive_threshold(geometry: "LabelPairGeometry") -> float:
    """
    Determine adaptive threshold based on pattern tightness.

    Tighter patterns (lower std deviation) -> stricter thresholds
    Looser patterns (higher std deviation) -> more lenient thresholds

    Args:
        geometry: The LabelPairGeometry to evaluate

    Returns:
        Threshold in standard deviations (1.5-2.5 sigma)
    """
    if geometry.std_deviation is None:
        return 2.0  # Default

    # Classify based on standard deviation
    if geometry.std_deviation < 0.1:
        return 1.5  # TIGHT pattern - be strict
    if geometry.std_deviation < 0.2:
        return 2.0  # MODERATE pattern - balanced
    return 2.5  # LOOSE pattern - be lenient


def check_geometric_anomaly(
    ctx: WordContext,
    all_contexts: List[WordContext],
    patterns: Optional[MerchantPatterns],
) -> Optional[EvaluationIssue]:
    """
    Check if a labeled word has geometric anomalies relative to other labels.

    Uses Cartesian coordinate space for robust anomaly detection that avoids
    angle wrap-around issues. Compares the deviation from expected position
    against learned patterns from the same merchant.

    With LLM batching enabled, checks patterns hierarchically:
    1. HAPPY patterns (conflict-free receipts): 1.5 sigma threshold (strict, HIGH
       confidence)
    2. AMBIGUOUS patterns (format variations): 2.0 sigma threshold (moderate,
       MEDIUM confidence)
    3. ANTI-PATTERN patterns (problematic receipts): 3.0 sigma threshold (lenient,
       LOW confidence)

    Without batching, uses adaptive thresholds based on pattern tightness:
    - TIGHT patterns (std < 0.1): 1.5 sigma threshold
    - MODERATE patterns (std 0.1-0.2): 2.0 sigma threshold
    - LOOSE patterns (std > 0.2): 2.5 sigma threshold

    Args:
        ctx: WordContext to check
        all_contexts: All WordContext objects on the receipt
        patterns: MerchantPatterns from other receipts (may be None)

    Returns:
        EvaluationIssue if geometric anomaly detected, None otherwise
    """
    if ctx.current_label is None or patterns is None:
        return None

    label = ctx.current_label.label

    # Find all other labels present on this receipt
    other_labels = set()
    for other_ctx in all_contexts:
        if other_ctx is not ctx and other_ctx.current_label:
            other_labels.add(other_ctx.current_label.label)

    if not other_labels:
        return None  # No other labels to compare against

    # Check if we have batch-specific patterns (LLM batching enabled)
    has_batch_patterns = (
        patterns.happy_label_pair_geometry
        or patterns.ambiguous_label_pair_geometry
        or patterns.anti_label_pair_geometry
    )

    if has_batch_patterns:
        # Use batch-specific patterns with hierarchical checking
        # First try HAPPY patterns (strict, high confidence)
        issue = _check_geometry_against_batch(
            ctx, all_contexts, patterns, "happy", threshold_multiplier=1.5
        )
        if issue:
            return issue

        # Then try AMBIGUOUS patterns (moderate, medium confidence)
        issue = _check_geometry_against_batch(
            ctx, all_contexts, patterns, "ambiguous", threshold_multiplier=2.0
        )
        if issue:
            return issue

        # Finally try ANTI-PATTERN patterns (lenient, low confidence)
        issue = _check_geometry_against_batch(
            ctx, all_contexts, patterns, "anti", threshold_multiplier=3.0
        )
        if issue:
            return issue
    else:
        # Use original patterns with adaptive thresholds (fallback if no
        # batching)
        if not patterns.label_pair_geometry:
            return None

        # Check geometry for each label pair
        for other_label in other_labels:
            pair = tuple(sorted([label, other_label]))

            # Check if we have learned geometry for this pair
            if pair not in patterns.label_pair_geometry:
                continue

            geometry = patterns.label_pair_geometry[pair]
            # Skip if we don't have Cartesian statistics (shouldn't happen with
            # new code)
            if (
                geometry.mean_dx is None
                or geometry.mean_dy is None
                or geometry.std_deviation is None
            ):
                continue

            # Get adaptive threshold based on pattern tightness
            threshold_std = _get_adaptive_threshold(geometry)

            issue = _compute_geometric_issue(
                ctx, all_contexts, label, other_label, geometry, threshold_std
            )
            if issue:
                return issue

    return None


def _check_geometry_against_batch(
    ctx: WordContext,
    all_contexts: List[WordContext],
    patterns: MerchantPatterns,
    batch_type: str,
    threshold_multiplier: float,
) -> Optional[EvaluationIssue]:
    """
    Check geometry against batch-specific patterns.

    Args:
        ctx: WordContext to check
        all_contexts: All WordContext objects on the receipt
        patterns: MerchantPatterns with batch-specific geometry
        batch_type: "happy", "ambiguous", or "anti"
        threshold_multiplier: Multiplier for standard deviations (1.5, 2.0, or
            3.0)

    Returns:
        EvaluationIssue if geometric anomaly detected, None otherwise
    """
    # Get the appropriate batch geometry dictionary
    if batch_type == "happy":
        batch_geometry = patterns.happy_label_pair_geometry
        confidence = "HIGH"
    elif batch_type == "ambiguous":
        batch_geometry = patterns.ambiguous_label_pair_geometry
        confidence = "MEDIUM"
    else:  # anti
        batch_geometry = patterns.anti_label_pair_geometry
        confidence = "LOW"

    if not batch_geometry:
        return None

    if ctx.current_label is None:
        return None
    label = ctx.current_label.label

    # Find all other labels present on this receipt
    other_labels = set()
    for other_ctx in all_contexts:
        if other_ctx is not ctx and other_ctx.current_label:
            other_labels.add(other_ctx.current_label.label)

    if not other_labels:
        return None

    # Check geometry for each label pair
    for other_label in other_labels:
        pair = tuple(sorted([label, other_label]))

        # Check if we have learned geometry for this batch
        if pair not in batch_geometry:
            continue

        geometry = batch_geometry[pair]
        # Skip if we don't have Cartesian statistics
        if (
            geometry.mean_dx is None
            or geometry.mean_dy is None
            or geometry.std_deviation is None
        ):
            continue

        # For batch-specific checks, use fixed threshold multiplier
        threshold_std = threshold_multiplier

        # Calculate actual geometry on this receipt (using word centroids)
        label_words = [
            c.word
            for c in all_contexts
            if c.current_label and c.current_label.label == label
        ]
        other_words = [
            c.word
            for c in all_contexts
            if c.current_label and c.current_label.label == other_label
        ]

        if not label_words or not other_words:
            continue

        # Calculate centroids
        x_coords_label = [w.calculate_centroid()[0] for w in label_words]
        y_coords_label = [w.calculate_centroid()[1] for w in label_words]
        centroid_label = (
            sum(x_coords_label) / len(x_coords_label),
            sum(y_coords_label) / len(y_coords_label),
        )

        x_coords_other = [w.calculate_centroid()[0] for w in other_words]
        y_coords_other = [w.calculate_centroid()[1] for w in other_words]
        centroid_other = (
            sum(x_coords_other) / len(x_coords_other),
            sum(y_coords_other) / len(y_coords_other),
        )

        # Calculate actual geometry
        actual_angle = _calculate_angle_degrees(centroid_label, centroid_other)
        actual_distance = _calculate_distance(centroid_label, centroid_other)

        # Convert to Cartesian coordinates
        actual_dx, actual_dy = _convert_polar_to_cartesian(
            actual_angle, actual_distance
        )

        # Compute deviation from expected position in Cartesian space
        deviation = math.sqrt(
            (actual_dx - geometry.mean_dx) ** 2
            + (actual_dy - geometry.mean_dy) ** 2
        )

        # Check if deviation is anomalous
        if geometry.std_deviation and geometry.std_deviation > 0:
            deviation_z_score = deviation / geometry.std_deviation
            if deviation_z_score > threshold_std:
                batch_label = (
                    "conflict-free (HAPPY)"
                    if batch_type == "happy"
                    else (
                        "format variation (AMBIGUOUS)"
                        if batch_type == "ambiguous"
                        else "problematic (ANTI_PATTERN)"
                    )
                )
                return EvaluationIssue(
                    issue_type="geometric_anomaly",
                    word=ctx.word,
                    current_label=label,
                    suggested_status="NEEDS_REVIEW",
                    reasoning=(
                        f"[{confidence} confidence] '{ctx.word.text}' labeled "
                        f"{label} has unusual geometric relationship with "
                        f"{other_label} (patterns from {batch_label} "
                        "receipts). Expected position "
                        f"({geometry.mean_dx:.2f}, {geometry.mean_dy:.2f}), "
                        f"actual ({actual_dx:.2f}, {actual_dy:.2f}), "
                        f"deviation "
                        f"{deviation:.3f} (threshold: {threshold_std}\u03c3 = "
                        f"{threshold_std * geometry.std_deviation:.3f}). "
                        "This may indicate mislabeling."
                    ),
                    word_context=ctx,
                )

    return None


def _compute_geometric_issue(
    ctx: WordContext,
    all_contexts: List[WordContext],
    label: str,
    other_label: str,
    geometry: LabelPairGeometry,
    threshold_std: float,
) -> Optional[EvaluationIssue]:
    """
    Helper to compute geometric issue for a given label pair.

    Args:
        ctx: WordContext to check
        all_contexts: All WordContext objects on the receipt
        label: Label of the word being checked
        other_label: Other label to compare against
        geometry: LabelPairGeometry pattern
        threshold_std: Threshold in standard deviations

    Returns:
        EvaluationIssue if anomaly detected, None otherwise
    """
    # Calculate actual geometry on this receipt (using word centroids)
    label_words = [
        c.word
        for c in all_contexts
        if c.current_label and c.current_label.label == label
    ]
    other_words = [
        c.word
        for c in all_contexts
        if c.current_label and c.current_label.label == other_label
    ]

    if not label_words or not other_words:
        return None

    # Calculate centroids
    x_coords_label = [w.calculate_centroid()[0] for w in label_words]
    y_coords_label = [w.calculate_centroid()[1] for w in label_words]
    centroid_label = (
        sum(x_coords_label) / len(x_coords_label),
        sum(y_coords_label) / len(y_coords_label),
    )

    x_coords_other = [w.calculate_centroid()[0] for w in other_words]
    y_coords_other = [w.calculate_centroid()[1] for w in other_words]
    centroid_other = (
        sum(x_coords_other) / len(x_coords_other),
        sum(y_coords_other) / len(y_coords_other),
    )

    # Calculate actual geometry
    actual_angle = _calculate_angle_degrees(centroid_label, centroid_other)
    actual_distance = _calculate_distance(centroid_label, centroid_other)

    # Convert to Cartesian coordinates
    actual_dx, actual_dy = _convert_polar_to_cartesian(
        actual_angle, actual_distance
    )

    # Compute deviation from expected position in Cartesian space
    if geometry.mean_dx is None or geometry.mean_dy is None:
        return None
    deviation = math.sqrt(
        (actual_dx - geometry.mean_dx) ** 2
        + (actual_dy - geometry.mean_dy) ** 2
    )

    # Check if deviation is anomalous
    if geometry.std_deviation and geometry.std_deviation > 0:
        deviation_z_score = deviation / geometry.std_deviation
        if deviation_z_score > threshold_std:
            return EvaluationIssue(
                issue_type="geometric_anomaly",
                word=ctx.word,
                current_label=label,
                suggested_status="NEEDS_REVIEW",
                reasoning=(
                    f"'{ctx.word.text}' labeled {label} has unusual geometric "
                    f"relationship with {other_label}. Expected position "
                    f"({geometry.mean_dx:.2f}, {geometry.mean_dy:.2f}), "
                    f"actual ({actual_dx:.2f}, {actual_dy:.2f}), "
                    f"deviation {deviation:.3f} (adaptive threshold: "
                    f"{threshold_std}\u03c3 = "
                    f"{threshold_std * geometry.std_deviation:.3f}). "
                    "This may indicate mislabeling."
                ),
                word_context=ctx,
            )

    return None


def check_constellation_anomaly(
    ctx: WordContext,
    all_contexts: List[WordContext],
    patterns: Optional[MerchantPatterns],
    threshold_std: float = 2.0,
) -> Optional[EvaluationIssue]:
    """
    Check if a labeled word is anomalously positioned within its constellation.

    Unlike pairwise checks that only examine A<->B relationships, constellation
    checks examine the holistic structure of label groups (n-tuples).

    For each constellation the word's label belongs to:
    1. Check if all labels in the constellation are present on the receipt
    2. Compute the constellation centroid from actual positions
    3. Check if this word's offset from centroid matches expected pattern

    This catches anomalies that pairwise checks miss:
    - A-B ok, B-C ok, but A is displaced relative to the group
    - Cluster is stretched/compressed
    - One label missing from expected group

    Args:
        ctx: WordContext to check
        all_contexts: All WordContext objects on the receipt
        patterns: MerchantPatterns with constellation_geometry
        threshold_std: Standard deviations for anomaly detection

    Returns:
        EvaluationIssue if constellation anomaly detected, None otherwise
    """
    if ctx.current_label is None or patterns is None:
        return None

    if not patterns.constellation_geometry:
        return None

    label = ctx.current_label.label

    # Find constellations that include this label
    relevant_constellations = [
        (constellation, geom)
        for constellation, geom in patterns.constellation_geometry.items()
        if label in constellation
    ]

    if not relevant_constellations:
        return None

    # Build label -> centroid mapping for this receipt
    label_centroids: Dict[str, Tuple[float, float]] = {}
    labels_by_type: Dict[str, List[WordContext]] = defaultdict(list)

    for other_ctx in all_contexts:
        if other_ctx.current_label:
            labels_by_type[other_ctx.current_label.label].append(other_ctx)

    for label_type, contexts in labels_by_type.items():
        if contexts:
            centroids = [c.word.calculate_centroid() for c in contexts]
            label_centroids[label_type] = (
                sum(c[0] for c in centroids) / len(centroids),
                sum(c[1] for c in centroids) / len(centroids),
            )

    # Check each relevant constellation
    for constellation, geom in relevant_constellations:
        # Check if all labels in constellation are present
        if not all(lbl in label_centroids for lbl in constellation):
            continue

        # Compute actual constellation centroid
        all_x = [label_centroids[lbl][0] for lbl in constellation]
        all_y = [label_centroids[lbl][1] for lbl in constellation]
        actual_centroid = (
            sum(all_x) / len(all_x),
            sum(all_y) / len(all_y),
        )

        # Get this label's position and expected relative position
        actual_pos = label_centroids[label]
        actual_dx = actual_pos[0] - actual_centroid[0]
        actual_dy = actual_pos[1] - actual_centroid[1]

        expected = geom.relative_positions.get(label)
        if expected is None or expected.std_deviation is None:
            continue
        if expected.std_deviation <= 0:
            continue

        # Compute deviation from expected position
        deviation = math.sqrt(
            (actual_dx - expected.mean_dx) ** 2
            + (actual_dy - expected.mean_dy) ** 2
        )

        z_score = deviation / expected.std_deviation
        if z_score > threshold_std:
            constellation_str = " + ".join(constellation)
            return EvaluationIssue(
                issue_type="constellation_anomaly",
                word=ctx.word,
                current_label=label,
                suggested_status="NEEDS_REVIEW",
                reasoning=(
                    f"'{ctx.word.text}' labeled {label} has unusual position "
                    f"within constellation [{constellation_str}]. Expected "
                    f"offset ({expected.mean_dx:.3f}, "
                    f"{expected.mean_dy:.3f}) from cluster center, actual "
                    f"({actual_dx:.3f}, {actual_dy:.3f}). Deviation "
                    f"{deviation:.3f} exceeds {threshold_std}\u03c3 threshold "
                    f"({threshold_std * expected.std_deviation:.3f}). This "
                    "suggests the label may be misplaced relative to its "
                    "group."
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
        return text.replace(".", "").isdigit() or text_lower in (
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

        # Compute the constellation centroid from present labels
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
            issue = check_position_anomaly(ctx, patterns)
            if issue:
                issues.append(issue)
                continue  # One issue per word

            # Check for unexpected label pair combinations first
            issue = check_unexpected_label_pair(ctx, word_contexts, patterns)
            if issue:
                issues.append(issue)
                continue

            # Use geometric anomaly detection instead of simple same-line
            # conflict
            issue = check_geometric_anomaly(ctx, word_contexts, patterns)
            if issue:
                issues.append(issue)
                continue

            # Check constellation anomaly (holistic n-tuple relationships)
            issue = check_constellation_anomaly(ctx, word_contexts, patterns)
            if issue:
                issues.append(issue)
                continue

            issue = check_text_label_conflict(ctx, word_contexts, patterns)
            if issue:
                issues.append(issue)
                continue
        else:
            # Check unlabeled words
            # First check same-line cluster heuristic (requires visual_lines)
            if visual_lines:
                issue = check_missing_label_in_cluster(ctx, visual_lines)
                if issue:
                    issues.append(issue)
                    continue

            # Then check constellation-based missing label detection
            issue = check_missing_constellation_member(
                ctx, word_contexts, patterns
            )
            if issue:
                issues.append(issue)

    return issues
