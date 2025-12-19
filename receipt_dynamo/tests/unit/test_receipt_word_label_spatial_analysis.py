from datetime import datetime

import pytest

from receipt_dynamo.entities import (
    ReceiptWordLabelSpatialAnalysis,
    SpatialRelationship,
    item_to_receipt_word_label_spatial_analysis,
)


@pytest.fixture
def example_spatial_relationship():
    """Example SpatialRelationship for testing."""
    return SpatialRelationship(
        to_label="TAX",
        to_line_id=10,
        to_word_id=42,
        distance=0.05,
        angle=1.57,
    )


@pytest.fixture
def example_spatial_analysis():
    """Example ReceiptWordLabelSpatialAnalysis for testing."""
    return ReceiptWordLabelSpatialAnalysis(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        line_id=12,
        word_id=45,
        from_label="GRAND_TOTAL",
        from_position={"x": 0.8, "y": 0.9},
        spatial_relationships=[
            SpatialRelationship(
                to_label="TAX",
                to_line_id=10,
                to_word_id=42,
                distance=0.05,
                angle=1.57,
            ),
            SpatialRelationship(
                to_label="SUBTOTAL",
                to_line_id=8,
                to_word_id=39,
                distance=0.08,
                angle=1.57,
            ),
        ],
        timestamp_added="2021-01-01T00:00:00",
        analysis_version="1.0",
    )


# ===== SpatialRelationship Tests =====


@pytest.mark.unit
def test_spatial_relationship_init_valid(example_spatial_relationship):
    """Test constructing a valid SpatialRelationship."""
    assert example_spatial_relationship.to_label == "TAX"
    assert example_spatial_relationship.to_line_id == 10
    assert example_spatial_relationship.to_word_id == 42
    assert example_spatial_relationship.distance == 0.05
    assert example_spatial_relationship.angle == 1.57


@pytest.mark.unit
def test_spatial_relationship_init_invalid_to_label():
    """SpatialRelationship with invalid to_label raises ValueError."""
    # Empty label
    with pytest.raises(
        ValueError, match="to_label must be a non-empty string"
    ):
        SpatialRelationship(
            to_label="",
            to_line_id=10,
            to_word_id=42,
            distance=0.05,
            angle=1.57,
        )

    # Non-string label
    with pytest.raises(
        ValueError, match="to_label must be a non-empty string"
    ):
        SpatialRelationship(
            to_label=123,
            to_line_id=10,
            to_word_id=42,
            distance=0.05,
            angle=1.57,
        )


@pytest.mark.unit
def test_spatial_relationship_init_invalid_ids():
    """SpatialRelationship with invalid IDs raises ValueError."""
    # Invalid to_line_id
    with pytest.raises(ValueError, match="to_line_id must be positive"):
        SpatialRelationship(
            to_label="TAX",
            to_line_id=-10,
            to_word_id=42,
            distance=0.05,
            angle=1.57,
        )

    # Invalid to_word_id
    with pytest.raises(ValueError, match="to_word_id must be positive"):
        SpatialRelationship(
            to_label="TAX",
            to_line_id=10,
            to_word_id=0,
            distance=0.05,
            angle=1.57,
        )


@pytest.mark.unit
def test_spatial_relationship_init_invalid_distance():
    """SpatialRelationship with invalid distance raises ValueError."""
    with pytest.raises(
        ValueError, match="distance must be a non-negative number"
    ):
        SpatialRelationship(
            to_label="TAX",
            to_line_id=10,
            to_word_id=42,
            distance=-0.05,
            angle=1.57,
        )


@pytest.mark.unit
def test_spatial_relationship_init_invalid_angle():
    """SpatialRelationship with invalid angle raises ValueError."""
    with pytest.raises(ValueError, match="angle must be a number"):
        SpatialRelationship(
            to_label="TAX",
            to_line_id=10,
            to_word_id=42,
            distance=0.05,
            angle="not_a_number",
        )


@pytest.mark.unit
def test_spatial_relationship_label_uppercased():
    """Test that SpatialRelationship automatically uppercases labels."""
    rel = SpatialRelationship(
        to_label="tax",
        to_line_id=10,
        to_word_id=42,
        distance=0.05,
        angle=1.57,
    )
    assert rel.to_label == "TAX"


# ===== ReceiptWordLabelSpatialAnalysis Tests =====


@pytest.mark.unit
def test_spatial_analysis_init_valid(example_spatial_analysis):
    """Test constructing a valid ReceiptWordLabelSpatialAnalysis."""
    assert (
        example_spatial_analysis.image_id
        == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert example_spatial_analysis.receipt_id == 1
    assert example_spatial_analysis.line_id == 12
    assert example_spatial_analysis.word_id == 45
    assert example_spatial_analysis.from_label == "GRAND_TOTAL"
    assert example_spatial_analysis.from_position == {"x": 0.8, "y": 0.9}
    assert len(example_spatial_analysis.spatial_relationships) == 2
    assert example_spatial_analysis.timestamp_added == "2021-01-01T00:00:00"
    assert example_spatial_analysis.analysis_version == "1.0"


@pytest.mark.unit
def test_spatial_analysis_init_datetime_conversion():
    """Test that datetime is converted to ISO string format."""
    dt = datetime(2021, 1, 1, 12, 30, 45)
    analysis = ReceiptWordLabelSpatialAnalysis(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        line_id=12,
        word_id=45,
        from_label="GRAND_TOTAL",
        from_position={"x": 0.8, "y": 0.9},
        spatial_relationships=[],
        timestamp_added=dt,
    )
    assert analysis.timestamp_added == "2021-01-01T12:30:45"


@pytest.mark.unit
def test_spatial_analysis_init_invalid_image_id():
    """ReceiptWordLabelSpatialAnalysis with invalid image_id raises ValueError."""
    with pytest.raises(ValueError, match="uuid must be a valid UUIDv4"):
        ReceiptWordLabelSpatialAnalysis(
            image_id="not-a-uuid",
            receipt_id=1,
            line_id=12,
            word_id=45,
            from_label="GRAND_TOTAL",
            from_position={"x": 0.8, "y": 0.9},
            spatial_relationships=[],
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_spatial_analysis_init_invalid_ids():
    """ReceiptWordLabelSpatialAnalysis with invalid IDs raises ValueError."""
    # Invalid receipt_id
    with pytest.raises(ValueError, match="receipt_id must be positive"):
        ReceiptWordLabelSpatialAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=-1,
            line_id=12,
            word_id=45,
            from_label="GRAND_TOTAL",
            from_position={"x": 0.8, "y": 0.9},
            spatial_relationships=[],
            timestamp_added="2021-01-01T00:00:00",
        )

    # Invalid line_id
    with pytest.raises(ValueError, match="line_id must be positive"):
        ReceiptWordLabelSpatialAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=0,
            word_id=45,
            from_label="GRAND_TOTAL",
            from_position={"x": 0.8, "y": 0.9},
            spatial_relationships=[],
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_spatial_analysis_init_invalid_from_label():
    """ReceiptWordLabelSpatialAnalysis with invalid from_label raises ValueError."""
    # Empty label
    with pytest.raises(
        ValueError, match="from_label must be a non-empty string"
    ):
        ReceiptWordLabelSpatialAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=12,
            word_id=45,
            from_label="",
            from_position={"x": 0.8, "y": 0.9},
            spatial_relationships=[],
            timestamp_added="2021-01-01T00:00:00",
        )

    # Non-string label
    with pytest.raises(
        ValueError, match="from_label must be a non-empty string"
    ):
        ReceiptWordLabelSpatialAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=12,
            word_id=45,
            from_label=123,
            from_position={"x": 0.8, "y": 0.9},
            spatial_relationships=[],
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_spatial_analysis_init_invalid_position():
    """ReceiptWordLabelSpatialAnalysis with invalid from_position raises ValueError."""
    # Non-dict position
    with pytest.raises(ValueError, match="from_position must be a dictionary"):
        ReceiptWordLabelSpatialAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=12,
            word_id=45,
            from_label="GRAND_TOTAL",
            from_position="not_a_dict",
            spatial_relationships=[],
            timestamp_added="2021-01-01T00:00:00",
        )

    # Missing required keys
    with pytest.raises(
        ValueError, match="from_position must contain 'x' and 'y' keys"
    ):
        ReceiptWordLabelSpatialAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=12,
            word_id=45,
            from_label="GRAND_TOTAL",
            from_position={"x": 0.8},  # Missing 'y'
            spatial_relationships=[],
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_spatial_analysis_init_invalid_relationships():
    """ReceiptWordLabelSpatialAnalysis with invalid spatial_relationships raises ValueError."""
    # Non-list relationships
    with pytest.raises(
        ValueError, match="spatial_relationships must be a list"
    ):
        ReceiptWordLabelSpatialAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=12,
            word_id=45,
            from_label="GRAND_TOTAL",
            from_position={"x": 0.8, "y": 0.9},
            spatial_relationships="not_a_list",
            timestamp_added="2021-01-01T00:00:00",
        )

    # List with non-SpatialRelationship objects
    with pytest.raises(
        ValueError,
        match="spatial_relationships\\[0\\] must be a SpatialRelationship",
    ):
        ReceiptWordLabelSpatialAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=12,
            word_id=45,
            from_label="GRAND_TOTAL",
            from_position={"x": 0.8, "y": 0.9},
            spatial_relationships=["not_a_relationship"],
            timestamp_added="2021-01-01T00:00:00",
        )


@pytest.mark.unit
def test_spatial_analysis_init_invalid_timestamp():
    """ReceiptWordLabelSpatialAnalysis with invalid timestamp raises ValueError."""
    # Invalid string format
    with pytest.raises(
        ValueError, match="timestamp_added string must be in ISO format"
    ):
        ReceiptWordLabelSpatialAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=12,
            word_id=45,
            from_label="GRAND_TOTAL",
            from_position={"x": 0.8, "y": 0.9},
            spatial_relationships=[],
            timestamp_added="not-iso-format",
        )

    # Non-string, non-datetime
    with pytest.raises(
        ValueError,
        match="timestamp_added must be a datetime object or a string",
    ):
        ReceiptWordLabelSpatialAnalysis(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            line_id=12,
            word_id=45,
            from_label="GRAND_TOTAL",
            from_position={"x": 0.8, "y": 0.9},
            spatial_relationships=[],
            timestamp_added=123456,
        )


@pytest.mark.unit
def test_spatial_analysis_label_uppercased():
    """Test that ReceiptWordLabelSpatialAnalysis automatically uppercases from_label."""
    analysis = ReceiptWordLabelSpatialAnalysis(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        line_id=12,
        word_id=45,
        from_label="grand_total",
        from_position={"x": 0.8, "y": 0.9},
        spatial_relationships=[],
        timestamp_added="2021-01-01T00:00:00",
    )
    assert analysis.from_label == "GRAND_TOTAL"


# ===== Key Generation Tests =====


@pytest.mark.unit
def test_spatial_analysis_key(example_spatial_analysis):
    """Test primary key generation."""
    key = example_spatial_analysis.key
    expected = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001#LINE#00012#WORD#00045#SPATIAL_ANALYSIS"},
    }
    assert key == expected


@pytest.mark.unit
def test_spatial_analysis_gsi1_key(example_spatial_analysis):
    """Test GSI1 key generation."""
    key = example_spatial_analysis.gsi1_key()
    expected = {
        "GSI1PK": {"S": "SPATIAL_ANALYSIS#GRAND_TOTAL"},
        "GSI1SK": {
            "S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001#TIMESTAMP#2021-01-01T00:00:00"
        },
    }
    assert key == expected


@pytest.mark.unit
def test_spatial_analysis_gsi2_key(example_spatial_analysis):
    """Test GSI2 key generation."""
    key = example_spatial_analysis.gsi2_key()
    expected = {
        "GSI2PK": {
            "S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#RECEIPT#00001#SPATIAL"
        },
        "GSI2SK": {"S": "LABEL#GRAND_TOTAL#LINE#00012#WORD#00045"},
    }
    assert key == expected


# ===== Serialization Tests =====


@pytest.mark.unit
def test_spatial_analysis_to_item(example_spatial_analysis):
    """Test conversion to DynamoDB item."""
    item = example_spatial_analysis.to_item()

    # Check basic structure
    assert "PK" in item
    assert "SK" in item
    assert "GSI1PK" in item
    assert "GSI1SK" in item
    assert "GSI2PK" in item
    assert "GSI2SK" in item
    assert "TYPE" in item
    assert item["TYPE"]["S"] == "RECEIPT_WORD_LABEL_SPATIAL_ANALYSIS"

    # Check data fields
    assert item["from_label"]["S"] == "GRAND_TOTAL"
    assert item["from_position"]["M"]["x"]["N"] == "0.8"
    assert item["from_position"]["M"]["y"]["N"] == "0.9"
    assert item["timestamp_added"]["S"] == "2021-01-01T00:00:00"
    assert item["analysis_version"]["S"] == "1.0"
    assert item["relationships_count"]["N"] == "2"

    # Check spatial relationships
    assert len(item["spatial_relationships"]["L"]) == 2
    first_rel = item["spatial_relationships"]["L"][0]["M"]
    assert first_rel["to_label"]["S"] == "TAX"
    assert first_rel["to_line_id"]["N"] == "10"
    assert first_rel["to_word_id"]["N"] == "42"
    assert first_rel["distance"]["N"] == "0.05"
    assert first_rel["angle"]["N"] == "1.57"


@pytest.mark.unit
def test_spatial_analysis_no_gsi3_keys(example_spatial_analysis):
    """Test that GSI3 keys are not included in the item."""
    item = example_spatial_analysis.to_item()
    gsi3_keys = [key for key in item.keys() if "GSI3" in key]
    assert gsi3_keys == []


# ===== Deserialization Tests =====


@pytest.mark.unit
def test_item_to_spatial_analysis_valid_input(example_spatial_analysis):
    """Test item_to_receipt_word_label_spatial_analysis with valid DynamoDB item."""
    item = example_spatial_analysis.to_item()
    reconstructed = item_to_receipt_word_label_spatial_analysis(item)
    assert reconstructed == example_spatial_analysis


@pytest.mark.unit
def test_item_to_spatial_analysis_missing_keys():
    """item_to_receipt_word_label_spatial_analysis raises ValueError when required keys are missing."""
    incomplete_item = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001#LINE#00012#WORD#00045#SPATIAL_ANALYSIS"},
    }
    with pytest.raises(ValueError, match="Invalid item format. Missing keys"):
        item_to_receipt_word_label_spatial_analysis(incomplete_item)


@pytest.mark.unit
def test_item_to_spatial_analysis_invalid_sk_format():
    """item_to_receipt_word_label_spatial_analysis raises ValueError with invalid SK format."""
    invalid_item = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "INVALID#FORMAT"},
        "from_label": {"S": "GRAND_TOTAL"},
        "from_position": {"M": {"x": {"N": "0.8"}, "y": {"N": "0.9"}}},
        "spatial_relationships": {"L": []},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
        "analysis_version": {"S": "1.0"},
    }
    with pytest.raises(ValueError, match="Invalid SK format"):
        item_to_receipt_word_label_spatial_analysis(invalid_item)


@pytest.mark.unit
def test_item_to_spatial_analysis_invalid_pk_format():
    """item_to_receipt_word_label_spatial_analysis raises ValueError with invalid PK format."""
    invalid_item = {
        "PK": {"S": "INVALID#FORMAT"},
        "SK": {"S": "RECEIPT#00001#LINE#00012#WORD#00045#SPATIAL_ANALYSIS"},
        "from_label": {"S": "GRAND_TOTAL"},
        "from_position": {"M": {"x": {"N": "0.8"}, "y": {"N": "0.9"}}},
        "spatial_relationships": {"L": []},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
        "analysis_version": {"S": "1.0"},
    }
    with pytest.raises(ValueError, match="Invalid PK format"):
        item_to_receipt_word_label_spatial_analysis(invalid_item)


# ===== Equality and Hashing Tests =====


@pytest.mark.unit
def test_spatial_analysis_eq(example_spatial_analysis):
    """Test that ReceiptWordLabelSpatialAnalysis equality works as expected."""
    # Test equality with same data
    same_analysis = ReceiptWordLabelSpatialAnalysis(
        **dict(example_spatial_analysis)
    )
    assert example_spatial_analysis == same_analysis

    # Test inequality with different data
    different_analysis = ReceiptWordLabelSpatialAnalysis(
        **dict(example_spatial_analysis, receipt_id=2)
    )
    assert example_spatial_analysis != different_analysis

    # Test inequality with non-ReceiptWordLabelSpatialAnalysis
    assert example_spatial_analysis != "not_a_spatial_analysis"
    assert example_spatial_analysis is not None


@pytest.mark.unit
def test_spatial_analysis_hash(example_spatial_analysis):
    """Test that ReceiptWordLabelSpatialAnalysis hashing works for set operations."""
    # Should be hashable and consistent
    hash1 = hash(example_spatial_analysis)
    hash2 = hash(example_spatial_analysis)
    assert hash1 == hash2

    # Different objects should have different hashes (usually)
    different_analysis = ReceiptWordLabelSpatialAnalysis(
        **dict(example_spatial_analysis, receipt_id=2)
    )
    assert hash(example_spatial_analysis) != hash(different_analysis)


# ===== Iterator Tests =====


@pytest.mark.unit
def test_spatial_analysis_iter(example_spatial_analysis):
    """Test that ReceiptWordLabelSpatialAnalysis can be converted to dict."""
    analysis_dict = dict(example_spatial_analysis)
    expected_keys = {
        "image_id",
        "receipt_id",
        "line_id",
        "word_id",
        "from_label",
        "from_position",
        "spatial_relationships",
        "timestamp_added",
        "analysis_version",
    }
    assert set(analysis_dict.keys()) == expected_keys
    assert analysis_dict["image_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert analysis_dict["from_label"] == "GRAND_TOTAL"


# ===== Repr Tests =====


@pytest.mark.unit
def test_spatial_analysis_repr(example_spatial_analysis):
    """Test string representation of ReceiptWordLabelSpatialAnalysis."""
    repr_str = repr(example_spatial_analysis)
    assert "ReceiptWordLabelSpatialAnalysis" in repr_str
    assert "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3'" in repr_str
    assert "from_label='GRAND_TOTAL'" in repr_str
    assert "relationships_count=2" in repr_str
