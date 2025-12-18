"""Unit tests for entity serialization utilities."""

import pytest

from receipt_dynamo.entities.util import (
    build_base_item,
    deserialize_bounding_box,
    deserialize_confidence,
    deserialize_coordinate_point,
    serialize_bounding_box,
    serialize_confidence,
    serialize_coordinate_point,
)


class TestSerializeBoundingBox:
    """Test cases for serialize_bounding_box utility function."""

    @pytest.mark.unit
    def test_serialize_bounding_box_basic(self):
        """Test basic bounding box serialization."""
        bounding_box = {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
        result = serialize_bounding_box(bounding_box)

        expected = {
            "M": {
                "x": {"N": "0.10000000000000000000"},
                "y": {"N": "0.20000000000000000000"},
                "width": {"N": "0.30000000000000000000"},
                "height": {"N": "0.40000000000000000000"},
            }
        }
        assert result == expected

    @pytest.mark.unit
    def test_serialize_bounding_box_zero_values(self):
        """Test bounding box serialization with zero values."""
        bounding_box = {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}
        result = serialize_bounding_box(bounding_box)

        expected = {
            "M": {
                "x": {"N": "0.00000000000000000000"},
                "y": {"N": "0.00000000000000000000"},
                "width": {"N": "0.00000000000000000000"},
                "height": {"N": "0.00000000000000000000"},
            }
        }
        assert result == expected

    @pytest.mark.unit
    def test_serialize_bounding_box_large_values(self):
        """Test bounding box serialization with large values."""
        bounding_box = {
            "x": 999.999,
            "y": 888.888,
            "width": 777.777,
            "height": 666.666,
        }
        result = serialize_bounding_box(bounding_box)

        # Verify structure and that values are properly formatted
        assert "M" in result
        assert len(result["M"]) == 4
        assert all(key in result["M"] for key in ["x", "y", "width", "height"])
        assert all("N" in result["M"][key] for key in ["x", "y", "width", "height"])

        # Verify specific values are correctly formatted
        assert result["M"]["x"]["N"] == "999.99900000000000000000"
        assert result["M"]["y"]["N"] == "888.88800000000000000000"

    @pytest.mark.unit
    def test_serialize_bounding_box_precision(self):
        """Test that bounding box serialization maintains proper decimal precision."""
        bounding_box = {
            "x": 0.123456789012345,
            "y": 0.987654321098765,
            "width": 0.555555555555555,
            "height": 0.111111111111111,
        }
        result = serialize_bounding_box(bounding_box)

        # Verify 20 decimal places are maintained
        for key in ["x", "y", "width", "height"]:
            n_value = result["M"][key]["N"]
            decimal_part = n_value.split(".")[1]
            assert len(decimal_part) == 20


class TestSerializeCoordinatePoint:
    """Test cases for serialize_coordinate_point utility function."""

    @pytest.mark.unit
    def test_serialize_coordinate_point_basic(self):
        """Test basic coordinate point serialization."""
        point = {"x": 0.5, "y": 0.7}
        result = serialize_coordinate_point(point)

        expected = {
            "M": {
                "x": {"N": "0.50000000000000000000"},
                "y": {"N": "0.70000000000000000000"},
            }
        }
        assert result == expected

    @pytest.mark.unit
    def test_serialize_coordinate_point_zero(self):
        """Test coordinate point serialization with zero values."""
        point = {"x": 0.0, "y": 0.0}
        result = serialize_coordinate_point(point)

        expected = {
            "M": {
                "x": {"N": "0.00000000000000000000"},
                "y": {"N": "0.00000000000000000000"},
            }
        }
        assert result == expected

    @pytest.mark.unit
    def test_serialize_coordinate_point_negative(self):
        """Test coordinate point serialization with negative values."""
        point = {"x": -0.25, "y": -0.75}
        result = serialize_coordinate_point(point)

        expected = {
            "M": {
                "x": {"N": "-0.25000000000000000000"},
                "y": {"N": "-0.75000000000000000000"},
            }
        }
        assert result == expected

    @pytest.mark.unit
    def test_serialize_coordinate_point_large_values(self):
        """Test coordinate point serialization with large values."""
        point = {"x": 1234.5678, "y": 9876.5432}
        result = serialize_coordinate_point(point)

        assert "M" in result
        assert len(result["M"]) == 2
        assert "x" in result["M"] and "y" in result["M"]
        assert "N" in result["M"]["x"] and "N" in result["M"]["y"]

    @pytest.mark.unit
    def test_serialize_coordinate_point_precision(self):
        """Test that coordinate point serialization maintains proper precision."""
        point = {"x": 0.123456789012345, "y": 0.987654321098765}
        result = serialize_coordinate_point(point)

        # Verify 20 decimal places are maintained
        for key in ["x", "y"]:
            n_value = result["M"][key]["N"]
            decimal_part = n_value.split(".")[1]
            assert len(decimal_part) == 20


class TestSerializeConfidence:
    """Test cases for serialize_confidence utility function."""

    @pytest.mark.unit
    def test_serialize_confidence_basic(self):
        """Test basic confidence serialization."""
        confidence = 0.95
        result = serialize_confidence(confidence)

        expected = {"N": "0.95"}
        assert result == expected

    @pytest.mark.unit
    def test_serialize_confidence_zero(self):
        """Test confidence serialization with zero value."""
        confidence = 0.0
        result = serialize_confidence(confidence)

        expected = {"N": "0.00"}
        assert result == expected

    @pytest.mark.unit
    def test_serialize_confidence_one(self):
        """Test confidence serialization with value of 1.0."""
        confidence = 1.0
        result = serialize_confidence(confidence)

        expected = {"N": "1.00"}
        assert result == expected

    @pytest.mark.unit
    def test_serialize_confidence_precision(self):
        """Test confidence serialization maintains 2 decimal places."""
        test_cases = [
            (0.1, "0.10"),
            (0.99, "0.99"),
            (0.999, "1.00"),  # Rounds up
            (0.994, "0.99"),  # Rounds down
            (0.995, "1.00"),  # Rounds up (ROUND_HALF_UP)
            (0.123456, "0.12"),  # Truncates to 2 decimals
            (0.987654, "0.99"),  # Rounds to 2 decimals
        ]

        for confidence, expected_n in test_cases:
            result = serialize_confidence(confidence)
            assert result == {"N": expected_n}, f"Failed for confidence {confidence}"

    @pytest.mark.unit
    def test_serialize_confidence_edge_cases(self):
        """Test confidence serialization with edge case values."""
        # Very small positive number
        result = serialize_confidence(0.001)
        assert result == {"N": "0.00"}

        # Very close to 1
        result = serialize_confidence(0.9999)
        assert result == {"N": "1.00"}


class TestSerializationUtilitiesIntegration:
    """Integration tests for serialization utilities with actual entity usage."""

    @pytest.mark.unit
    def test_serialization_utilities_with_receipt_word_data(self):
        """Test that utilities work correctly with real ReceiptWord data."""
        # Real data similar to what would be in a ReceiptWord
        bounding_box = {
            "x": 0.1234,
            "y": 0.5678,
            "width": 0.2345,
            "height": 0.1111,
        }
        top_right = {"x": 0.3579, "y": 0.5678}
        confidence = 0.8567

        # Test all utilities
        bbox_result = serialize_bounding_box(bounding_box)
        point_result = serialize_coordinate_point(top_right)
        conf_result = serialize_confidence(confidence)

        # Verify structure matches DynamoDB expectations
        assert bbox_result["M"]["x"]["N"] == "0.12340000000000000000"
        assert point_result["M"]["x"]["N"] == "0.35790000000000000000"
        assert conf_result["N"] == "0.86"

    @pytest.mark.unit
    def test_serialization_consistency_across_utilities(self):
        """Test that utilities produce consistent DynamoDB format structures."""
        bounding_box = {"x": 0.5, "y": 0.5, "width": 0.5, "height": 0.5}
        point = {"x": 0.5, "y": 0.5}
        confidence = 0.5

        bbox_result = serialize_bounding_box(bounding_box)
        point_result = serialize_coordinate_point(point)
        conf_result = serialize_confidence(confidence)

        # All should use proper DynamoDB type indicators
        assert "M" in bbox_result
        assert "M" in point_result
        assert "N" in conf_result

        # Number formatting should be consistent where applicable
        assert bbox_result["M"]["x"]["N"] == point_result["M"]["x"]["N"]
        assert bbox_result["M"]["y"]["N"] == point_result["M"]["y"]["N"]

    @pytest.mark.unit
    def test_utilities_maintain_backward_compatibility(self):
        """Test that utilities produce same format as original hardcoded serialization."""
        # This test ensures our utilities produce identical output to the original code
        bounding_box = {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}

        # What the original code would have produced
        original_format = {
            "M": {
                "x": {"N": "0.10000000000000000000"},
                "y": {"N": "0.20000000000000000000"},
                "width": {"N": "0.30000000000000000000"},
                "height": {"N": "0.40000000000000000000"},
            }
        }

        # What our utility produces
        utility_format = serialize_bounding_box(bounding_box)

        assert utility_format == original_format


class TestBuildBaseItem:
    """Test cases for build_base_item utility function."""

    class MockEntity:
        """Mock entity for testing build_base_item."""

        def __init__(self, has_gsi=False):
            self.key = {"PK": {"S": "TEST#123"}, "SK": {"S": "ITEM#456"}}
            self.has_gsi = has_gsi

        def gsi1_key(self):
            return {
                "GSI1PK": {"S": "STATUS#ACTIVE"},
                "GSI1SK": {"S": "TEST#123"},
            }

        def gsi2_key(self):
            return {
                "GSI2PK": {"S": "TYPE#ENTITY"},
                "GSI2SK": {"S": "TEST#123"},
            }

        @property
        def gsi_property(self):
            return {"GSI3PK": {"S": "CATEGORY#A"}, "GSI3SK": {"S": "TEST#123"}}

    @pytest.mark.unit
    def test_build_base_item_primary_key_only(self):
        """Test build_base_item with just primary key."""
        entity = self.MockEntity()
        result = build_base_item(entity, "TEST_ENTITY")

        expected = {
            "PK": {"S": "TEST#123"},
            "SK": {"S": "ITEM#456"},
            "TYPE": {"S": "TEST_ENTITY"},
        }
        assert result == expected

    @pytest.mark.unit
    def test_build_base_item_with_single_gsi(self):
        """Test build_base_item with one GSI key."""
        entity = self.MockEntity()
        result = build_base_item(entity, "TEST_ENTITY", ["gsi1_key"])

        expected = {
            "PK": {"S": "TEST#123"},
            "SK": {"S": "ITEM#456"},
            "GSI1PK": {"S": "STATUS#ACTIVE"},
            "GSI1SK": {"S": "TEST#123"},
            "TYPE": {"S": "TEST_ENTITY"},
        }
        assert result == expected

    @pytest.mark.unit
    def test_build_base_item_with_multiple_gsi(self):
        """Test build_base_item with multiple GSI keys."""
        entity = self.MockEntity()
        result = build_base_item(entity, "TEST_ENTITY", ["gsi1_key", "gsi2_key"])

        expected = {
            "PK": {"S": "TEST#123"},
            "SK": {"S": "ITEM#456"},
            "GSI1PK": {"S": "STATUS#ACTIVE"},
            "GSI1SK": {"S": "TEST#123"},
            "GSI2PK": {"S": "TYPE#ENTITY"},
            "GSI2SK": {"S": "TEST#123"},
            "TYPE": {"S": "TEST_ENTITY"},
        }
        assert result == expected

    @pytest.mark.unit
    def test_build_base_item_with_property_gsi(self):
        """Test build_base_item with GSI property (not method)."""
        entity = self.MockEntity()
        result = build_base_item(entity, "TEST_ENTITY", ["gsi_property"])

        expected = {
            "PK": {"S": "TEST#123"},
            "SK": {"S": "ITEM#456"},
            "GSI3PK": {"S": "CATEGORY#A"},
            "GSI3SK": {"S": "TEST#123"},
            "TYPE": {"S": "TEST_ENTITY"},
        }
        assert result == expected

    @pytest.mark.unit
    def test_build_base_item_missing_gsi_method(self):
        """Test build_base_item gracefully handles missing GSI methods."""
        entity = self.MockEntity()
        result = build_base_item(entity, "TEST_ENTITY", ["gsi1_key", "nonexistent_gsi"])

        # Should include gsi1_key but skip nonexistent_gsi
        expected = {
            "PK": {"S": "TEST#123"},
            "SK": {"S": "ITEM#456"},
            "GSI1PK": {"S": "STATUS#ACTIVE"},
            "GSI1SK": {"S": "TEST#123"},
            "TYPE": {"S": "TEST_ENTITY"},
        }
        assert result == expected

    @pytest.mark.unit
    def test_build_base_item_empty_gsi_list(self):
        """Test build_base_item with empty GSI list."""
        entity = self.MockEntity()
        result = build_base_item(entity, "TEST_ENTITY", [])

        expected = {
            "PK": {"S": "TEST#123"},
            "SK": {"S": "ITEM#456"},
            "TYPE": {"S": "TEST_ENTITY"},
        }
        assert result == expected

    @pytest.mark.unit
    def test_build_base_item_none_gsi_list(self):
        """Test build_base_item with None GSI list."""
        entity = self.MockEntity()
        result = build_base_item(entity, "TEST_ENTITY", None)

        expected = {
            "PK": {"S": "TEST#123"},
            "SK": {"S": "ITEM#456"},
            "TYPE": {"S": "TEST_ENTITY"},
        }
        assert result == expected

    @pytest.mark.unit
    def test_build_base_item_maintains_key_structure(self):
        """Test that build_base_item doesn't modify original key structure."""
        entity = self.MockEntity()
        original_key = entity.key.copy()

        result = build_base_item(entity, "TEST_ENTITY", ["gsi1_key"])

        # Original key should be unchanged
        assert entity.key == original_key

        # Result should contain all expected keys
        assert "PK" in result and "SK" in result
        assert "GSI1PK" in result and "GSI1SK" in result
        assert "TYPE" in result


class TestBuildBaseItemIntegration:
    """Integration tests for build_base_item with real entity patterns."""

    @pytest.mark.unit
    def test_build_base_item_receipt_word_pattern(self):
        """Test build_base_item matches ReceiptWord pattern."""
        import uuid

        from receipt_dynamo.constants import EmbeddingStatus
        from receipt_dynamo.entities.receipt_word import ReceiptWord

        # Create a real ReceiptWord
        word = ReceiptWord(
            receipt_id=123,
            image_id=str(uuid.uuid4()),
            line_id=1,
            word_id=1,
            text="TEST",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            top_right={"x": 0.4, "y": 0.2},
            top_left={"x": 0.1, "y": 0.2},
            bottom_right={"x": 0.4, "y": 0.6},
            bottom_left={"x": 0.1, "y": 0.6},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
            embedding_status=EmbeddingStatus.NONE,
        )

        # Test the utility
        base_item = build_base_item(
            word, "RECEIPT_WORD", ["gsi1_key", "gsi2_key", "gsi3_key"]
        )

        # Should have all the expected keys
        assert "PK" in base_item and "SK" in base_item
        assert "GSI1PK" in base_item and "GSI1SK" in base_item
        assert "GSI2PK" in base_item and "GSI2SK" in base_item
        assert "GSI3PK" in base_item and "GSI3SK" in base_item
        assert base_item["TYPE"]["S"] == "RECEIPT_WORD"

    @pytest.mark.unit
    def test_build_base_item_letter_pattern(self):
        """Test build_base_item matches Letter pattern."""
        import uuid

        from receipt_dynamo.entities.letter import Letter

        # Create a real Letter
        letter = Letter(
            image_id=str(uuid.uuid4()),
            line_id=1,
            word_id=1,
            letter_id=1,
            text="A",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            top_right={"x": 0.4, "y": 0.2},
            top_left={"x": 0.1, "y": 0.2},
            bottom_right={"x": 0.4, "y": 0.6},
            bottom_left={"x": 0.1, "y": 0.6},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
        )

        # Test the utility
        base_item = build_base_item(letter, "LETTER")

        # Should have primary key and TYPE only (no GSI)
        assert "PK" in base_item and "SK" in base_item
        assert base_item["TYPE"]["S"] == "LETTER"
        assert "GSI1PK" not in base_item  # Letter doesn't have GSI keys


class TestDeserializeBoundingBox:
    """Test cases for deserialize_bounding_box utility function."""

    @pytest.mark.unit
    def test_deserialize_bounding_box_basic(self):
        """Test basic bounding box deserialization."""
        item_field = {
            "M": {
                "x": {"N": "0.10000000000000000000"},
                "y": {"N": "0.20000000000000000000"},
                "width": {"N": "0.30000000000000000000"},
                "height": {"N": "0.40000000000000000000"},
            }
        }
        result = deserialize_bounding_box(item_field)

        expected = {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
        assert result == expected

    @pytest.mark.unit
    def test_deserialize_bounding_box_zero_values(self):
        """Test bounding box deserialization with zero values."""
        item_field = {
            "M": {
                "x": {"N": "0.00000000000000000000"},
                "y": {"N": "0.00000000000000000000"},
                "width": {"N": "0.00000000000000000000"},
                "height": {"N": "0.00000000000000000000"},
            }
        }
        result = deserialize_bounding_box(item_field)

        expected = {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}
        assert result == expected

    @pytest.mark.unit
    def test_deserialize_bounding_box_large_values(self):
        """Test bounding box deserialization with large values."""
        item_field = {
            "M": {
                "x": {"N": "999.99900000000000000000"},
                "y": {"N": "888.88800000000000000000"},
                "width": {"N": "777.77700000000000000000"},
                "height": {"N": "666.66600000000000000000"},
            }
        }
        result = deserialize_bounding_box(item_field)

        assert result["x"] == 999.999
        assert result["y"] == 888.888
        assert result["width"] == 777.777
        assert result["height"] == 666.666


class TestDeserializeCoordinatePoint:
    """Test cases for deserialize_coordinate_point utility function."""

    @pytest.mark.unit
    def test_deserialize_coordinate_point_basic(self):
        """Test basic coordinate point deserialization."""
        item_field = {
            "M": {
                "x": {"N": "0.50000000000000000000"},
                "y": {"N": "0.70000000000000000000"},
            }
        }
        result = deserialize_coordinate_point(item_field)

        expected = {"x": 0.5, "y": 0.7}
        assert result == expected

    @pytest.mark.unit
    def test_deserialize_coordinate_point_negative(self):
        """Test coordinate point deserialization with negative values."""
        item_field = {
            "M": {
                "x": {"N": "-0.25000000000000000000"},
                "y": {"N": "-0.75000000000000000000"},
            }
        }
        result = deserialize_coordinate_point(item_field)

        expected = {"x": -0.25, "y": -0.75}
        assert result == expected

    @pytest.mark.unit
    def test_deserialize_coordinate_point_zero(self):
        """Test coordinate point deserialization with zero values."""
        item_field = {
            "M": {
                "x": {"N": "0.00000000000000000000"},
                "y": {"N": "0.00000000000000000000"},
            }
        }
        result = deserialize_coordinate_point(item_field)

        expected = {"x": 0.0, "y": 0.0}
        assert result == expected


class TestDeserializeConfidence:
    """Test cases for deserialize_confidence utility function."""

    @pytest.mark.unit
    def test_deserialize_confidence_basic(self):
        """Test basic confidence deserialization."""
        item_field = {"N": "0.95"}
        result = deserialize_confidence(item_field)

        assert result == 0.95

    @pytest.mark.unit
    def test_deserialize_confidence_zero(self):
        """Test confidence deserialization with zero value."""
        item_field = {"N": "0.00"}
        result = deserialize_confidence(item_field)

        assert result == 0.0

    @pytest.mark.unit
    def test_deserialize_confidence_one(self):
        """Test confidence deserialization with value of 1.0."""
        item_field = {"N": "1.00"}
        result = deserialize_confidence(item_field)

        assert result == 1.0

    @pytest.mark.unit
    def test_deserialize_confidence_precision(self):
        """Test confidence deserialization with various precision values."""
        test_cases = [
            ("0.10", 0.1),
            ("0.99", 0.99),
            ("0.123", 0.123),
            ("0.9876", 0.9876),
        ]

        for n_value, expected in test_cases:
            item_field = {"N": n_value}
            result = deserialize_confidence(item_field)
            assert result == expected, f"Failed for confidence {n_value}"


class TestSerializationDeserializationRoundTrip:
    """Test round-trip serialization/deserialization consistency."""

    @pytest.mark.unit
    def test_bounding_box_round_trip(self):
        """Test that serialization and deserialization are inverse operations for bounding box."""
        original = {
            "x": 0.1234,
            "y": 0.5678,
            "width": 0.2345,
            "height": 0.1111,
        }

        # Serialize then deserialize
        serialized = serialize_bounding_box(original)
        deserialized = deserialize_bounding_box(serialized)

        # Should get back the original values
        assert deserialized == original

    @pytest.mark.unit
    def test_coordinate_point_round_trip(self):
        """Test that serialization and deserialization are inverse operations for coordinate points."""
        original = {"x": 0.3579, "y": 0.8642}

        # Serialize then deserialize
        serialized = serialize_coordinate_point(original)
        deserialized = deserialize_coordinate_point(serialized)

        # Should get back the original values
        assert deserialized == original

    @pytest.mark.unit
    def test_confidence_round_trip(self):
        """Test that serialization and deserialization are inverse operations for confidence."""
        original = 0.8567

        # Serialize then deserialize
        serialized = serialize_confidence(original)
        deserialized = deserialize_confidence(serialized)

        # Should get back the original value (with rounding to 2 decimal places)
        assert deserialized == 0.86  # Rounded by serialize_confidence

    @pytest.mark.unit
    def test_multiple_field_round_trip(self):
        """Test round-trip with multiple fields like a real entity would use."""
        original_bbox = {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
        original_point = {"x": 0.4, "y": 0.2}
        original_confidence = 0.95

        # Serialize all fields
        serialized_bbox = serialize_bounding_box(original_bbox)
        serialized_point = serialize_coordinate_point(original_point)
        serialized_conf = serialize_confidence(original_confidence)

        # Deserialize all fields
        deserialized_bbox = deserialize_bounding_box(serialized_bbox)
        deserialized_point = deserialize_coordinate_point(serialized_point)
        deserialized_conf = deserialize_confidence(serialized_conf)

        # Verify round-trip consistency
        assert deserialized_bbox == original_bbox
        assert deserialized_point == original_point
        assert deserialized_conf == original_confidence
