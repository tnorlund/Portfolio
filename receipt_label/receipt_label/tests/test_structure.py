import uuid
from datetime import datetime
from typing import Dict, List, Optional

import pytest
from receipt_label.models.structure import (
    ContentPattern,
    ReceiptSection,
    SpatialPattern,
    StructureAnalysis,
)

from receipt_dynamo.entities.receipt_structure_analysis import (
    ContentPattern as DynamoContentPattern,
)
from receipt_dynamo.entities.receipt_structure_analysis import (
    ReceiptSection as DynamoReceiptSection,
)
from receipt_dynamo.entities.receipt_structure_analysis import (
    ReceiptStructureAnalysis,
)
from receipt_dynamo.entities.receipt_structure_analysis import (
    SpatialPattern as DynamoSpatialPattern,
)


# Test data fixtures
@pytest.fixture
def sample_spatial_pattern() -> SpatialPattern:
    """Sample spatial pattern for testing."""
    return SpatialPattern(pattern_type="alignment", description="left-aligned")


@pytest.fixture
def sample_content_pattern() -> ContentPattern:
    """Sample content pattern for testing."""
    return ContentPattern(
        pattern_type="semantic",
        description="contains business name",
        examples=["Store", "Market", "Shop"],
    )


@pytest.fixture
def sample_receipt_section(
    sample_spatial_pattern, sample_content_pattern
) -> ReceiptSection:
    """Sample receipt section for testing."""
    return ReceiptSection(
        name="header",
        line_ids=[1, 2, 3],
        spatial_patterns=[sample_spatial_pattern],
        content_patterns=[sample_content_pattern],
        reasoning="Clear header section with business name and address",
    )


@pytest.fixture
def sample_structure_analysis(sample_receipt_section) -> StructureAnalysis:
    """Sample structure analysis for testing."""
    return StructureAnalysis(
        sections=[sample_receipt_section],
        overall_reasoning="Receipt has clear header, body, and footer sections",
        metadata={"model_version": "1.0.0", "processing_time_ms": 150},
    )


@pytest.fixture
def sample_dynamo_receipt_structure_analysis() -> ReceiptStructureAnalysis:
    """Sample DynamoDB ReceiptStructureAnalysis for testing."""
    # Create the spatial and content patterns
    spatial_pattern = DynamoSpatialPattern(
        pattern_type="alignment", description="left-aligned"
    )

    content_pattern = DynamoContentPattern(
        pattern_type="semantic",
        description="contains business name",
        examples=["Store", "Market", "Shop"],
    )

    # Create the section
    section = DynamoReceiptSection(
        name="header",
        line_ids=[1, 2, 3],
        spatial_patterns=[spatial_pattern],
        content_patterns=[content_pattern],
        reasoning="Clear header section with business name and address",
    )

    # Create the ReceiptStructureAnalysis
    return ReceiptStructureAnalysis(
        receipt_id=123,
        image_id="abc123",
        sections=[section],
        overall_reasoning="Receipt has clear header, body, and footer sections",
        version="1.0.0",
        metadata={"model_version": "1.0.0", "processing_time_ms": 150},
        timestamp_added=datetime.fromisoformat("2023-03-15T12:00:00"),
        timestamp_updated=datetime.fromisoformat("2023-03-15T12:30:00"),
        processing_metrics={"section_count": 1},
        source_info={"model": "gpt-4"},
    )


@pytest.fixture
def sample_dynamo_dict() -> Dict:
    """Sample DynamoDB dictionary for testing."""
    return {
        "sections": [
            {
                "name": "header",
                "line_ids": [1, 2, 3],
                "spatial_patterns": [
                    {
                        "pattern_type": "alignment",
                        "description": "left-aligned",
                        "metadata": {},
                    }
                ],
                "content_patterns": [
                    {
                        "pattern_type": "semantic",
                        "description": "contains business name",
                        "examples": ["Store", "Market", "Shop"],
                        "metadata": {},
                    }
                ],
                "reasoning": "Clear header section with business name and address",
                "start_line": 1,
                "end_line": 3,
                "metadata": {},
            }
        ],
        "overall_reasoning": "Receipt has clear header, body, and footer sections",
        "timestamp_added": "2023-03-15T12:00:00",
        "timestamp_updated": "2023-03-15T12:30:00",
        "metadata": {"model_version": "1.0.0", "processing_time_ms": 150},
        "processing_metrics": {
            "section_count": 1,
            "section_types": ["header"],
            "pattern_counts": {"spatial_patterns": 1, "content_patterns": 1},
        },
        "source_info": {"model": "gpt-4", "version": "1.0"},
    }


@pytest.mark.unit
class TestStructureAnalysis:
    """Tests for the StructureAnalysis class."""

    def test_to_dynamo_basic(self, sample_structure_analysis):
        """Test conversion to DynamoDB ReceiptStructureAnalysis entity."""
        # Set required fields
        sample_structure_analysis.receipt_id = 123
        sample_structure_analysis.image_id = "abc123"

        dynamo_obj = sample_structure_analysis.to_dynamo()

        # Check that we got the right type of object
        assert isinstance(dynamo_obj, ReceiptStructureAnalysis)

        # Check basic fields
        assert dynamo_obj.receipt_id == 123
        assert dynamo_obj.image_id == "abc123"
        assert dynamo_obj.version == "1.0.0"
        assert (
            dynamo_obj.overall_reasoning
            == sample_structure_analysis.overall_reasoning
        )

        # Check sections
        assert len(dynamo_obj.sections) == 1
        section = dynamo_obj.sections[0]
        assert section.name == "header"
        assert section.line_ids == [1, 2, 3]

        # Check patterns
        assert len(section.spatial_patterns) == 1
        assert section.spatial_patterns[0].pattern_type == "alignment"
        assert section.spatial_patterns[0].description == "left-aligned"

        assert len(section.content_patterns) == 1
        assert section.content_patterns[0].pattern_type == "semantic"
        assert (
            section.content_patterns[0].description == "contains business name"
        )
        assert "Store" in section.content_patterns[0].examples

    def test_to_dynamo_with_string_patterns(self):
        """Test to_dynamo with string patterns instead of pattern objects."""
        # Create a section with string patterns
        section = ReceiptSection(
            name="test-section",
            line_ids=[1, 2],
            spatial_patterns=["top-aligned", "indented"],
            content_patterns=["contains prices"],
            reasoning="Test section with string patterns",
        )

        analysis = StructureAnalysis(
            sections=[section],
            overall_reasoning="Test analysis",
            receipt_id=123,
            image_id="abc123",
        )

        # Convert to DynamoDB object
        dynamo_obj = analysis.to_dynamo()

        # Check that string patterns were converted properly
        assert len(dynamo_obj.sections) == 1
        section = dynamo_obj.sections[0]

        assert len(section.spatial_patterns) == 2
        assert section.spatial_patterns[0].pattern_type == "legacy"
        assert section.spatial_patterns[0].description == "top-aligned"

        assert len(section.content_patterns) == 1
        assert section.content_patterns[0].pattern_type == "legacy"
        assert section.content_patterns[0].description == "contains prices"

    def test_from_dynamo_basic(self, sample_dynamo_receipt_structure_analysis):
        """Test conversion from DynamoDB ReceiptStructureAnalysis entity."""
        analysis = StructureAnalysis.from_dynamo(
            sample_dynamo_receipt_structure_analysis
        )

        # Check basic fields
        assert len(analysis.sections) == 1
        assert (
            analysis.overall_reasoning
            == "Receipt has clear header, body, and footer sections"
        )
        assert analysis.timestamp_added == "2023-03-15T12:00:00"
        assert analysis.timestamp_updated == "2023-03-15T12:30:00"

        # Check section conversion
        section = analysis.sections[0]
        assert section.name == "header"
        assert section.line_ids == [1, 2, 3]
        assert (
            section.reasoning
            == "Clear header section with business name and address"
        )

        # Check patterns
        assert len(section.spatial_patterns) == 1
        assert section.spatial_patterns[0].pattern_type == "alignment"
        assert section.spatial_patterns[0].description == "left-aligned"

        assert len(section.content_patterns) == 1
        assert section.content_patterns[0].pattern_type == "semantic"
        assert (
            section.content_patterns[0].description == "contains business name"
        )
        assert "Store" in section.content_patterns[0].examples

        # Check metadata conversion
        assert "model_version" in analysis.metadata
        assert analysis.metadata["model_version"] == "1.0.0"
        assert "processing_metrics" in analysis.metadata
        assert analysis.metadata["processing_metrics"]["section_count"] == 1
        assert "source_info" in analysis.metadata
        assert analysis.metadata["source_info"]["model"] == "gpt-4"

    def test_roundtrip_conversion(self, sample_structure_analysis):
        """Test that to_dynamo followed by from_dynamo results in equivalent object."""
        # Set required fields for to_dynamo
        sample_structure_analysis.receipt_id = 123
        sample_structure_analysis.image_id = "abc123"

        # Convert to DynamoDB object and back
        dynamo_obj = sample_structure_analysis.to_dynamo()
        reconstructed = StructureAnalysis.from_dynamo(dynamo_obj)

        # Check key properties match
        assert len(reconstructed.sections) == len(
            sample_structure_analysis.sections
        )
        assert (
            reconstructed.overall_reasoning
            == sample_structure_analysis.overall_reasoning
        )
        assert reconstructed.receipt_id == sample_structure_analysis.receipt_id
        assert reconstructed.image_id == sample_structure_analysis.image_id
        assert reconstructed.version == sample_structure_analysis.version

        # Check section properties
        orig_section = sample_structure_analysis.sections[0]
        new_section = reconstructed.sections[0]

        assert new_section.name == orig_section.name
        assert new_section.line_ids == orig_section.line_ids
        assert new_section.reasoning == orig_section.reasoning

        # Check pattern conversion
        assert len(new_section.spatial_patterns) == len(
            orig_section.spatial_patterns
        )
        assert (
            new_section.spatial_patterns[0].pattern_type
            == orig_section.spatial_patterns[0].pattern_type
        )
        assert (
            new_section.spatial_patterns[0].description
            == orig_section.spatial_patterns[0].description
        )

        assert len(new_section.content_patterns) == len(
            orig_section.content_patterns
        )
        assert (
            new_section.content_patterns[0].pattern_type
            == orig_section.content_patterns[0].pattern_type
        )
        assert (
            new_section.content_patterns[0].description
            == orig_section.content_patterns[0].description
        )
        assert (
            new_section.content_patterns[0].examples
            == orig_section.content_patterns[0].examples
        )
