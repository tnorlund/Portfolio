"""Entity serialization for S3 data transfer.

Provides functions to serialize/deserialize receipt entities (ReceiptWord,
ReceiptWordLabel, ReceiptPlace) for JSON storage in S3.

Uses dataclasses.asdict() for serialization since all entities are dataclasses.
Deserialization parses datetime strings and passes kwargs to constructors.
"""

import sys
from dataclasses import asdict
from datetime import datetime, timezone

from receipt_dynamo.entities import (
    ReceiptPlace,
    ReceiptWord,
    ReceiptWordLabel,
)

# Import types from parent package
sys.path.insert(0, str(__file__).rsplit("/lambdas", 1)[0])
from evaluator_types import (
    PatternsFile,
    SerializedLabel,
    SerializedPlace,
    SerializedWord,
)


def serialize_word(word: ReceiptWord) -> SerializedWord:
    """Serialize ReceiptWord for S3 storage using asdict."""
    data = asdict(word)
    # Convert embedding_status enum to string if present
    if data.get("embedding_status"):
        data["embedding_status"] = str(data["embedding_status"])
    return data  # type: ignore[return-value]


def deserialize_word(data: SerializedWord) -> ReceiptWord:
    """Deserialize ReceiptWord from S3 data."""
    return ReceiptWord(**data)


def serialize_label(label: ReceiptWordLabel) -> SerializedLabel:
    """Serialize ReceiptWordLabel for S3 storage using asdict."""
    data = asdict(label)
    # Convert timestamp_added to ISO string for JSON serialization
    if data.get("timestamp_added") and hasattr(data["timestamp_added"], "isoformat"):
        data["timestamp_added"] = data["timestamp_added"].isoformat()
    return data  # type: ignore[return-value]


def deserialize_label(data: SerializedLabel) -> ReceiptWordLabel:
    """Deserialize ReceiptWordLabel from S3 data."""
    # Make a mutable copy to avoid modifying the input
    label_data = dict(data)
    # Parse timestamp string back to datetime
    if isinstance(label_data.get("timestamp_added"), str):
        try:
            label_data["timestamp_added"] = datetime.fromisoformat(
                label_data["timestamp_added"]
            )
        except ValueError:
            label_data["timestamp_added"] = None
    return ReceiptWordLabel(**label_data)


def serialize_place(place: ReceiptPlace) -> SerializedPlace:
    """Serialize ReceiptPlace for S3 storage using asdict."""
    data = asdict(place)
    # Convert datetime to ISO string
    if data.get("timestamp") and hasattr(data["timestamp"], "isoformat"):
        data["timestamp"] = data["timestamp"].isoformat()
    return data  # type: ignore[return-value]


def deserialize_place(data: SerializedPlace | None) -> ReceiptPlace | None:
    """Deserialize ReceiptPlace from S3 data."""
    if not data:
        return None

    # Make a mutable copy to avoid modifying the input
    place_data = dict(data)
    # Parse timestamp string back to datetime
    if isinstance(place_data.get("timestamp"), str):
        try:
            # Handle both with and without timezone
            ts = place_data["timestamp"]
            if ts.endswith("+00:00"):
                ts = ts.replace("+00:00", "")
            place_data["timestamp"] = datetime.fromisoformat(ts)
        except ValueError:
            place_data["timestamp"] = datetime.now(timezone.utc)
    elif place_data.get("timestamp") is None:
        place_data["timestamp"] = datetime.now(timezone.utc)

    return ReceiptPlace(**place_data)


def serialize_words(words: list[ReceiptWord]) -> list[SerializedWord]:
    """Serialize a list of ReceiptWord objects."""
    return [serialize_word(w) for w in words]


def deserialize_words(data: list[SerializedWord]) -> list[ReceiptWord]:
    """Deserialize a list of ReceiptWord objects."""
    return [deserialize_word(d) for d in data]


def serialize_labels(labels: list[ReceiptWordLabel]) -> list[SerializedLabel]:
    """Serialize a list of ReceiptWordLabel objects."""
    return [serialize_label(label) for label in labels]


def deserialize_labels(data: list[SerializedLabel]) -> list[ReceiptWordLabel]:
    """Deserialize a list of ReceiptWordLabel objects."""
    return [deserialize_label(d) for d in data]


def deserialize_patterns(data: PatternsFile | None):
    """
    Deserialize pre-computed MerchantPatterns from S3.

    The serialized format contains pre-computed statistics (mean, std, count)
    rather than raw observations, allowing fast pattern loading.

    Args:
        data: Patterns file loaded from S3, or None.

    Returns:
        MerchantPatterns object or None if patterns is null.
    """
    if not data or data.get("patterns") is None:
        return None

    from receipt_agent.agents.label_evaluator.state import (
        ConstellationGeometry,
        LabelPairGeometry,
        LabelRelativePosition,
        MerchantPatterns,
    )

    p = data["patterns"]

    # Reconstruct label_positions as dict[str, list[float]]
    # We store stats but need to create synthetic positions for compatibility
    # For evaluation, we only need mean_y and std_y, so store those
    label_positions: dict[str, list[float]] = {}
    label_position_stats = p.get("label_positions", {})
    for label, stats in label_position_stats.items():
        # Store the mean as a single-element list (patterns uses mean internally)
        # The evaluator will use the stats we attach separately
        label_positions[label] = [stats["mean_y"]]

    # Reconstruct label_pair_geometry
    label_pair_geometry: dict[tuple, LabelPairGeometry] = {}
    for geom_data in p.get("label_pair_geometry", []):
        pair_key = tuple(geom_data["labels"])
        geom = LabelPairGeometry(
            observations=[],  # Not stored in serialized form
            mean_angle=geom_data.get("mean_angle"),
            mean_distance=geom_data.get("mean_distance"),
            std_angle=geom_data.get("std_angle"),
            std_distance=geom_data.get("std_distance"),
            mean_dx=geom_data.get("mean_dx"),
            mean_dy=geom_data.get("mean_dy"),
            std_dx=geom_data.get("std_dx"),
            std_dy=geom_data.get("std_dy"),
        )
        label_pair_geometry[pair_key] = geom

    # Reconstruct constellation_geometry
    constellation_geometry: dict[tuple, ConstellationGeometry] = {}
    for cg_data in p.get("constellation_geometry", []):
        labels_key = tuple(cg_data["labels"])
        relative_positions = {}
        for label, rel_data in cg_data.get("relative_positions", {}).items():
            relative_positions[label] = LabelRelativePosition(
                mean_dx=rel_data.get("mean_dx", 0.0),
                mean_dy=rel_data.get("mean_dy", 0.0),
                std_dx=rel_data.get("std_dx", 0.0),
                std_dy=rel_data.get("std_dy", 0.0),
            )
        constellation_geometry[labels_key] = ConstellationGeometry(
            labels=labels_key,
            observation_count=cg_data.get("observation_count", 0),
            relative_positions=relative_positions,
        )

    # Reconstruct all_observed_pairs
    all_observed_pairs = set()
    for pair in p.get("all_observed_pairs", []):
        all_observed_pairs.add(tuple(pair))

    # Reconstruct batch_classification
    batch_classification = p.get("batch_classification", {
        "HAPPY": 0, "AMBIGUOUS": 0, "ANTI_PATTERN": 0
    })

    # Reconstruct labels_with_same_line_multiplicity
    labels_with_multiplicity = set(
        p.get("labels_with_same_line_multiplicity", [])
    )

    patterns = MerchantPatterns(
        merchant_name=p.get("merchant_name", ""),
        receipt_count=p.get("receipt_count", 0),
        label_positions=label_positions,
        label_pair_geometry=label_pair_geometry,
        all_observed_pairs=all_observed_pairs,
        constellation_geometry=constellation_geometry,
        batch_classification=batch_classification,
        labels_with_same_line_multiplicity=labels_with_multiplicity,
    )

    return patterns
