"""
Helper functions for the Label Evaluator agent.

Provides spatial analysis utilities for grouping words into visual lines,
computing label patterns across receipts, and applying validation rules.
"""

import logging
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel

from receipt_agent.agents.label_evaluator.state import (
    ConstellationGeometry,
    EvaluationIssue,
    GeometricRelationship,
    LabelPairGeometry,
    LabelRelativePosition,
    MerchantPatterns,
    OtherReceiptData,
    ReviewContext,
    VisualLine,
    WordContext,
)

logger = logging.getLogger(__name__)

# Configuration for limiting label pair geometry computation
MAX_LABEL_PAIRS = 4  # Only compute geometry for top N label type pairs
MAX_RELATIONSHIP_DIMENSION = (
    2  # Analyze relationships between N labels (2=pairs, 3=triples, etc.)
)

# Semantic groupings of related labels for better pattern understanding
LABEL_GROUPS = {
    "header": {
        "MERCHANT_NAME",
        "STORE_HOURS",
        "PHONE_NUMBER",
        "WEBSITE",
        "LOYALTY_ID",
        "ADDRESS_LINE",
    },
    "metadata": {"DATE", "TIME", "PAYMENT_METHOD"},
    "line_items": {"PRODUCT_NAME", "QUANTITY", "UNIT_PRICE", "LINE_TOTAL"},
    "discounts": {"COUPON", "DISCOUNT"},
    "totals": {"SUBTOTAL", "TAX", "GRAND_TOTAL"},
}

# Map each label to its group
LABEL_TO_GROUP: Dict[str, str] = {}
for group_name, labels in LABEL_GROUPS.items():
    for label in labels:
        LABEL_TO_GROUP[label] = group_name

# Priority within-group pairs (relationships within semantic groups)
WITHIN_GROUP_PRIORITY_PAIRS = {
    # Line item internal structure
    ("PRODUCT_NAME", "UNIT_PRICE"),
    ("UNIT_PRICE", "PRODUCT_NAME"),
    ("QUANTITY", "LINE_TOTAL"),
    ("LINE_TOTAL", "QUANTITY"),
    ("PRODUCT_NAME", "QUANTITY"),
    ("QUANTITY", "PRODUCT_NAME"),
    # Totals section structure
    ("SUBTOTAL", "TAX"),
    ("TAX", "SUBTOTAL"),
    ("TAX", "GRAND_TOTAL"),
    ("GRAND_TOTAL", "TAX"),
}

# Priority cross-group pairs (relationships between different semantic groups)
CROSS_GROUP_PRIORITY_PAIRS = {
    # Line items to totals
    ("LINE_TOTAL", "SUBTOTAL"),
    ("SUBTOTAL", "LINE_TOTAL"),
    ("LINE_TOTAL", "GRAND_TOTAL"),
    ("GRAND_TOTAL", "LINE_TOTAL"),
    # Discounts to totals
    ("DISCOUNT", "SUBTOTAL"),
    ("SUBTOTAL", "DISCOUNT"),
    ("COUPON", "LINE_TOTAL"),
    ("LINE_TOTAL", "COUPON"),
}

# Conflicting label pairs that should NEVER appear on the same word position
# These indicate data quality issues in training labels
CONFLICTING_LABEL_PAIRS = {
    # Line item quantity/price pairs (a price can't be both unit and line total for same item)
    ("QUANTITY", "UNIT_PRICE"),
    ("QUANTITY", "LINE_TOTAL"),
    ("UNIT_PRICE", "LINE_TOTAL"),
    # Semantic impossibilities
    ("PRODUCT_NAME", "QUANTITY"),
    ("PRODUCT_NAME", "UNIT_PRICE"),
    ("PRODUCT_NAME", "LINE_TOTAL"),
}


# Geometry helpers
def _calculate_angle_degrees(
    from_point: Tuple[float, float],
    to_point: Tuple[float, float],
) -> float:
    """
    Calculate angle in degrees from one point to another.

    Args:
        from_point: (x, y) starting point
        to_point: (x, y) ending point

    Returns:
        Angle in degrees (0-360), where:
        - 0° = directly right
        - 90° = directly down
        - 180° = directly left
        - 270° = directly up
    """
    dx = to_point[0] - from_point[0]
    dy = to_point[1] - from_point[1]

    # Note: y increases downward (0=bottom, 1=top in receipt coords)
    # atan2(dy, dx) gives angle with x=right as 0°
    radians = math.atan2(dy, dx)
    degrees = math.degrees(radians)

    # Normalize to 0-360 range
    if degrees < 0:
        degrees += 360

    return degrees


def _calculate_distance(
    point1: Tuple[float, float],
    point2: Tuple[float, float],
) -> float:
    """
    Calculate Euclidean distance between two points.

    Args:
        point1: (x, y) first point
        point2: (x, y) second point

    Returns:
        Distance (in normalized 0-1 coordinate space, diagonal ≈ 1.41)
    """
    dx = point2[0] - point1[0]
    dy = point2[1] - point1[1]
    return math.sqrt(dx * dx + dy * dy)


def _angle_difference(angle1: float, angle2: float) -> float:
    """
    Calculate shortest angular distance between two angles.

    Args:
        angle1, angle2: Angles in degrees (0-360)

    Returns:
        Shortest angular distance (0-180 degrees)
    """
    diff = abs(angle1 - angle2)
    if diff > 180:
        diff = 360 - diff
    return diff


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
    labels_by_word: Dict[Tuple[int, int], List[ReceiptWordLabel]] = (
        defaultdict(list)
    )
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


def _is_within_group_pair(pair: Tuple[str, str]) -> bool:
    """Check if a pair is within the same semantic group."""
    label1, label2 = pair
    group1 = LABEL_TO_GROUP.get(label1)
    group2 = LABEL_TO_GROUP.get(label2)
    return group1 is not None and group1 == group2


def _is_cross_group_pair(pair: Tuple[str, str]) -> bool:
    """Check if a pair spans different semantic groups."""
    label1, label2 = pair
    group1 = LABEL_TO_GROUP.get(label1)
    group2 = LABEL_TO_GROUP.get(label2)
    return group1 is not None and group2 is not None and group1 != group2


def _select_top_label_pairs(
    all_pairs: Dict[Tuple[str, str], int],
    max_pairs: int = MAX_LABEL_PAIRS,
) -> Set[Tuple[str, str]]:
    """
    Select top label type pairs for geometry computation.

    Intelligently selects pairs based on:
    - Semantic importance (priority pairs within/across groups)
    - Data frequency (how often pairs appear together)
    - Diversity (mix within-group and cross-group patterns from actual data)

    Approach:
    1. Separate pairs into within-group and cross-group based on semantic groups
    2. Prioritize important pairs that exist in the data
    3. Fill slots with most frequent pairs, naturally balancing types
    4. Let the data distribution determine the final mix

    Args:
        all_pairs: Dict mapping (label1, label2) to frequency count
        max_pairs: Maximum number of pairs to select (default: 4)

    Returns:
        Set of selected label type pairs (sorted tuples)
    """
    selected: Set[Tuple[str, str]] = set()

    # Separate all pairs into categories
    within_group_pairs = []
    cross_group_pairs = []
    other_pairs = []

    for pair in all_pairs.keys():
        sorted_pair = sorted(pair)
        normalized: Tuple[str, str] = (sorted_pair[0], sorted_pair[1])
        if _is_within_group_pair(pair):
            within_group_pairs.append((normalized, all_pairs[pair]))
        elif _is_cross_group_pair(pair):
            cross_group_pairs.append((normalized, all_pairs[pair]))
        else:
            other_pairs.append((normalized, all_pairs[pair]))

    # Sort by frequency (most common first)
    within_group_pairs.sort(key=lambda x: -x[1])
    cross_group_pairs.sort(key=lambda x: -x[1])
    other_pairs.sort(key=lambda x: -x[1])

    # Pass 1: Add priority pairs (these are important regardless of frequency)
    for pair, freq in within_group_pairs:
        if len(selected) >= max_pairs:
            break
        if pair in WITHIN_GROUP_PRIORITY_PAIRS and pair not in selected:
            selected.add(pair)

    for pair, freq in cross_group_pairs:
        if len(selected) >= max_pairs:
            break
        if pair in CROSS_GROUP_PRIORITY_PAIRS and pair not in selected:
            selected.add(pair)

    # Pass 2: Fill remaining slots with most frequent pairs (any type)
    # This naturally preserves the data's distribution
    combined = sorted(
        [
            (p, f)
            for p, f in within_group_pairs + cross_group_pairs + other_pairs
            if p not in selected
        ],
        key=lambda x: -x[1],
    )

    for pair, freq in combined:
        if len(selected) >= max_pairs:
            break
        if pair not in selected:
            selected.add(pair)

    # Categorize final selection and log
    if selected:
        within_group_count = sum(
            1 for p in selected if _is_within_group_pair(p)
        )
        cross_group_count = sum(1 for p in selected if _is_cross_group_pair(p))

        pair_info = []
        for p in sorted(selected):
            if _is_within_group_pair(p):
                pair_info.append(f"{p} [within-{LABEL_TO_GROUP.get(p[0])}]")
            elif _is_cross_group_pair(p):
                pair_info.append(
                    f"{p} [cross: {LABEL_TO_GROUP.get(p[0])}↔{LABEL_TO_GROUP.get(p[1])}]"
                )
            else:
                pair_info.append(f"{p} [ungrouped]")

        logger.info(
            f"Selected {len(selected)} label pairs for geometry computation "
            f"({within_group_count} within-group, {cross_group_count} cross-group):"
        )
        for info in pair_info:
            logger.info(f"  - {info}")

    return selected


def _select_top_label_ntuples(
    all_ntuples: Dict[Tuple[str, ...], int],
    max_ntuples: int = 4,
) -> Set[Tuple[str, ...]]:
    """
    Select top label n-tuples by frequency.

    For n-tuples (dimension >= 3), we use a simpler frequency-based approach
    rather than semantic grouping, since semantic relationships become complex.

    Args:
        all_ntuples: Dict mapping n-tuple to frequency count
        max_ntuples: Maximum number of n-tuples to select

    Returns:
        Set of selected n-tuples
    """
    if not all_ntuples:
        return set()

    # Sort by frequency and select top N
    sorted_ntuples = sorted(all_ntuples.items(), key=lambda x: -x[1])
    selected = {ntuple for ntuple, _ in sorted_ntuples[:max_ntuples]}

    logger.debug(
        f"Selected {len(selected)} label n-tuples by frequency "
        f"(top: {', '.join(str(nt) for nt, freq in sorted_ntuples[:3])})"
    )

    return selected


def _generate_label_ntuples(
    all_pairs: Dict[Tuple[str, str], int],
    dimension: int = 2,
) -> Dict[Tuple[str, ...], int]:
    """
    Generate n-tuples of labels from pair co-occurrence data.

    For dimension=2: Returns pairs as-is
    For dimension=3+: Identifies labels that co-occur and groups them into n-tuples

    Args:
        all_pairs: Dict mapping (label1, label2) to co-occurrence frequency
        dimension: Size of n-tuples to generate (2=pairs, 3=triples, etc.)

    Returns:
        Dict mapping n-tuple to frequency count
    """
    if dimension < 2:
        raise ValueError("dimension must be >= 2")

    if dimension == 2:
        # For pairs, just return the input as-is
        return all_pairs

    # For dimension >= 3, build a co-occurrence graph and find cliques
    # A clique is a set of labels that all co-occur with each other

    # Build adjacency list from pairs
    label_neighbors: Dict[str, Set[str]] = defaultdict(set)
    for (label_a, label_b), _freq in all_pairs.items():
        label_neighbors[label_a].add(label_b)
        label_neighbors[label_b].add(label_a)

    # Generate all possible n-tuples and check if they form cliques
    ntuples: Dict[Tuple[str, ...], int] = defaultdict(int)
    all_labels = sorted(label_neighbors.keys())

    for ntuple in combinations(all_labels, dimension):
        # Check if this n-tuple is a clique (all pairs exist)
        # Count it with the minimum frequency of all pairs within
        is_clique = True
        min_freq = float("inf")

        for i, label_a in enumerate(ntuple):
            for label_b in ntuple[i + 1 :]:
                pair = tuple(sorted([label_a, label_b]))
                if pair not in all_pairs:
                    is_clique = False
                    break
                min_freq = min(min_freq, all_pairs[pair])
            if not is_clique:
                break

        if is_clique and min_freq > 0:
            sorted_tuple = tuple(sorted(ntuple))
            ntuples[sorted_tuple] = min_freq

    return ntuples


def _convert_polar_to_cartesian(
    angle_degrees: float,
    distance: float,
) -> Tuple[float, float]:
    """
    Convert polar coordinates (angle, distance) to Cartesian (dx, dy).

    Args:
        angle_degrees: Angle in degrees (0-360)
        distance: Euclidean distance (0-1 normalized)

    Returns:
        Tuple of (dx, dy) in Cartesian space
    """
    angle_radians = math.radians(angle_degrees)
    dx = distance * math.cos(angle_radians)
    dy = distance * math.sin(angle_radians)
    return (dx, dy)


def _print_pattern_statistics(
    patterns: MerchantPatterns,
    merchant_name: str,
) -> None:
    """
    Print detailed statistics for learned patterns.

    Shows for each label pair:
    - Number of observations
    - Mean angle and distance with standard deviation
    - Outlier detection ranges (±1.5 std dev)
    - Pattern tightness assessment

    Args:
        patterns: The learned MerchantPatterns
        merchant_name: Name of merchant for logging context
    """
    logger.info(f"\n{'='*80}")
    logger.info(f"PATTERN STATISTICS FOR {merchant_name}")
    logger.info(f"{'='*80}")
    logger.info(f"Training data: {patterns.receipt_count} receipts")
    logger.info(f"Label types observed: {len(patterns.label_positions)}")
    logger.info(
        f"Label pairs with geometry: {len(patterns.label_pair_geometry)}\n"
    )

    if not patterns.label_pair_geometry:
        logger.info("No geometric patterns to display.")
        return

    for pair in sorted(patterns.label_pair_geometry.keys()):
        geometry = patterns.label_pair_geometry[pair]
        obs_count = len(geometry.observations)

        logger.info(f"\n{pair[0]} ↔ {pair[1]}:")
        logger.info(f"  Observations: {obs_count}")

        if geometry.mean_angle is not None:
            mean_angle = geometry.mean_angle
            std_angle = geometry.std_angle or 0.0
            mean_distance = geometry.mean_distance or 0.0
            std_distance = geometry.std_distance or 0.0

            # Tightness assessment based on std dev
            if std_angle < 10:
                tightness = "VERY TIGHT"
            elif std_angle < 20:
                tightness = "TIGHT"
            elif std_angle < 40:
                tightness = "MODERATE"
            else:
                tightness = "LOOSE"

            logger.info(f"  [POLAR COORDINATES]")
            logger.info(
                f"  Angle: mean={mean_angle:.1f}°, std={std_angle:.1f}° [{tightness}]"
            )
            logger.info(
                f"    Range (±1.5σ): {(mean_angle - 1.5 * std_angle) % 360:.1f}° "
                f"to {(mean_angle + 1.5 * std_angle) % 360:.1f}°"
            )

            logger.info(
                f"  Distance: mean={mean_distance:.3f}, std={std_distance:.3f}"
            )
            logger.info(
                f"    Range (±1.5σ): {max(0, mean_distance - 1.5 * std_distance):.3f} "
                f"to {min(1.0, mean_distance + 1.5 * std_distance):.3f}"
            )

            # Convert observations to Cartesian coordinates for analysis
            logger.info(f"\n  [CARTESIAN COORDINATES]")
            cartesian_coords = [
                _convert_polar_to_cartesian(obs.angle, obs.distance)
                for obs in geometry.observations
            ]

            # Compute Cartesian statistics
            dx_values = [coord[0] for coord in cartesian_coords]
            dy_values = [coord[1] for coord in cartesian_coords]

            mean_dx = statistics.mean(dx_values)
            mean_dy = statistics.mean(dy_values)

            if len(dx_values) >= 2:
                std_dx = statistics.stdev(dx_values)
                std_dy = statistics.stdev(dy_values)
            else:
                std_dx = 0.0
                std_dy = 0.0

            # Compute Cartesian tightness (distance from mean in 2D space)
            mean_cartesian_distance = math.sqrt(mean_dx**2 + mean_dy**2)

            # Compute distances of each observation from the mean
            deviations = [
                math.sqrt((dx - mean_dx) ** 2 + (dy - mean_dy) ** 2)
                for dx, dy in cartesian_coords
            ]
            mean_deviation = statistics.mean(deviations)
            if len(deviations) >= 2:
                std_deviation = statistics.stdev(deviations)
            else:
                std_deviation = 0.0

            # Cartesian tightness assessment
            if std_deviation < 0.05:
                cart_tightness = "VERY TIGHT"
            elif std_deviation < 0.1:
                cart_tightness = "TIGHT"
            elif std_deviation < 0.2:
                cart_tightness = "MODERATE"
            else:
                cart_tightness = "LOOSE"

            logger.info(f"  dx: mean={mean_dx:+.3f}, std={std_dx:.3f}")
            logger.info(f"  dy: mean={mean_dy:+.3f}, std={std_dy:.3f}")
            logger.info(
                f"  Mean position: ({mean_dx:+.3f}, {mean_dy:+.3f}) [distance from origin: {mean_cartesian_distance:.3f}]"
            )
            logger.info(
                f"  Deviation from mean: mean={mean_deviation:.3f}, std={std_deviation:.3f} [{cart_tightness}]"
            )
            logger.info(
                f"    Outlier range (±1.5σ): {max(0, mean_deviation - 1.5 * std_deviation):.3f} "
                f"to {mean_deviation + 1.5 * std_deviation:.3f}"
            )

            # Sample sizes assessment
            if obs_count < 5:
                logger.info(
                    f"\n  ⚠️  WARNING: Only {obs_count} observations - pattern may be unreliable"
                )
            elif obs_count < 10:
                logger.info(
                    f"\n  ⚠️  Note: {obs_count} observations - limited confidence"
                )
            else:
                logger.info(f"\n  ✓ Sufficient observations ({obs_count})")

    logger.info(f"\n{'='*80}\n")


def _compute_patterns_for_subset(
    receipts: List[OtherReceiptData],
) -> Dict[Tuple[str, str], LabelPairGeometry]:
    """
    Compute label pair geometry patterns from a subset of receipts.

    Used for batch-specific pattern learning (HAPPY, AMBIGUOUS, ANTI).

    Args:
        receipts: Subset of receipts to analyze

    Returns:
        Dict mapping (label1, label2) pairs to LabelPairGeometry objects
    """
    geometry_dict: Dict[Tuple[str, str], LabelPairGeometry] = {}

    for receipt_data in receipts:
        words = receipt_data.words
        labels = receipt_data.labels

        # Build word lookup
        word_by_id: Dict[Tuple[int, int], ReceiptWord] = {
            (w.line_id, w.word_id): w for w in words
        }

        # Get most recent label per word, preferring VALID ones
        labels_by_word: Dict[Tuple[int, int], List[ReceiptWordLabel]] = (
            defaultdict(list)
        )
        for label in labels:
            key = (label.line_id, label.word_id)
            labels_by_word[key].append(label)

        current_labels: Dict[Tuple[int, int], ReceiptWordLabel] = {}
        for key, label_list in labels_by_word.items():
            label_list.sort(
                key=lambda lbl: (
                    lbl.timestamp_added.isoformat()
                    if hasattr(lbl.timestamp_added, "isoformat")
                    else str(lbl.timestamp_added)
                ),
                reverse=True,
            )
            valid_labels = [
                lbl for lbl in label_list if lbl.validation_status == "VALID"
            ]
            if valid_labels:
                current_labels[key] = valid_labels[0]
            elif label_list:
                current_labels[key] = label_list[0]

        # Group words by label type to get centroids
        labels_by_type: Dict[str, List[ReceiptWord]] = defaultdict(list)
        for key, label in current_labels.items():
            word = word_by_id.get(key)
            if word:
                labels_by_type[label.label].append(word)

        # Compute geometry for label pairs on same line
        labels_by_line: Dict[int, List[str]] = defaultdict(list)
        for key, label in current_labels.items():
            labels_by_line[key[0]].append(label.label)

        # Track unique pairs on same line
        for line_labels in labels_by_line.values():
            unique_labels = list(set(line_labels))
            for i, label_a in enumerate(unique_labels):
                for label_b in unique_labels[i + 1 :]:
                    sorted_labels = sorted([label_a, label_b])
                    pair: Tuple[str, str] = (
                        sorted_labels[0],
                        sorted_labels[1],
                    )

                    # Get centroids for this pair
                    words_a = labels_by_type[label_a]
                    words_b = labels_by_type[label_b]

                    if words_a and words_b:
                        # Calculate centroids from word positions
                        centroids_a = [w.calculate_centroid() for w in words_a]
                        centroid_a = (
                            sum(c[0] for c in centroids_a) / len(centroids_a),
                            sum(c[1] for c in centroids_a) / len(centroids_a),
                        )
                        centroids_b = [w.calculate_centroid() for w in words_b]
                        centroid_b = (
                            sum(c[0] for c in centroids_b) / len(centroids_b),
                            sum(c[1] for c in centroids_b) / len(centroids_b),
                        )

                        if pair not in geometry_dict:
                            geometry_dict[pair] = LabelPairGeometry()

                        angle = _calculate_angle_degrees(
                            centroid_a, centroid_b
                        )
                        distance = _calculate_distance(centroid_a, centroid_b)

                        geometry_dict[pair].observations.append(
                            GeometricRelationship(
                                angle=angle,
                                distance=distance,
                            )
                        )

    # Compute statistics for each pair
    for pair, geometry in geometry_dict.items():
        if geometry.observations:
            angles = [obs.angle for obs in geometry.observations]
            distances = [obs.distance for obs in geometry.observations]

            geometry.mean_angle = statistics.mean(angles)
            geometry.std_angle = (
                statistics.stdev(angles) if len(angles) >= 2 else 0.0
            )
            geometry.mean_distance = statistics.mean(distances)
            geometry.std_distance = (
                statistics.stdev(distances) if len(distances) >= 2 else 0.0
            )

            # Cartesian statistics
            cartesian_coords = [
                _convert_polar_to_cartesian(obs.angle, obs.distance)
                for obs in geometry.observations
            ]

            dx_values = [coord[0] for coord in cartesian_coords]
            dy_values = [coord[1] for coord in cartesian_coords]

            geometry.mean_dx = statistics.mean(dx_values)
            geometry.mean_dy = statistics.mean(dy_values)
            geometry.std_dx = (
                statistics.stdev(dx_values) if len(dx_values) >= 2 else 0.0
            )
            geometry.std_dy = (
                statistics.stdev(dy_values) if len(dy_values) >= 2 else 0.0
            )

            deviations = [
                math.sqrt(
                    (dx - geometry.mean_dx) ** 2 + (dy - geometry.mean_dy) ** 2
                )
                for dx, dy in cartesian_coords
            ]
            geometry.mean_deviation = statistics.mean(deviations)
            geometry.std_deviation = (
                statistics.stdev(deviations) if len(deviations) >= 2 else 0.0
            )

    return geometry_dict


def _compute_constellation_patterns(
    receipts: List[OtherReceiptData],
    constellations: List[Tuple[str, ...]],
    min_observations: int = 3,
) -> Dict[Tuple[str, ...], ConstellationGeometry]:
    """
    Compute constellation geometry patterns from receipts.

    For each constellation (n-tuple of labels), computes:
    - Relative position of each label to the constellation centroid
    - Bounding box statistics (width, height, aspect ratio)
    - Constellation centroid position on receipt

    Args:
        receipts: List of receipts to analyze
        constellations: List of label tuples to compute patterns for
        min_observations: Minimum receipts needed for valid pattern

    Returns:
        Dict mapping constellation tuple to ConstellationGeometry
    """
    # Initialize storage for observations
    # constellation -> list of observations
    # Each observation is a dict:
    #   - label_positions: {label: (x, y)} for each label in constellation
    #   - centroid: (x, y) of constellation center
    #   - bbox: (width, height)
    constellation_observations: Dict[Tuple[str, ...], List[Dict[str, Any]]] = (
        defaultdict(list)
    )

    for receipt_data in receipts:
        words = receipt_data.words
        labels = receipt_data.labels

        # Build word lookup
        word_by_id: Dict[Tuple[int, int], ReceiptWord] = {
            (w.line_id, w.word_id): w for w in words
        }

        # Get most recent valid label per word
        labels_by_word: Dict[Tuple[int, int], List[ReceiptWordLabel]] = (
            defaultdict(list)
        )
        for label in labels:
            key = (label.line_id, label.word_id)
            labels_by_word[key].append(label)

        current_labels: Dict[Tuple[int, int], ReceiptWordLabel] = {}
        for key, label_list in labels_by_word.items():
            label_list.sort(
                key=lambda lbl: (
                    lbl.timestamp_added.isoformat()
                    if hasattr(lbl.timestamp_added, "isoformat")
                    else str(lbl.timestamp_added)
                ),
                reverse=True,
            )
            valid_labels = [
                lbl for lbl in label_list if lbl.validation_status == "VALID"
            ]
            if valid_labels:
                current_labels[key] = valid_labels[0]
            elif label_list:
                current_labels[key] = label_list[0]

        # Group words by label type and compute centroids
        label_centroids: Dict[str, Tuple[float, float]] = {}
        labels_by_type: Dict[str, List[ReceiptWord]] = defaultdict(list)

        for key, label in current_labels.items():
            word = word_by_id.get(key)
            if word:
                labels_by_type[label.label].append(word)

        # Compute centroid for each label type
        for label_type, label_words in labels_by_type.items():
            if label_words:
                centroids = [w.calculate_centroid() for w in label_words]
                label_centroids[label_type] = (
                    sum(c[0] for c in centroids) / len(centroids),
                    sum(c[1] for c in centroids) / len(centroids),
                )

        # Check each constellation
        for constellation in constellations:
            # Check if all labels in constellation are present
            if not all(lbl in label_centroids for lbl in constellation):
                continue

            # Get positions of all labels in this constellation
            positions = {lbl: label_centroids[lbl] for lbl in constellation}

            # Compute constellation centroid
            all_x = [pos[0] for pos in positions.values()]
            all_y = [pos[1] for pos in positions.values()]
            centroid = (
                sum(all_x) / len(all_x),
                sum(all_y) / len(all_y),
            )

            # Compute bounding box
            min_x, max_x = min(all_x), max(all_x)
            min_y, max_y = min(all_y), max(all_y)
            width = max_x - min_x
            height = max_y - min_y

            # Store observation
            constellation_observations[constellation].append(
                {
                    "label_positions": positions,
                    "centroid": centroid,
                    "width": width,
                    "height": height,
                }
            )

    # Compute statistics for each constellation
    result: Dict[Tuple[str, ...], ConstellationGeometry] = {}

    for constellation, observations in constellation_observations.items():
        if len(observations) < min_observations:
            continue

        # Initialize ConstellationGeometry
        geom = ConstellationGeometry(
            labels=constellation,
            observation_count=len(observations),
            relative_positions={},
        )

        # Compute relative position statistics for each label
        for lbl_type in constellation:
            # Collect relative positions (offset from constellation centroid)
            dx_values: List[float] = []
            dy_values: List[float] = []

            for obs in observations:
                centroid = obs["centroid"]
                pos = obs["label_positions"][lbl_type]
                dx_values.append(pos[0] - centroid[0])
                dy_values.append(pos[1] - centroid[1])

            # Compute statistics
            mean_dx = statistics.mean(dx_values)
            mean_dy = statistics.mean(dy_values)
            std_dx = (
                statistics.stdev(dx_values) if len(dx_values) >= 2 else 0.0
            )
            std_dy = (
                statistics.stdev(dy_values) if len(dy_values) >= 2 else 0.0
            )

            # Compute combined deviation metric
            deviations = [
                math.sqrt((dx - mean_dx) ** 2 + (dy - mean_dy) ** 2)
                for dx, dy in zip(dx_values, dy_values)
            ]
            std_deviation = (
                statistics.stdev(deviations) if len(deviations) >= 2 else 0.0
            )

            geom.relative_positions[lbl_type] = LabelRelativePosition(
                mean_dx=mean_dx,
                mean_dy=mean_dy,
                std_dx=std_dx,
                std_dy=std_dy,
                std_deviation=std_deviation,
            )

        # Compute bounding box statistics
        widths = [obs["width"] for obs in observations]
        heights = [obs["height"] for obs in observations]

        geom.mean_width = statistics.mean(widths)
        geom.mean_height = statistics.mean(heights)
        geom.std_width = statistics.stdev(widths) if len(widths) >= 2 else 0.0
        geom.std_height = (
            statistics.stdev(heights) if len(heights) >= 2 else 0.0
        )

        # Aspect ratio (avoid division by zero)
        aspect_ratios = [
            w / h if h > 0.001 else 0.0 for w, h in zip(widths, heights)
        ]
        if aspect_ratios:
            geom.mean_aspect_ratio = statistics.mean(aspect_ratios)
            geom.std_aspect_ratio = (
                statistics.stdev(aspect_ratios)
                if len(aspect_ratios) >= 2
                else 0.0
            )

        # Constellation centroid position
        centroid_x = [obs["centroid"][0] for obs in observations]
        centroid_y = [obs["centroid"][1] for obs in observations]

        geom.mean_centroid_x = statistics.mean(centroid_x)
        geom.mean_centroid_y = statistics.mean(centroid_y)
        geom.std_centroid_x = (
            statistics.stdev(centroid_x) if len(centroid_x) >= 2 else 0.0
        )
        geom.std_centroid_y = (
            statistics.stdev(centroid_y) if len(centroid_y) >= 2 else 0.0
        )

        result[constellation] = geom

    return result


def batch_receipts_by_quality(
    other_receipt_data: List[OtherReceiptData],
    merchant_name: str,
) -> Dict[str, List[OtherReceiptData]]:
    """
    Classify receipts into HAPPY, AMBIGUOUS, ANTI_PATTERN batches.

    Uses LLM-based conflict analysis to intelligently separate receipts
    by data quality for specialized pattern learning.

    Args:
        other_receipt_data: All receipts for merchant
        merchant_name: Name of merchant

    Returns:
        Dict mapping batch names to lists of receipts in that batch
    """
    batches: Dict[str, List[OtherReceiptData]] = {
        "HAPPY": [],
        "AMBIGUOUS": [],
        "ANTI_PATTERN": [],
    }

    for receipt_data in other_receipt_data:
        batch = assign_batch_with_llm(receipt_data.labels, merchant_name)
        batches[batch].append(receipt_data)

    logger.info(
        f"Batched {len(other_receipt_data)} receipts: "
        f"HAPPY={len(batches['HAPPY'])}, "
        f"AMBIGUOUS={len(batches['AMBIGUOUS'])}, "
        f"ANTI_PATTERN={len(batches['ANTI_PATTERN'])}"
    )

    return batches


def compute_merchant_patterns(
    other_receipt_data: List[OtherReceiptData],
    merchant_name: str,
    max_pair_patterns: int = MAX_LABEL_PAIRS,
    max_relationship_dimension: int = MAX_RELATIONSHIP_DIMENSION,
) -> Optional[MerchantPatterns]:
    """
    Compute label patterns from other receipts of the same merchant.

    Analyzes validated labels from other receipts to build a statistical
    model of expected label positions and co-occurrence patterns, supporting
    analysis of label pairs, triples, and higher-order relationships.

    Args:
        other_receipt_data: Data from other receipts of same merchant
        merchant_name: Name of the merchant
        max_pair_patterns: Maximum number of label combinations to compute
            detailed geometry for. Higher values = more comprehensive analysis
            but slower computation. (default: 4)
            - 4: Fast baseline, catches most common patterns (~4ms)
            - 16: Balanced analysis (~7ms)
            - 32: Comprehensive (~12ms)
            - 148+: Complete relationship mapping (~30ms+)
        max_relationship_dimension: Size of label relationships to analyze
            (default: 2)
            - 2: Pairwise relationships (A↔B)
            - 3: Triples (A↔B↔C, all pairwise within)
            - 4: Quadruples (A↔B↔C↔D)
            - 5+: Higher-order relationships (more expensive)

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

    # Track all label pairs for pair selection and unexpected pair detection
    all_pair_frequencies: Dict[Tuple[str, str], int] = defaultdict(int)

    for receipt_data in other_receipt_data:
        words = receipt_data.words
        labels = receipt_data.labels

        # Build word lookup
        word_by_id: Dict[Tuple[int, int], ReceiptWord] = {
            (w.line_id, w.word_id): w for w in words
        }

        # Get most recent label per word, preferring VALID ones
        labels_by_word: Dict[Tuple[int, int], List[ReceiptWordLabel]] = (
            defaultdict(list)
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
            # Track which labels appear multiple times on the same line
            label_counts: Dict[str, int] = defaultdict(int)
            for lbl in line_labels:
                label_counts[lbl] += 1

            # Record labels that appear multiple times (multiplicity > 1)
            for label, count in label_counts.items():
                if count > 1:
                    patterns.labels_with_same_line_multiplicity.add(label)

            # Track unique label pairs on this line
            unique_labels = list(set(line_labels))
            for i, label_a in enumerate(unique_labels):
                for label_b in unique_labels[i + 1 :]:
                    pair = tuple(sorted([label_a, label_b]))
                    patterns.same_line_pairs[pair] += 1
                    all_pair_frequencies[pair] += 1
                    patterns.all_observed_pairs.add(pair)

        # Track value pairs: when the same text has different labels
        # Group words by text to find duplicates
        words_by_text: Dict[str, List[Tuple[ReceiptWord, str]]] = defaultdict(
            list
        )
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

        # Count pair frequencies for geometry (first pass - cheap)
        # Group words by label (using existing ReceiptWord objects)
        words_by_label: Dict[str, List[ReceiptWord]] = defaultdict(list)
        for key, label in current_labels.items():
            word = word_by_id.get(key)
            if word:
                words_by_label[label.label].append(word)

        # Count label pairs without expensive geometry calculation
        unique_labels = list(words_by_label.keys())
        for i, label_a in enumerate(unique_labels):
            for label_b in unique_labels[i + 1 :]:
                pair = tuple(sorted([label_a, label_b]))
                all_pair_frequencies[pair] += 1
                patterns.all_observed_pairs.add(pair)

    # TWO-PASS OPTIMIZATION: Select top pairs/tuples before computing expensive geometry
    # This avoids computing geometry for pairs we'll discard anyway
    # For dimension >= 3, generate n-tuples and select top ones
    selected_constellations: List[Tuple[str, ...]] = (
        []
    )  # Store for constellation computation

    if max_relationship_dimension >= 3:
        # Generate n-tuples from pair co-occurrence data
        label_ntuples = _generate_label_ntuples(
            all_pair_frequencies,
            dimension=max_relationship_dimension,
        )
        logger.debug(
            f"Generated {len(label_ntuples)} label {max_relationship_dimension}-tuples "
            f"from co-occurrence data"
        )

        # Select top n-tuples, then extract all pairs from them
        selected_tuples = _select_top_label_ntuples(
            label_ntuples,
            max_ntuples=max_pair_patterns,
        )
        # Store for constellation computation
        selected_constellations = [tuple(sorted(t)) for t in selected_tuples]

        # Extract all pairs from selected n-tuples
        selected_pairs: Set[Tuple[str, ...]] = set()
        for ntuple in selected_tuples:
            # Add all pairwise combinations from this n-tuple
            for pair in combinations(sorted(ntuple), 2):
                selected_pairs.add(pair)
        logger.debug(
            f"Selected {len(selected_tuples)} label {max_relationship_dimension}-tuples, "
            f"extracted {len(selected_pairs)} pairwise relationships for geometry"
        )
    else:
        # For dimension == 2, use pairs directly
        selected_pairs = _select_top_label_pairs(
            all_pair_frequencies,
            max_pairs=max_pair_patterns,
        )
        logger.debug(
            f"Selected {len(selected_pairs)} label pairs for geometry computation"
        )

    # Second pass: Compute geometry only for selected top pairs
    # Cache centroids per label per receipt to avoid recalculation
    for receipt_data in other_receipt_data:
        words = receipt_data.words
        labels = receipt_data.labels

        # Build word lookup
        word_by_id: Dict[Tuple[int, int], ReceiptWord] = {
            (w.line_id, w.word_id): w for w in words
        }

        # Get most recent label per word, preferring VALID ones
        labels_by_word: Dict[Tuple[int, int], List[ReceiptWordLabel]] = (
            defaultdict(list)
        )
        for label in labels:
            key = (label.line_id, label.word_id)
            labels_by_word[key].append(label)

        current_labels: Dict[Tuple[int, int], ReceiptWordLabel] = {}
        for key, label_list in labels_by_word.items():
            label_list.sort(
                key=lambda lbl: (
                    lbl.timestamp_added.isoformat()
                    if hasattr(lbl.timestamp_added, "isoformat")
                    else str(lbl.timestamp_added)
                ),
                reverse=True,
            )
            valid_labels = [
                lbl for lbl in label_list if lbl.validation_status == "VALID"
            ]
            if valid_labels:
                current_labels[key] = valid_labels[0]
            elif label_list:
                current_labels[key] = label_list[0]

        # Cache centroids for this receipt (centroid caching optimization)
        centroid_cache: Dict[Tuple[int, int], Tuple[float, float]] = {}
        for key in current_labels.keys():
            word = word_by_id.get(key)
            if word:
                centroid_cache[key] = word.calculate_centroid()

        # Group words by label
        words_by_label: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
        for key, label in current_labels.items():
            words_by_label[label.label].append(key)

        # Compute geometry ONLY for selected pairs
        unique_labels = list(words_by_label.keys())
        for i, label_a in enumerate(unique_labels):
            for label_b in unique_labels[i + 1 :]:
                pair = tuple(sorted([label_a, label_b]))

                # Skip if not in top pairs
                if pair not in selected_pairs:
                    continue

                # Calculate centroids using cached values
                word_keys_a = words_by_label[label_a]
                word_keys_b = words_by_label[label_b]

                x_coords_a = [
                    centroid_cache[k][0]
                    for k in word_keys_a
                    if k in centroid_cache
                ]
                y_coords_a = [
                    centroid_cache[k][1]
                    for k in word_keys_a
                    if k in centroid_cache
                ]
                if x_coords_a and y_coords_a:
                    centroid_a = (
                        sum(x_coords_a) / len(x_coords_a),
                        sum(y_coords_a) / len(y_coords_a),
                    )
                else:
                    continue

                x_coords_b = [
                    centroid_cache[k][0]
                    for k in word_keys_b
                    if k in centroid_cache
                ]
                y_coords_b = [
                    centroid_cache[k][1]
                    for k in word_keys_b
                    if k in centroid_cache
                ]
                if x_coords_b and y_coords_b:
                    centroid_b = (
                        sum(x_coords_b) / len(x_coords_b),
                        sum(y_coords_b) / len(y_coords_b),
                    )
                else:
                    continue

                # Calculate geometry
                angle = _calculate_angle_degrees(centroid_a, centroid_b)
                distance = _calculate_distance(centroid_a, centroid_b)

                # Store in patterns
                if pair not in patterns.label_pair_geometry:
                    patterns.label_pair_geometry[pair] = LabelPairGeometry()

                patterns.label_pair_geometry[pair].observations.append(
                    GeometricRelationship(angle=angle, distance=distance)
                )

    # Finalize geometry statistics (both polar and Cartesian)
    for pair, geometry in patterns.label_pair_geometry.items():
        if geometry.observations:
            angles = [obs.angle for obs in geometry.observations]
            distances = [obs.distance for obs in geometry.observations]

            # Polar statistics
            geometry.mean_angle = statistics.mean(angles)
            geometry.mean_distance = statistics.mean(distances)

            if len(angles) >= 2:
                geometry.std_angle = statistics.stdev(angles)
                geometry.std_distance = statistics.stdev(distances)
            else:
                geometry.std_angle = 0.0
                geometry.std_distance = 0.0

            # Cartesian statistics - convert observations to (dx, dy) coordinates
            cartesian_coords = [
                _convert_polar_to_cartesian(obs.angle, obs.distance)
                for obs in geometry.observations
            ]

            dx_values = [coord[0] for coord in cartesian_coords]
            dy_values = [coord[1] for coord in cartesian_coords]

            geometry.mean_dx = statistics.mean(dx_values)
            geometry.mean_dy = statistics.mean(dy_values)

            if len(dx_values) >= 2:
                geometry.std_dx = statistics.stdev(dx_values)
                geometry.std_dy = statistics.stdev(dy_values)
            else:
                geometry.std_dx = 0.0
                geometry.std_dy = 0.0

            # Compute distance-from-mean for each observation in Cartesian space
            deviations = [
                math.sqrt(
                    (dx - geometry.mean_dx) ** 2 + (dy - geometry.mean_dy) ** 2
                )
                for dx, dy in cartesian_coords
            ]
            geometry.mean_deviation = statistics.mean(deviations)

            if len(deviations) >= 2:
                geometry.std_deviation = statistics.stdev(deviations)
            else:
                geometry.std_deviation = 0.0

    # Log geometry statistics (pairs already selected in two-pass optimization)
    if patterns.label_pair_geometry:
        logger.info(
            f"Computed geometry for {len(patterns.label_pair_geometry)} selected label pairs "
            f"(skipped {len(all_pair_frequencies) - len(patterns.label_pair_geometry)} non-selected pairs)"
        )

    # Print detailed statistics for learned patterns
    _print_pattern_statistics(patterns, merchant_name)

    # Compute batch-specific patterns for LLM-integrated batching
    logger.info("\n" + "=" * 80)
    logger.info("BATCH-SPECIFIC PATTERN COMPUTATION (LLM-Integrated Batching)")
    logger.info("=" * 80)

    try:
        batches = batch_receipts_by_quality(other_receipt_data, merchant_name)
        patterns.batch_classification = {
            batch_name: len(receipts)
            for batch_name, receipts in batches.items()
        }

        # Compute patterns for each batch separately
        for batch_name, batch_receipts in batches.items():
            if not batch_receipts:
                logger.info(f"{batch_name} batch: 0 receipts, skipping")
                continue

            logger.info(
                f"\n{batch_name} batch: Computing patterns from "
                f"{len(batch_receipts)} receipts"
            )

            # Compute patterns for this batch using existing logic
            batch_patterns = _compute_patterns_for_subset(batch_receipts)

            # Store batch-specific geometry
            if batch_name == "HAPPY":
                patterns.happy_label_pair_geometry = batch_patterns
                logger.info(
                    f"  Happy patterns: {len(batch_patterns)} label pairs "
                    f"(threshold: 1.5σ - strict)"
                )
            elif batch_name == "AMBIGUOUS":
                patterns.ambiguous_label_pair_geometry = batch_patterns
                logger.info(
                    f"  Ambiguous patterns: {len(batch_patterns)} label pairs "
                    f"(threshold: 2.0σ - moderate)"
                )
            elif batch_name == "ANTI_PATTERN":
                patterns.anti_label_pair_geometry = batch_patterns
                logger.info(
                    f"  Anti-patterns: {len(batch_patterns)} label pairs "
                    f"(threshold: 3.0σ - lenient)"
                )

        logger.info("\n" + "=" * 80)

    except Exception as e:
        logger.warning(f"Batch-specific pattern computation failed: {e}")
        logger.warning("Proceeding with non-batched patterns only")

    # Compute constellation patterns for n-tuples (dimension >= 3)
    if selected_constellations:
        logger.info("\n" + "=" * 80)
        logger.info("CONSTELLATION PATTERN COMPUTATION (N-Tuple Geometry)")
        logger.info("=" * 80)

        constellation_patterns = _compute_constellation_patterns(
            other_receipt_data,
            selected_constellations,
            min_observations=3,
        )
        patterns.constellation_geometry = constellation_patterns

        if constellation_patterns:
            logger.info(
                f"Computed {len(constellation_patterns)} constellation patterns:"
            )
            for constellation, geom in constellation_patterns.items():
                labels_str = " + ".join(constellation)
                logger.info(
                    f"  {labels_str}: {geom.observation_count} observations, "
                    f"bbox={geom.mean_width:.3f}x{geom.mean_height:.3f}"
                )
        else:
            logger.info(
                "No constellation patterns computed (insufficient data)"
            )

        logger.info("=" * 80 + "\n")

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
        # Special handling for self-comparisons (same label appearing multiple times)
        if label == other_label:
            # If this label type never appears multiple times in training data, flag it
            if label not in patterns.labels_with_same_line_multiplicity:
                # Only flag if we have good training data
                if patterns.receipt_count >= 5:
                    return EvaluationIssue(
                        issue_type="unexpected_label_multiplicity",
                        word=ctx.word,
                        current_label=label,
                        suggested_status="NEEDS_REVIEW",
                        reasoning=(
                            f"'{ctx.word.text}' labeled {label} appears multiple times on receipt, "
                            f"but {label} never appears multiple times in {patterns.receipt_count} "
                            f"training receipts for this merchant. This may indicate mislabeling."
                        ),
                        word_context=ctx,
                    )
            # Label appears multiple times in training data, so this is expected
            continue

        pair = tuple(sorted([label, other_label]))

        # If this pair never appeared in training receipts, it's unexpected
        if pair not in patterns.all_observed_pairs:
            # But be permissive: only flag if we have good training data coverage
            # (need confidence from multiple receipts)
            if patterns.receipt_count >= 5:
                return EvaluationIssue(
                    issue_type="unexpected_label_pair",
                    word=ctx.word,
                    current_label=label,
                    suggested_status="NEEDS_REVIEW",
                    reasoning=(
                        f"'{ctx.word.text}' labeled {label} appears with {other_label}, "
                        f"a combination never seen in {patterns.receipt_count} training receipts "
                        f"for this merchant. This may indicate mislabeling or unusual structure."
                    ),
                    word_context=ctx,
                )

    return None


def _get_adaptive_threshold(geometry: "LabelPairGeometry") -> float:
    """
    Determine adaptive threshold based on pattern tightness.

    Tighter patterns (lower std deviation) → stricter thresholds
    Looser patterns (higher std deviation) → more lenient thresholds

    Args:
        geometry: The LabelPairGeometry to evaluate

    Returns:
        Threshold in standard deviations (1.5-2.5σ)
    """
    if geometry.std_deviation is None:
        return 2.0  # Default

    # Classify based on standard deviation
    if geometry.std_deviation < 0.1:
        return 1.5  # TIGHT pattern - be strict
    elif geometry.std_deviation < 0.2:
        return 2.0  # MODERATE pattern - balanced
    else:
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
    1. HAPPY patterns (conflict-free receipts): 1.5σ threshold (strict, HIGH confidence)
    2. AMBIGUOUS patterns (format variations): 2.0σ threshold (moderate, MEDIUM confidence)
    3. ANTI-PATTERN patterns (problematic receipts): 3.0σ threshold (lenient, LOW confidence)

    Without batching, uses adaptive thresholds based on pattern tightness:
    - TIGHT patterns (std < 0.1): 1.5σ threshold
    - MODERATE patterns (std 0.1-0.2): 2.0σ threshold
    - LOOSE patterns (std > 0.2): 2.5σ threshold

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
        # Use original patterns with adaptive thresholds (fallback if no batching)
        if not patterns.label_pair_geometry:
            return None

        # Check geometry for each label pair
        for other_label in other_labels:
            pair = tuple(sorted([label, other_label]))

            # Check if we have learned geometry for this pair
            if pair not in patterns.label_pair_geometry:
                continue

            geometry = patterns.label_pair_geometry[pair]
            # Skip if we don't have Cartesian statistics (shouldn't happen with new code)
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
        threshold_multiplier: Multiplier for standard deviations (1.5, 2.0, or 3.0)

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
                        f"[{confidence} confidence] '{ctx.word.text}' labeled {label} has unusual geometric relationship "
                        f"with {other_label} (patterns from {batch_label} receipts). "
                        f"Expected position ({geometry.mean_dx:.2f}, {geometry.mean_dy:.2f}), "
                        f"actual ({actual_dx:.2f}, {actual_dy:.2f}), deviation {deviation:.3f} "
                        f"(threshold: {threshold_std}σ = {threshold_std * geometry.std_deviation:.3f}). "
                        f"This may indicate mislabeling."
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
                    f"'{ctx.word.text}' labeled {label} has unusual geometric relationship "
                    f"with {other_label}. Expected position ({geometry.mean_dx:.2f}, {geometry.mean_dy:.2f}), "
                    f"actual ({actual_dx:.2f}, {actual_dy:.2f}), deviation {deviation:.3f} "
                    f"(adaptive threshold: {threshold_std}σ = {threshold_std * geometry.std_deviation:.3f}). "
                    f"This may indicate mislabeling."
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

    Unlike pairwise checks that only examine A↔B relationships, constellation
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
                    f"'{ctx.word.text}' labeled {label} has unusual position within "
                    f"constellation [{constellation_str}]. "
                    f"Expected offset ({expected.mean_dx:.3f}, {expected.mean_dy:.3f}) "
                    f"from cluster center, actual ({actual_dx:.3f}, {actual_dy:.3f}). "
                    f"Deviation {deviation:.3f} exceeds {threshold_std}σ threshold "
                    f"({threshold_std * expected.std_deviation:.3f}). "
                    f"This suggests the label may be misplaced relative to its group."
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

                    # Check if spatial ordering roughly matches (allow small variation)
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

                if (
                    other_fit > my_fit + 0.5
                ):  # Other position is significantly better
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
        EvaluationIssue if missing constellation member detected, None otherwise
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
                f"'{ctx.word.text}' has no label but is at the expected position "
                f"for {missing_label} within constellation [{constellation_str}]. "
                f"Present labels: [{present_str}]. "
                f"Distance to expected position: {distance:.3f} (threshold: {position_threshold:.3f}). "
                f"This word may be missing a {missing_label} label."
            ),
            word_context=ctx,
        )

    return None


def detect_label_conflicts(
    labels: List[ReceiptWordLabel],
) -> List[Tuple[int, int, Set[str]]]:
    """
    Detect when the same word position has conflicting labels.

    This identifies data quality issues in training labels where semantic
    contradictions appear (e.g., same price labeled as both UNIT_PRICE and LINE_TOTAL).

    Args:
        labels: All ReceiptWordLabel objects for a receipt

    Returns:
        List of (line_id, word_id, conflicting_labels) tuples where labels conflict
    """
    # Group labels by position
    labels_by_position = defaultdict(set)
    for label in labels:
        position = (label.line_id, label.word_id)
        labels_by_position[position].add(label.label)

    conflicts = []
    for position, label_set in labels_by_position.items():
        # Check if any conflicting pair exists in this position's labels
        for label1, label2 in CONFLICTING_LABEL_PAIRS:
            if label1 in label_set and label2 in label_set:
                line_id, word_id = position
                conflicts.append((line_id, word_id, label_set))
                logger.warning(
                    f"Label conflict at line {line_id}, word {word_id}: "
                    f"{label1} and {label2} both present (all labels: {label_set})"
                )
                break  # Only report once per position

    return conflicts


def classify_conflicts_with_llm(
    conflicts: List[Tuple[int, int, Set[str]]],
    receipt_label: str,
) -> Dict[Tuple[int, int], str]:
    """
    Use LLM to classify each conflict as REAL_ERROR or FORMAT_VARIATION.

    This distinguishes between:
    - REAL_ERROR: Labeling mistake or parsing error (should flag)
    - FORMAT_VARIATION: Expected format ambiguity in this merchant's receipts

    Args:
        conflicts: List of (line_id, word_id, labels_set) tuples
        receipt_label: Name of merchant/receipt type

    Returns:
        Dict mapping (line_id, word_id) to classification
    """
    try:
        from langchain_ollama import ChatOllama
    except ImportError:
        logger.warning(
            "ChatOllama not available, using heuristic classification"
        )
        return _heuristic_conflict_classification(conflicts)

    classifications = {}

    for line_id, word_id, label_set in conflicts:
        labels_str = ", ".join(sorted(label_set))
        conflicting_pairs = []

        for label1, label2 in CONFLICTING_LABEL_PAIRS:
            if label1 in label_set and label2 in label_set:
                conflicting_pairs.append(f"{label1} & {label2}")

        prompt = f"""
You are analyzing label conflicts in a {receipt_label} receipt.

At line {line_id}, word {word_id}, we found these conflicting labels:
{labels_str}

Conflicting pairs:
{", ".join(conflicting_pairs)}

Question: Is this a data quality issue or an expected format variation for {receipt_label}?

Consider:
- UNIT_PRICE ↔ LINE_TOTAL: Sprouts often shows both (FORMAT_VARIATION)
- PRODUCT_NAME ↔ QUANTITY: Products aren't quantities (REAL_ERROR)
- QUANTITY ↔ UNIT_PRICE: Quantity confusion (REAL_ERROR)

Answer with ONLY one word: REAL_ERROR or FORMAT_VARIATION
"""

        try:
            llm = ChatOllama(model="gpt-oss:20b-cloud", temperature=0.3)
            response = llm.invoke(prompt)
            classification = response.content.strip().upper()

            if classification not in ("REAL_ERROR", "FORMAT_VARIATION"):
                # Fallback if LLM didn't give clean answer
                classification = _classify_conflict_heuristic(label_set)

            classifications[(line_id, word_id)] = classification
            logger.debug(
                f"Conflict at line {line_id}, word {word_id}: {classification}"
            )

        except Exception as e:
            logger.warning(
                f"LLM classification failed for line {line_id}: {e}, using heuristic"
            )
            classifications[(line_id, word_id)] = _classify_conflict_heuristic(
                label_set
            )

    return classifications


def _classify_conflict_heuristic(label_set: Set[str]) -> str:
    """Heuristic classification when LLM not available."""
    # UNIT_PRICE ↔ LINE_TOTAL is typically format variation
    if "UNIT_PRICE" in label_set and "LINE_TOTAL" in label_set:
        return "FORMAT_VARIATION"

    # PRODUCT_NAME with anything is typically real error
    if "PRODUCT_NAME" in label_set:
        return "REAL_ERROR"

    # Default to real error for safety
    return "REAL_ERROR"


def _heuristic_conflict_classification(
    conflicts: List[Tuple[int, int, Set[str]]],
) -> Dict[Tuple[int, int], str]:
    """Classify conflicts using heuristics only."""
    classifications = {}
    for line_id, word_id, label_set in conflicts:
        classifications[(line_id, word_id)] = _classify_conflict_heuristic(
            label_set
        )
    return classifications


def assign_batch_with_llm(
    labels: List[ReceiptWordLabel],
    receipt_label: str,
) -> str:
    """
    Classify receipt into HAPPY, AMBIGUOUS, or ANTI_PATTERN batch.

    Uses LLM-classified conflicts to make intelligent assignment.

    Args:
        labels: All labels for receipt
        receipt_label: Merchant/receipt type name

    Returns:
        Batch assignment: "HAPPY", "AMBIGUOUS", or "ANTI_PATTERN"
    """
    conflicts = detect_label_conflicts(labels)

    if not conflicts:
        return "HAPPY"  # No conflicts = definitely happy

    # Classify each conflict
    classifications = classify_conflicts_with_llm(conflicts, receipt_label)

    real_errors = sum(
        1
        for (line_id, word_id), classification in classifications.items()
        if classification == "REAL_ERROR"
    )
    format_variations = sum(
        1
        for (line_id, word_id), classification in classifications.items()
        if classification == "FORMAT_VARIATION"
    )

    logger.info(
        f"Batch classification: {len(conflicts)} conflicts "
        f"({real_errors} real errors, {format_variations} format variations)"
    )

    # Assignment logic
    if real_errors >= 3:
        return "ANTI_PATTERN"  # Many real errors = problematic
    elif format_variations >= 2 and real_errors == 0:
        return "AMBIGUOUS"  # Format variations but no real errors
    elif format_variations >= 1 and real_errors <= 1:
        return "AMBIGUOUS"  # Mixed but mostly variations
    else:
        # Small number of issues, might still be usable
        return "HAPPY" if real_errors == 0 else "AMBIGUOUS"


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

            # Check for unexpected label pair combinations first
            issue = check_unexpected_label_pair(ctx, word_contexts, patterns)
            if issue:
                issues.append(issue)
                continue

            # Use geometric anomaly detection instead of simple same-line conflict
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
            # First check same-line cluster heuristic
            issue = check_missing_label_in_cluster(ctx)
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
    merchant_name: Optional[str] = None,
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
        merchant_name: Optional merchant name to scope results to same merchant

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
            logger.warning(
                f"Invalid embedding format for word: {word_chroma_id}"
            )
            return []

        if not query_embedding:
            logger.warning(f"Empty embedding found for word: {word_chroma_id}")
            return []

        # Query ChromaDB words collection using the existing embedding
        results = chroma_client.query(
            collection_name="words",
            query_embeddings=[query_embedding],
            n_results=n_results * 2
            + 1,  # Fetch more, filter later (+1 for self)
            include=["documents", "metadatas", "distances"],
        )

        if not results or not results.get("ids"):
            return []

        ids = list(results.get("ids", [[]])[0])
        documents = list(results.get("documents", [[]])[0])
        metadatas = list(results.get("metadatas", [[]])[0])
        distances = list(results.get("distances", [[]])[0])

        similar_words: List[SimilarWordResult] = []

        for doc_id, doc, meta, dist in zip(
            ids, documents, metadatas, distances
        ):
            # Skip self (the query word)
            if doc_id == word_chroma_id:
                continue

            # Filter by merchant if specified
            if merchant_name:
                result_merchant = meta.get("merchant_name")
                if result_merchant != merchant_name:
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
                [
                    lbl.strip()
                    for lbl in valid_labels_str.split(",")
                    if lbl.strip()
                ]
                if valid_labels_str
                else []
            )
            invalid_labels = (
                [
                    lbl.strip()
                    for lbl in invalid_labels_str.split(",")
                    if lbl.strip()
                ]
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
        logger.error(
            f"Error querying ChromaDB for similar words: {e}", exc_info=True
        )
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
        status = (
            f"[{w.validation_status}]"
            if w.validation_status
            else "[unvalidated]"
        )
        label = w.label or "no label"
        lines.append(
            f'- "{w.word_text}" → {label} {status} (similarity: {w.similarity_score:.2f})'
        )

        # Add valid/invalid labels if available
        if w.valid_labels:
            lines.append(f"    Valid labels: {', '.join(w.valid_labels)}")
        if w.invalid_labels:
            lines.append(f"    Invalid labels: {', '.join(w.invalid_labels)}")

    return "\n".join(lines)
