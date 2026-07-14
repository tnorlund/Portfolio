"""Contract tests for receipt-word-label spatial analysis records."""

from datetime import datetime

import pytest

from receipt_dynamo.entities import (
    ReceiptWordLabelSpatialAnalysis,
    SpatialRelationship,
    item_to_receipt_word_label_spatial_analysis,
)

pytestmark = pytest.mark.unit

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


def make_relationship(**overrides):
    values = {
        "to_label": "TAX",
        "to_line_id": 10,
        "to_word_id": 42,
        "distance": 0.05,
        "angle": 1.57,
    }
    values.update(overrides)
    return SpatialRelationship(**values)


def make_analysis(**overrides):
    values = {
        "image_id": IMAGE_ID,
        "receipt_id": 1,
        "line_id": 12,
        "word_id": 45,
        "from_label": "GRAND_TOTAL",
        "from_position": {"x": 0.8, "y": 0.9},
        "spatial_relationships": [
            make_relationship(),
            make_relationship(
                to_label="SUBTOTAL",
                to_line_id=8,
                to_word_id=39,
                distance=0.08,
            ),
        ],
        "timestamp_added": "2021-01-01T00:00:00",
        "analysis_version": "1.0",
    }
    values.update(overrides)
    return ReceiptWordLabelSpatialAnalysis(**values)


@pytest.fixture
def example_spatial_relationship():
    return make_relationship()


@pytest.fixture
def example_spatial_analysis():
    return make_analysis()


def test_spatial_relationship_normalizes_label_and_numbers():
    relationship = make_relationship(to_label="tax", distance=1, angle=0)

    assert relationship.to_label == "TAX"
    assert relationship.distance == 1.0
    assert relationship.angle == 0.0


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("to_label", "", "to_label must be a non-empty string"),
        ("to_label", 123, "to_label must be a non-empty string"),
        ("to_line_id", 0, "to_line_id must be positive"),
        ("to_line_id", -1, "to_line_id must be positive"),
        ("to_line_id", True, "to_line_id must be an integer"),
        ("to_word_id", 0, "to_word_id must be positive"),
        ("to_word_id", "1", "to_word_id must be an integer"),
        ("to_word_id", False, "to_word_id must be an integer"),
        ("distance", -0.1, "distance must be a non-negative number"),
        ("distance", "near", "distance must be a non-negative number"),
        ("distance", True, "distance must be a non-negative number"),
        ("distance", float("nan"), "distance must be a non-negative number"),
        ("distance", float("inf"), "distance must be a non-negative number"),
        ("angle", "north", "angle must be a number"),
        ("angle", False, "angle must be a number"),
        ("angle", float("nan"), "angle must be a number"),
        ("angle", float("inf"), "angle must be a number"),
    ],
)
def test_spatial_relationship_rejects_invalid_fields(field, value, match):
    with pytest.raises(ValueError, match=match):
        make_relationship(**{field: value})


def test_spatial_analysis_normalizes_and_copies_mutable_inputs():
    position = {"x": 1, "y": 2}
    relationships = [make_relationship()]
    analysis = make_analysis(
        from_label="grand_total",
        from_position=position,
        spatial_relationships=relationships,
        timestamp_added=datetime(2021, 1, 1, 12, 30, 45),
    )
    position["x"] = 99
    relationships.clear()

    assert analysis.from_label == "GRAND_TOTAL"
    assert analysis.from_position == {"x": 1.0, "y": 2.0}
    assert len(analysis.spatial_relationships) == 1
    assert analysis.timestamp_added == "2021-01-01T12:30:45"


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("image_id", "bad", "uuid must be a valid UUIDv4"),
        ("receipt_id", 0, "receipt_id must be positive"),
        ("receipt_id", True, "receipt_id must be an integer"),
        ("line_id", -1, "line_id must be positive"),
        ("line_id", "1", "line_id must be an integer"),
        ("word_id", 0, "word_id must be positive"),
        ("word_id", False, "word_id must be an integer"),
        ("from_label", "", "from_label must be a non-empty string"),
        ("from_label", 1, "from_label must be a non-empty string"),
        ("spatial_relationships", None, "must be a list"),
        (
            "spatial_relationships",
            ["not-a-relationship"],
            "must be a SpatialRelationship",
        ),
        ("timestamp_added", 1, "timestamp_added must be a datetime"),
        ("timestamp_added", "today", "valid ISO format timestamp"),
        (
            "analysis_version",
            "",
            "analysis_version must be a non-empty string",
        ),
        ("analysis_version", 1, "analysis_version must be a non-empty string"),
    ],
)
def test_spatial_analysis_rejects_invalid_scalar_fields(field, value, match):
    with pytest.raises(ValueError, match=match):
        make_analysis(**{field: value})


@pytest.mark.parametrize(
    "position,match",
    [
        (None, "from_position must be a dictionary"),
        ({"x": 1}, "must contain exactly"),
        ({"x": 1, "y": 2, "z": 3}, "must contain exactly"),
        ({"x": True, "y": 2}, r"from_position\['x'\] must be a finite number"),
        ({"x": "1", "y": 2}, r"from_position\['x'\] must be a finite number"),
        (
            {"x": float("nan"), "y": 2},
            r"from_position\['x'\] must be a finite number",
        ),
        (
            {"x": 1, "y": float("inf")},
            r"from_position\['y'\] must be a finite number",
        ),
    ],
)
def test_spatial_analysis_rejects_invalid_position_maps(position, match):
    with pytest.raises(ValueError, match=match):
        make_analysis(from_position=position)


def test_spatial_analysis_primary_and_gsi_keys_are_exact(
    example_spatial_analysis,
):
    analysis = example_spatial_analysis
    assert analysis.key == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": "RECEIPT#00001#LINE#00012#WORD#00045#SPATIAL_ANALYSIS"},
    }
    assert analysis.gsi1_key() == {
        "GSI1PK": {"S": "SPATIAL_ANALYSIS#GRAND_TOTAL"},
        "GSI1SK": {
            "S": (
                f"IMAGE#{IMAGE_ID}#RECEIPT#00001#"
                "TIMESTAMP#2021-01-01T00:00:00"
            )
        },
    }
    assert analysis.gsi2_key() == {
        "GSI2PK": {"S": f"IMAGE#{IMAGE_ID}#RECEIPT#00001#SPATIAL"},
        "GSI2SK": {"S": "LABEL#GRAND_TOTAL#LINE#00012#WORD#00045"},
    }


def test_spatial_analysis_item_has_exact_nested_schema(
    example_spatial_analysis,
):
    item = example_spatial_analysis.to_item()

    assert set(item) == {
        "PK",
        "SK",
        "GSI1PK",
        "GSI1SK",
        "GSI2PK",
        "GSI2SK",
        "TYPE",
        "from_label",
        "from_position",
        "spatial_relationships",
        "relationships_count",
        "timestamp_added",
        "analysis_version",
    }
    assert item["TYPE"] == {"S": "RECEIPT_WORD_LABEL_SPATIAL_ANALYSIS"}
    assert item["from_position"] == {
        "M": {"x": {"N": "0.8"}, "y": {"N": "0.9"}}
    }
    assert item["relationships_count"] == {"N": "2"}
    first = item["spatial_relationships"]["L"][0]["M"]
    assert set(first) == {
        "to_label",
        "to_line_id",
        "to_word_id",
        "distance",
        "angle",
    }
    assert first["to_label"] == {"S": "TAX"}


@pytest.mark.parametrize(
    "relationships",
    [
        [],
        [make_relationship()],
        [make_relationship(), make_relationship(to_label="SUBTOTAL")],
    ],
)
def test_spatial_analysis_round_trip_preserves_nested_records(relationships):
    analysis = make_analysis(spatial_relationships=relationships)
    item = analysis.to_item()
    restored = item_to_receipt_word_label_spatial_analysis(item)

    assert restored == analysis
    assert restored.to_item() == item
    assert hash(restored) == hash(analysis)


@pytest.mark.parametrize(
    "field,value",
    [
        ("receipt_id", 2),
        ("line_id", 13),
        ("word_id", 46),
        ("from_label", "TAX"),
        ("from_position", {"x": 0.1, "y": 0.2}),
        ("spatial_relationships", []),
        ("timestamp_added", "2021-01-01T00:00:01"),
        ("analysis_version", "2.0"),
    ],
)
def test_spatial_analysis_equality_includes_every_field(field, value):
    assert make_analysis() != make_analysis(**{field: value})


def test_spatial_analysis_iteration_and_repr(example_spatial_analysis):
    mapping = dict(example_spatial_analysis)
    representation = repr(example_spatial_analysis)

    assert mapping["from_position"] == {"x": 0.8, "y": 0.9}
    assert len(mapping["spatial_relationships"]) == 2
    assert representation.startswith(
        f"ReceiptWordLabelSpatialAnalysis(image_id='{IMAGE_ID}'"
    )
    assert "relationships_count=2" in representation


def test_spatial_analysis_has_no_gsi3_keys(example_spatial_analysis):
    item = example_spatial_analysis.to_item()
    assert "GSI3PK" not in item
    assert "GSI3SK" not in item


@pytest.mark.parametrize(
    "missing", ["PK", "SK", "from_position", "analysis_version"]
)
def test_spatial_analysis_from_item_rejects_missing_keys(
    example_spatial_analysis, missing
):
    item = example_spatial_analysis.to_item()
    item.pop(missing)

    with pytest.raises(ValueError, match="Missing keys"):
        item_to_receipt_word_label_spatial_analysis(item)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("PK", {"S": "NOT_IMAGE#value"}, "Invalid PK format"),
        ("SK", {"S": "RECEIPT#1#BAD"}, "Invalid SK format"),
        (
            "from_position",
            {"M": {"x": {"N": "nan"}, "y": {"N": "1"}}},
            "finite number",
        ),
    ],
)
def test_spatial_analysis_from_item_rejects_malformed_values(
    example_spatial_analysis, field, value, match
):
    item = example_spatial_analysis.to_item()
    item[field] = value

    with pytest.raises(ValueError, match=match):
        item_to_receipt_word_label_spatial_analysis(item)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (
            lambda analysis: analysis.from_position.update(x=float("nan")),
            "finite number",
        ),
        (
            lambda analysis: analysis.from_position.update(z=1),
            "must contain exactly",
        ),
        (
            lambda analysis: setattr(
                analysis.spatial_relationships[0], "distance", float("inf")
            ),
            "distance must be a non-negative number",
        ),
        (
            lambda analysis: analysis.spatial_relationships.append("bad"),
            "must be a SpatialRelationship",
        ),
    ],
)
def test_spatial_analysis_serialization_revalidates_mutable_nested_state(
    example_spatial_analysis, mutation, match
):
    mutation(example_spatial_analysis)

    with pytest.raises(ValueError, match=match):
        example_spatial_analysis.to_item()
