"""
Statistical pattern computation for the Label Evaluator agent.

Provides functions for computing label patterns from training receipts,
including geometric relationships, constellation patterns, and batch
classification for quality-based pattern learning.
"""

import logging
import math
import statistics
from collections import Counter, defaultdict
from itertools import combinations
from typing import Any, Dict, List, Optional, Set, Tuple

from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel

from receipt_agent.agents.label_evaluator.geometry import (
    angle_difference,
    calculate_angle_degrees,
    calculate_distance,
    convert_polar_to_cartesian,
)
from receipt_agent.agents.label_evaluator.state import (
    ConstellationGeometry,
    GeometricRelationship,
    LabelPairGeometry,
    LabelRelativePosition,
    MerchantPatterns,
    OtherReceiptData,
)
from receipt_agent.constants import (
    CONFLICTING_LABEL_PAIRS,
    CROSS_GROUP_PRIORITY_PAIRS,
    LABEL_TO_GROUP,
    WITHIN_GROUP_PRIORITY_PAIRS,
)

logger = logging.getLogger(__name__)

# Configuration for limiting label pair geometry computation
MAX_LABEL_PAIRS = 4  # Only compute geometry for top N label type pairs
MAX_RELATIONSHIP_DIMENSION = (
    2  # Analyze relationships between N labels (2=pairs, 3=triples, etc.)
)


# =============================================================================
# Label Pair Selection
# =============================================================================


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
    for pair, _freq in within_group_pairs:
        if len(selected) >= max_pairs:
            break
        if pair in WITHIN_GROUP_PRIORITY_PAIRS and pair not in selected:
            selected.add(pair)

    for pair, _freq in cross_group_pairs:
        if len(selected) >= max_pairs:
            break
        if pair in CROSS_GROUP_PRIORITY_PAIRS and pair not in selected:
            selected.add(pair)

    # Pass 2: Fill remaining slots with most frequent pairs (any type)
    combined = sorted(
        [
            (p, f)
            for p, f in within_group_pairs + cross_group_pairs + other_pairs
            if p not in selected
        ],
        key=lambda x: -x[1],
    )

    for pair, _freq in combined:
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
                    f"{p} [cross: {LABEL_TO_GROUP.get(p[0])}↔"
                    f"{LABEL_TO_GROUP.get(p[1])}]"
                )
            else:
                pair_info.append(f"{p} [ungrouped]")

        logger.info(
            "Selected %s label pairs for geometry computation "
            "(%s within-group, %s cross-group):",
            len(selected),
            within_group_count,
            cross_group_count,
        )
        for info in pair_info:
            logger.info("  - %s", info)

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
        "Selected %s label n-tuples by frequency (top: %s)",
        len(selected),
        ", ".join(str(nt) for nt, freq in sorted_ntuples[:3]),
    )

    return selected


def _generate_label_ntuples(
    all_pairs: Dict[Tuple[str, str], int],
    dimension: int = 2,
) -> Dict[Tuple[str, ...], int]:
    """
    Generate n-tuples of labels from pair co-occurrence data.

    For dimension=2: Returns pairs as-is
    For dimension=3+: Identifies labels that co-occur and groups them into
    n-tuples

    Args:
        all_pairs: Dict mapping (label1, label2) to co-occurrence frequency
        dimension: Size of n-tuples to generate (2=pairs, 3=triples, etc.)

    Returns:
        Dict mapping n-tuple to frequency count
    """
    if dimension < 2:
        raise ValueError("dimension must be >= 2")

    if dimension == 2:
        # For pairs, just return the input as-is (cast to match return type)
        return dict(all_pairs.items())

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
                pair = (min(label_a, label_b), max(label_a, label_b))
                if pair not in all_pairs:
                    is_clique = False
                    break
                min_freq = min(min_freq, all_pairs[pair])
            if not is_clique:
                break

        if is_clique and min_freq > 0:
            sorted_tuple = tuple(sorted(ntuple))
            ntuples[sorted_tuple] = int(min_freq)

    return ntuples


# =============================================================================
# Pattern Statistics
# =============================================================================


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
    logger.info("\n%s", "=" * 80)
    logger.info("PATTERN STATISTICS FOR %s", merchant_name)
    logger.info("%s", "=" * 80)
    logger.info("Training data: %s receipts", patterns.receipt_count)
    logger.info(
        "Label types observed: %s",
        len(patterns.label_positions),
    )
    logger.info(
        "Label pairs with geometry: %s\n",
        len(patterns.label_pair_geometry),
    )

    if not patterns.label_pair_geometry:
        logger.info("No geometric patterns to display.")
        return

    for pair in sorted(patterns.label_pair_geometry.keys()):
        geometry = patterns.label_pair_geometry[pair]
        obs_count = len(geometry.observations)

        logger.info("\n%s ↔ %s:", pair[0], pair[1])
        logger.info("  Observations: %s", obs_count)

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

            logger.info("  [POLAR COORDINATES]")
            logger.info(
                "  Angle: mean=%.1f°, std=%.1f° [%s]",
                mean_angle,
                std_angle,
                tightness,
            )
            logger.info(
                "    Range (±1.5σ): %.1f° to %.1f°",
                (mean_angle - 1.5 * std_angle) % 360,
                (mean_angle + 1.5 * std_angle) % 360,
            )

            logger.info(
                "  Distance: mean=%.3f, std=%.3f",
                mean_distance,
                std_distance,
            )
            logger.info(
                "    Range (±1.5σ): %.3f to %.3f",
                max(0, mean_distance - 1.5 * std_distance),
                min(1.0, mean_distance + 1.5 * std_distance),
            )

            # Convert observations to Cartesian coordinates for analysis
            logger.info("\n  [CARTESIAN COORDINATES]")
            cartesian_coords = [
                convert_polar_to_cartesian(obs.angle, obs.distance)
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

            logger.info(
                "  dx: mean=%+.3f, std=%.3f",
                mean_dx,
                std_dx,
            )
            logger.info(
                "  dy: mean=%+.3f, std=%.3f",
                mean_dy,
                std_dy,
            )
            logger.info(
                "  Mean position: (%+.3f, %+.3f) [distance from origin: %.3f]",
                mean_dx,
                mean_dy,
                mean_cartesian_distance,
            )
            logger.info(
                "  Deviation from mean: mean=%.3f, std=%.3f [%s]",
                mean_deviation,
                std_deviation,
                cart_tightness,
            )
            logger.info(
                "    Outlier range (±1.5σ): %.3f to %.3f",
                max(0, mean_deviation - 1.5 * std_deviation),
                mean_deviation + 1.5 * std_deviation,
            )

            # Sample sizes assessment
            if obs_count < 5:
                logger.info(
                    "\n  ⚠️  WARNING: Only %s observations - ",
                    obs_count,
                )
                logger.info("pattern may be unreliable")
            elif obs_count < 10:
                logger.info(
                    "\n  ⚠️  Note: %s observations - limited confidence",
                    obs_count,
                )
            else:
                logger.info("\n  ✓ Sufficient observations (%s)", obs_count)

    logger.info("\n%s\n", "=" * 80)


# =============================================================================
# Batch-Specific Pattern Computation
# =============================================================================


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

                        angle = calculate_angle_degrees(
                            centroid_a, centroid_b
                        )
                        distance = calculate_distance(centroid_a, centroid_b)

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
                convert_polar_to_cartesian(obs.angle, obs.distance)
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


# =============================================================================
# Constellation Pattern Computation
# =============================================================================


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


# =============================================================================
# Batch Classification (for quality-based pattern learning)
# =============================================================================


def detect_label_conflicts(
    labels: List[ReceiptWordLabel],
) -> List[Tuple[int, int, Set[str]]]:
    """
    Detect when the same word position has conflicting labels.

    This identifies data quality issues in training labels where semantic
    contradictions appear (e.g., same price labeled as both UNIT_PRICE and
    LINE_TOTAL).

    Labels with validation_status=INVALID are excluded since they've already
    been rejected in the audit trail.

    Args:
        labels: All ReceiptWordLabel objects for a receipt

    Returns:
        List of (line_id, word_id, conflicting_labels) tuples where labels
        conflict
    """
    # Group labels by position, excluding INVALID labels
    labels_by_position: Dict[Tuple[int, int], Set[str]] = defaultdict(set)
    for label in labels:
        # Skip labels already marked as INVALID in the audit trail
        if label.validation_status == "INVALID":
            continue
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
                    "Label conflict at line %s, word %s: %s and %s both "
                    "present (all labels: %s)",
                    line_id,
                    word_id,
                    label1,
                    label2,
                    label_set,
                )
                break  # Only report once per position

    return conflicts


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


def classify_conflicts_with_llm(
    conflicts: List[Tuple[int, int, Set[str]]],
    receipt_label: str,
) -> Dict[Tuple[int, int], str]:
    """
    Classify each conflict as REAL_ERROR or FORMAT_VARIATION.

    Uses deterministic heuristics based on known label pair semantics:
    - UNIT_PRICE + LINE_TOTAL: FORMAT_VARIATION (expected ambiguity)
    - PRODUCT_NAME + anything: REAL_ERROR (semantic impossibility)

    Args:
        conflicts: List of (line_id, word_id, labels_set) tuples
        receipt_label: Name of merchant/receipt type (unused, kept for API
            compat)

    Returns:
        Dict mapping (line_id, word_id) to classification
    """
    # Use heuristics - they're deterministic and match the known patterns
    return _heuristic_conflict_classification(conflicts)


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
        "Batch classification: %s conflicts (%s real errors, %s format "
        "variations)",
        len(conflicts),
        real_errors,
        format_variations,
    )

    # Assignment logic
    if real_errors >= 3:
        return "ANTI_PATTERN"  # Many real errors = problematic
    if format_variations >= 2 and real_errors == 0:
        return "AMBIGUOUS"  # Format variations but no real errors
    # pylint: disable-next=chained-comparison
    if format_variations >= 1 and 0 <= real_errors <= 1:
        return "AMBIGUOUS"  # Mixed but mostly variations
    # Small number of issues, might still be usable
    return "HAPPY" if real_errors == 0 else "AMBIGUOUS"


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
        "Batched %s receipts: HAPPY=%s, AMBIGUOUS=%s, ANTI_PATTERN=%s",
        len(other_receipt_data),
        len(batches["HAPPY"]),
        len(batches["AMBIGUOUS"]),
        len(batches["ANTI_PATTERN"]),
    )

    return batches


# =============================================================================
# Main Entry Point
# =============================================================================


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
        labels_by_line: Dict[int, List[str]] = defaultdict(list)
        for key, label in current_labels.items():
            labels_by_line[key[0]].append(label.label)

        for line_labels in labels_by_line.values():
            # Track which labels appear multiple times on the same line
            label_counts: Dict[str, int] = defaultdict(int)
            for lbl in line_labels:
                label_counts[lbl] += 1

            # Record labels that appear multiple times (multiplicity > 1)
            for lbl_name, count in label_counts.items():
                if count > 1:
                    patterns.labels_with_same_line_multiplicity.add(lbl_name)

            # Track unique label pairs on this line
            unique_line_labels = list(set(line_labels))
            for i, label_a in enumerate(unique_line_labels):
                for label_b in unique_line_labels[i + 1 :]:
                    pair = (min(label_a, label_b), max(label_a, label_b))
                    patterns.same_line_pairs[pair] += 1
                    all_pair_frequencies[pair] += 1
                    patterns.all_observed_pairs.add(pair)

        # Track value pairs: when the same text has different labels
        words_by_text: Dict[str, List[Tuple[ReceiptWord, str]]] = defaultdict(
            list
        )
        for key, cur_label in current_labels.items():
            word = word_by_id.get(key)
            if word:
                words_by_text[word.text].append((word, cur_label.label))

        # For each text value that appears multiple times with different labels
        for _text, word_label_pairs in words_by_text.items():
            if len(word_label_pairs) > 1:
                # Check for same text with different labels
                unique_label_pairs: Set[Tuple[str, str]] = set()
                label_positions_by_label: Dict[str, float] = {}

                for w, lbl_name in word_label_pairs:
                    y = w.calculate_centroid()[1]
                    label_positions_by_label[lbl_name] = y
                    for _other_word, other_lbl in word_label_pairs:
                        if lbl_name != other_lbl:
                            pair = (
                                min(lbl_name, other_lbl),
                                max(lbl_name, other_lbl),
                            )
                            unique_label_pairs.add(pair)

                # Record each value pair and their positions
                for pair in unique_label_pairs:
                    patterns.value_pairs[pair] += 1
                    label1, label2 = pair
                    y1 = label_positions_by_label.get(label1)
                    y2 = label_positions_by_label.get(label2)
                    if y1 is not None and y2 is not None:
                        patterns.value_pair_positions[pair] = (y1, y2)

        # Count pair frequencies for geometry (first pass - cheap)
        words_by_label: Dict[str, List[ReceiptWord]] = defaultdict(list)
        for key, cur_label in current_labels.items():
            word = word_by_id.get(key)
            if word:
                words_by_label[cur_label.label].append(word)

        # Count label pairs without expensive geometry calculation
        unique_labels = list(words_by_label.keys())
        for i, label_a in enumerate(unique_labels):
            for label_b in unique_labels[i + 1 :]:
                pair = (min(label_a, label_b), max(label_a, label_b))
                all_pair_frequencies[pair] += 1
                patterns.all_observed_pairs.add(pair)

    # TWO-PASS OPTIMIZATION: Select top pairs/tuples before computing geometry
    selected_constellations: List[Tuple[str, ...]] = []

    if max_relationship_dimension >= 3:
        # Generate n-tuples from pair co-occurrence data
        label_ntuples = _generate_label_ntuples(
            all_pair_frequencies,
            dimension=max_relationship_dimension,
        )
        logger.debug(
            "Generated %s label %s-tuples from co-occurrence data",
            len(label_ntuples),
            max_relationship_dimension,
        )

        # Select top n-tuples, then extract all pairs from them
        selected_tuples = _select_top_label_ntuples(
            label_ntuples,
            max_ntuples=max_pair_patterns,
        )
        selected_constellations = [tuple(sorted(t)) for t in selected_tuples]

        # Extract all pairs from selected n-tuples
        selected_pairs: Set[Tuple[str, str]] = set()
        for ntuple in selected_tuples:
            for pair in combinations(sorted(ntuple), 2):
                selected_pairs.add((pair[0], pair[1]))
        logger.debug(
            "Selected %s label %s-tuples, extracted %s pairwise relationships",
            len(selected_tuples),
            max_relationship_dimension,
            len(selected_pairs),
        )
    else:
        # For dimension == 2, use pairs directly
        selected_pairs = _select_top_label_pairs(
            all_pair_frequencies,
            max_pairs=max_pair_patterns,
        )
        logger.debug(
            "Selected %s label pairs for geometry computation",
            len(selected_pairs),
        )

    # Second pass: Compute geometry only for selected top pairs
    for receipt_data in other_receipt_data:
        words_2 = receipt_data.words
        labels_2 = receipt_data.labels

        # Build word lookup
        word_by_id_2: Dict[Tuple[int, int], ReceiptWord] = {
            (w.line_id, w.word_id): w for w in words_2
        }

        # Get most recent label per word, preferring VALID ones
        labels_by_word_2: Dict[Tuple[int, int], List[ReceiptWordLabel]] = (
            defaultdict(list)
        )
        for label_item in labels_2:
            key = (label_item.line_id, label_item.word_id)
            labels_by_word_2[key].append(label_item)

        current_labels_2: Dict[Tuple[int, int], ReceiptWordLabel] = {}
        for key, label_list in labels_by_word_2.items():
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
                current_labels_2[key] = valid_labels[0]
            elif label_list:
                current_labels_2[key] = label_list[0]

        # Cache centroids for this receipt
        centroid_cache: Dict[Tuple[int, int], Tuple[float, float]] = {}
        for key in current_labels_2:
            word = word_by_id_2.get(key)
            if word:
                centroid_cache[key] = word.calculate_centroid()

        # Group words by label (storing keys, not ReceiptWord objects)
        words_by_label_2: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
        for key, cur_label in current_labels_2.items():
            words_by_label_2[cur_label.label].append(key)

        # Compute geometry ONLY for selected pairs
        unique_labels_2 = list(words_by_label_2.keys())
        for i, label_a in enumerate(unique_labels_2):
            for label_b in unique_labels_2[i + 1 :]:
                pair = (min(label_a, label_b), max(label_a, label_b))

                # Skip if not in top pairs
                if pair not in selected_pairs:
                    continue

                # Calculate centroids using cached values
                word_keys_a = words_by_label_2[label_a]
                word_keys_b = words_by_label_2[label_b]

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
                angle = calculate_angle_degrees(centroid_a, centroid_b)
                distance = calculate_distance(centroid_a, centroid_b)

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

            # Cartesian statistics
            cartesian_coords = [
                convert_polar_to_cartesian(obs.angle, obs.distance)
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

            # Compute distance-from-mean for each observation
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

    # Log geometry statistics
    if patterns.label_pair_geometry:
        logger.info(
            "Computed geometry for %s selected label pairs "
            "(skipped %s non-selected pairs)",
            len(patterns.label_pair_geometry),
            len(all_pair_frequencies) - len(patterns.label_pair_geometry),
        )

    # Print detailed statistics for learned patterns
    _print_pattern_statistics(patterns, merchant_name)

    # Compute batch-specific patterns for LLM-integrated batching
    logger.info("\n%s", "=" * 80)
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
                logger.info("%s batch: 0 receipts, skipping", batch_name)
                continue

            logger.info(
                "\n%s batch: Computing patterns from %s receipts",
                batch_name,
                len(batch_receipts),
            )

            # Compute patterns for this batch using existing logic
            batch_patterns = _compute_patterns_for_subset(batch_receipts)

            # Store batch-specific geometry
            if batch_name == "HAPPY":
                patterns.happy_label_pair_geometry = batch_patterns
                logger.info(
                    "  Happy patterns: %s label pairs (threshold: 1.5σ - "
                    "strict)",
                    len(batch_patterns),
                )
            elif batch_name == "AMBIGUOUS":
                patterns.ambiguous_label_pair_geometry = batch_patterns
                logger.info(
                    "  Ambiguous patterns: %s label pairs "
                    "(threshold: 2.0σ - moderate)",
                    len(batch_patterns),
                )
            elif batch_name == "ANTI_PATTERN":
                patterns.anti_label_pair_geometry = batch_patterns
                logger.info(
                    "  Anti-patterns: %s label pairs (threshold: 3.0σ - "
                    "lenient)",
                    len(batch_patterns),
                )

        logger.info("\n%s", "=" * 80)

    except Exception as e:
        logger.warning("Batch-specific pattern computation failed: %s", e)
        logger.warning("Proceeding with non-batched patterns only")

    # Compute constellation patterns for n-tuples (dimension >= 3)
    if selected_constellations:
        logger.info("\n%s", "=" * 80)
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
                "Computed %s constellation patterns:",
                len(constellation_patterns),
            )
            for constellation, geom in constellation_patterns.items():
                labels_str = " + ".join(constellation)
                logger.info(
                    "  %s: %s observations, bbox=%.3fx%.3f",
                    labels_str,
                    geom.observation_count,
                    geom.mean_width,
                    geom.mean_height,
                )
        else:
            logger.info(
                "No constellation patterns computed (insufficient data)"
            )

        logger.info("%s\n", "=" * 80)

    return patterns
