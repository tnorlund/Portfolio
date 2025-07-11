from datetime import datetime
from typing import Dict, List

import pytest

from receipt_dynamo.entities.receipt_structure_analysis import (
    ContentPattern,
    ReceiptSection,
    ReceiptStructureAnalysis,
    SpatialPattern,
    item_to_receipt_structure_analysis,
)


# Fixtures for test data
@pytest.fixture
def example_spatial_pattern():
    return SpatialPattern(
        pattern_type="alignment",
        description="left-aligned text",
        metadata={"confidence": 0.95},
    )


@pytest.fixture
def example_content_pattern():
    return ContentPattern(
        pattern_type="format",
        description="price pattern",
        examples=["$10.99", "$5.50"],
        metadata={"confidence": 0.85},
    )


@pytest.fixture
def example_receipt_section(example_spatial_pattern, example_content_pattern):
    return ReceiptSection(
        name="header",
        line_ids=[1, 2, 3],
        spatial_patterns=[example_spatial_pattern],
        content_patterns=[example_content_pattern],
        reasoning="Contains store name and logo",
        start_line=1,
        end_line=3,
        metadata={"confidence": 0.9},
    )


@pytest.fixture
def example_receipt_structure_analysis(example_receipt_section):
    return ReceiptStructureAnalysis(
        receipt_id=123,
        image_id="abc123",
        sections=[example_receipt_section],
        overall_reasoning="Clear structure with distinct sections",
        version="1.0.0",
        metadata={"source": "test"},
        timestamp_added=datetime(2023, 1, 1, 12, 0, 0),
        timestamp_updated=datetime(2023, 1, 2, 12, 0, 0),
        processing_metrics={"time_ms": 150},
        source_info={"model": "test_model"},
        processing_history=[
            {
                "event": "creation",
                "timestamp": "2023-01-01T12:00:00",
                "details": "Initial creation",
            }
        ],
    )


@pytest.fixture
def example_dynamo_item(example_receipt_section):
    return {
        "PK": {"S": "IMAGE#abc123"},
        "SK": {"S": "RECEIPT#123#ANALYSIS#STRUCTURE"},
        "receipt_id": {"N": "123"},
        "image_id": {"S": "abc123"},
        "entity_type": {"S": "STRUCTURE_ANALYSIS"},
        "sections": {
            "L": [
                {
                    "M": {
                        "name": {"S": "header"},
                        "line_ids": {
                            "L": [{"N": "1"}, {"N": "2"}, {"N": "3"}]
                        },
                        "spatial_patterns": {
                            "L": [
                                {
                                    "M": {
                                        "pattern_type": {"S": "alignment"},
                                        "description": {
                                            "S": "left-aligned text"
                                        },
                                        "metadata": {
                                            "M": {"confidence": {"S": "0.95"}}
                                        },
                                    }
                                }
                            ]
                        },
                        "content_patterns": {
                            "L": [
                                {
                                    "M": {
                                        "pattern_type": {"S": "format"},
                                        "description": {"S": "price pattern"},
                                        "examples": {
                                            "L": [
                                                {"S": "$10.99"},
                                                {"S": "20.50"},
                                            ]
                                        },
                                        "metadata": {
                                            "M": {"reliability": {"S": "0.9"}}
                                        },
                                    }
                                }
                            ]
                        },
                        "reasoning": {
                            "S": "This section has typical header formatting"
                        },
                        "start_line": {"N": "1"},
                        "end_line": {"N": "3"},
                        "metadata": {"M": {"confidence": {"S": "0.85"}}},
                    }
                }
            ]
        },
        "overall_reasoning": {"S": "Clear structure with distinct sections"},
        "version": {"S": "1.0.0"},
        "metadata": {"M": {"source": {"S": "test"}}},
        "timestamp_added": {"S": "2023-01-01T12:00:00"},
        "timestamp_updated": {"S": "2023-01-02T12:00:00"},
        "processing_metrics": {"M": {"time_ms": {"N": "150"}}},
        "source_info": {"M": {"model": {"S": "test_model"}}},
        "processing_history": {
            "L": [
                {
                    "M": {
                        "event": {"S": "creation"},
                        "timestamp": {"S": "2023-01-01T12:00:00"},
                        "details": {"S": "Initial creation"},
                    }
                }
            ]
        },
        "GSI1PK": {"S": "ANALYSIS_TYPE"},
        "GSI1SK": {"S": "STRUCTURE#2023-01-01T12:00:00"},
        "GSI2PK": {"S": "RECEIPT"},
        "GSI2SK": {"S": "IMAGE#abc123#RECEIPT#123"},
    }


# SpatialPattern Tests
@pytest.mark.unit
def test_spatial_pattern_init_valid(example_spatial_pattern):
    """Test valid initialization of SpatialPattern."""
    assert example_spatial_pattern.pattern_type == "alignment"
    assert example_spatial_pattern.description == "left-aligned text"
    assert example_spatial_pattern.metadata == {"confidence": 0.95}


@pytest.mark.unit
def test_spatial_pattern_init_invalid_type():
    """Test validation of pattern_type parameter."""
    with pytest.raises(TypeError):
        SpatialPattern(pattern_type=123, description="left-aligned text")


@pytest.mark.unit
def test_spatial_pattern_init_invalid_description():
    """Test validation of description parameter."""
    with pytest.raises(TypeError):
        SpatialPattern(pattern_type="alignment", description=123)


@pytest.mark.unit
def test_spatial_pattern_init_invalid_metadata():
    """Test validation of metadata parameter."""
    with pytest.raises(TypeError):
        SpatialPattern(
            pattern_type="alignment",
            description="left-aligned text",
            metadata="invalid",
        )


@pytest.mark.unit
def test_spatial_pattern_to_dict(example_spatial_pattern):
    """Test conversion to dictionary."""
    result = example_spatial_pattern.to_dict()
    expected = {
        "pattern_type": "alignment",
        "description": "left-aligned text",
        "metadata": {"confidence": 0.95},
    }
    assert result == expected


@pytest.mark.unit
def test_spatial_pattern_from_dict():
    """Test creation from dictionary."""
    data = {
        "pattern_type": "alignment",
        "description": "left-aligned text",
        "metadata": {"confidence": 0.95},
    }
    pattern = SpatialPattern.from_dict(data)
    assert pattern.pattern_type == "alignment"
    assert pattern.description == "left-aligned text"
    assert pattern.metadata == {"confidence": 0.95}


@pytest.mark.unit
def test_spatial_pattern_from_dict_invalid():
    """Test validation when creating from invalid dictionary."""
    with pytest.raises(TypeError):
        SpatialPattern.from_dict("not a dict")


@pytest.mark.unit
def test_spatial_pattern_eq(example_spatial_pattern):
    """Test equality comparison."""
    same_pattern = SpatialPattern(
        pattern_type="alignment",
        description="left-aligned text",
        metadata={"confidence": 0.95},
    )
    different_pattern = SpatialPattern(
        pattern_type="different",
        description="left-aligned text",
        metadata={"confidence": 0.95},
    )

    assert example_spatial_pattern == same_pattern
    assert example_spatial_pattern != different_pattern
    assert example_spatial_pattern != "not a pattern"


@pytest.mark.unit
def test_spatial_pattern_repr(example_spatial_pattern):
    """Test string representation."""
    repr_str = repr(example_spatial_pattern)
    assert "SpatialPattern" in repr_str
    assert "alignment" in repr_str
    assert "left-aligned text" in repr_str


# ContentPattern Tests
@pytest.mark.unit
def test_content_pattern_init_valid(example_content_pattern):
    """Test valid initialization of ContentPattern."""
    assert example_content_pattern.pattern_type == "format"
    assert example_content_pattern.description == "price pattern"
    assert example_content_pattern.examples == ["$10.99", "$5.50"]
    assert example_content_pattern.metadata == {"confidence": 0.85}


@pytest.mark.unit
def test_content_pattern_init_invalid_type():
    """Test validation of pattern_type parameter."""
    with pytest.raises(TypeError):
        ContentPattern(pattern_type=123, description="price pattern")


@pytest.mark.unit
def test_content_pattern_init_invalid_description():
    """Test validation of description parameter."""
    with pytest.raises(TypeError):
        ContentPattern(pattern_type="format", description=123)


@pytest.mark.unit
def test_content_pattern_init_invalid_examples():
    """Test validation of examples parameter."""
    with pytest.raises(TypeError):
        ContentPattern(
            pattern_type="format",
            description="price pattern",
            examples="invalid",
        )


@pytest.mark.unit
def test_content_pattern_init_invalid_example_items():
    """Test validation of example items."""
    with pytest.raises(TypeError):
        ContentPattern(
            pattern_type="format",
            description="price pattern",
            examples=["valid", 123],
        )


@pytest.mark.unit
def test_content_pattern_init_invalid_metadata():
    """Test validation of metadata parameter."""
    with pytest.raises(TypeError):
        ContentPattern(
            pattern_type="format",
            description="price pattern",
            metadata="invalid",
        )


@pytest.mark.unit
def test_content_pattern_to_dict(example_content_pattern):
    """Test conversion to dictionary."""
    result = example_content_pattern.to_dict()
    expected = {
        "pattern_type": "format",
        "description": "price pattern",
        "examples": ["$10.99", "$5.50"],
        "metadata": {"confidence": 0.85},
    }
    assert result == expected


@pytest.mark.unit
def test_content_pattern_from_dict():
    """Test creation from dictionary."""
    data = {
        "pattern_type": "format",
        "description": "price pattern",
        "examples": ["$10.99", "$5.50"],
        "metadata": {"confidence": 0.85},
    }
    pattern = ContentPattern.from_dict(data)
    assert pattern.pattern_type == "format"
    assert pattern.description == "price pattern"
    assert pattern.examples == ["$10.99", "$5.50"]
    assert pattern.metadata == {"confidence": 0.85}


@pytest.mark.unit
def test_content_pattern_from_dict_invalid():
    """Test validation when creating from invalid dictionary."""
    with pytest.raises(TypeError):
        ContentPattern.from_dict("not a dict")


@pytest.mark.unit
def test_content_pattern_eq(example_content_pattern):
    """Test equality comparison."""
    same_pattern = ContentPattern(
        pattern_type="format",
        description="price pattern",
        examples=["$10.99", "$5.50"],
        metadata={"confidence": 0.85},
    )
    different_pattern = ContentPattern(
        pattern_type="different",
        description="price pattern",
        examples=["$10.99", "$5.50"],
        metadata={"confidence": 0.85},
    )

    assert example_content_pattern == same_pattern
    assert example_content_pattern != different_pattern
    assert example_content_pattern != "not a pattern"


@pytest.mark.unit
def test_content_pattern_repr(example_content_pattern):
    """Test string representation."""
    repr_str = repr(example_content_pattern)
    assert "ContentPattern" in repr_str
    assert "format" in repr_str
    assert "price pattern" in repr_str
    assert "examples=2" in repr_str


# ReceiptSection Tests
@pytest.mark.unit
def test_receipt_section_init_valid(
    example_receipt_section, example_spatial_pattern, example_content_pattern
):
    """Test valid initialization of ReceiptSection."""
    assert example_receipt_section.name == "header"
    assert example_receipt_section.line_ids == [1, 2, 3]
    assert example_receipt_section.spatial_patterns == [
        example_spatial_pattern
    ]
    assert example_receipt_section.content_patterns == [
        example_content_pattern
    ]
    assert example_receipt_section.reasoning == "Contains store name and logo"
    assert example_receipt_section.start_line == 1
    assert example_receipt_section.end_line == 3
    assert example_receipt_section.metadata == {"confidence": 0.9}


@pytest.mark.unit
def test_receipt_section_init_invalid_name():
    """Test validation of name parameter."""
    with pytest.raises(TypeError):
        ReceiptSection(
            name=123,
            line_ids=[1, 2, 3],
            spatial_patterns=[],
            content_patterns=[],
            reasoning="test",
        )


@pytest.mark.unit
def test_receipt_section_init_invalid_line_ids():
    """Test validation of line_ids parameter."""
    with pytest.raises(TypeError):
        ReceiptSection(
            name="header",
            line_ids="invalid",
            spatial_patterns=[],
            content_patterns=[],
            reasoning="test",
        )


@pytest.mark.unit
def test_receipt_section_init_invalid_line_id_items():
    """Test validation of line_id items."""
    with pytest.raises(TypeError):
        ReceiptSection(
            name="header",
            line_ids=[1, "invalid", 3],
            spatial_patterns=[],
            content_patterns=[],
            reasoning="test",
        )


@pytest.mark.unit
def test_receipt_section_init_invalid_spatial_patterns():
    """Test validation of spatial_patterns parameter."""
    with pytest.raises(TypeError):
        ReceiptSection(
            name="header",
            line_ids=[1, 2, 3],
            spatial_patterns="invalid",
            content_patterns=[],
            reasoning="test",
        )


@pytest.mark.unit
def test_receipt_section_init_invalid_spatial_pattern_items():
    """Test handling of dictionary spatial pattern items."""
    # This should not raise an exception - the implementation converts dicts to SpatialPattern objects
    section = ReceiptSection(
        name="header",
        line_ids=[1, 2, 3],
        spatial_patterns=[
            {"pattern_type": "test", "description": "test pattern"}
        ],
        content_patterns=[],
        reasoning="test",
    )
    # Verify conversion happened correctly
    assert len(section.spatial_patterns) == 1
    assert isinstance(section.spatial_patterns[0], SpatialPattern)
    assert section.spatial_patterns[0].pattern_type == "test"
    assert section.spatial_patterns[0].description == "test pattern"


@pytest.mark.unit
def test_receipt_section_init_invalid_content_patterns():
    """Test validation of content_patterns parameter."""
    with pytest.raises(TypeError):
        ReceiptSection(
            name="header",
            line_ids=[1, 2, 3],
            spatial_patterns=[],
            content_patterns="invalid",
            reasoning="test",
        )


@pytest.mark.unit
def test_receipt_section_init_invalid_content_pattern_items():
    """Test handling of dictionary content pattern items."""
    # This should not raise an exception - the implementation converts dicts to ContentPattern objects
    section = ReceiptSection(
        name="header",
        line_ids=[1, 2, 3],
        spatial_patterns=[],
        content_patterns=[
            {"pattern_type": "test", "description": "test pattern"}
        ],
        reasoning="test",
    )
    # Verify conversion happened correctly
    assert len(section.content_patterns) == 1
    assert isinstance(section.content_patterns[0], ContentPattern)
    assert section.content_patterns[0].pattern_type == "test"
    assert section.content_patterns[0].description == "test pattern"


@pytest.mark.unit
def test_receipt_section_init_invalid_reasoning():
    """Test validation of reasoning parameter."""
    with pytest.raises(TypeError):
        ReceiptSection(
            name="header",
            line_ids=[1, 2, 3],
            spatial_patterns=[],
            content_patterns=[],
            reasoning=123,
        )


@pytest.mark.unit
def test_receipt_section_init_invalid_start_line():
    """Test validation of start_line parameter."""
    with pytest.raises(TypeError):
        ReceiptSection(
            name="header",
            line_ids=[1, 2, 3],
            spatial_patterns=[],
            content_patterns=[],
            reasoning="test",
            start_line="invalid",
        )


@pytest.mark.unit
def test_receipt_section_init_invalid_end_line():
    """Test validation of end_line parameter."""
    with pytest.raises(TypeError):
        ReceiptSection(
            name="header",
            line_ids=[1, 2, 3],
            spatial_patterns=[],
            content_patterns=[],
            reasoning="test",
            end_line="invalid",
        )


@pytest.mark.unit
def test_receipt_section_init_invalid_metadata():
    """Test validation of metadata parameter."""
    with pytest.raises(TypeError):
        ReceiptSection(
            name="header",
            line_ids=[1, 2, 3],
            spatial_patterns=[],
            content_patterns=[],
            reasoning="test",
            metadata="invalid",
        )


@pytest.mark.unit
def test_receipt_section_automatic_start_end_line():
    """Test automatic calculation of start and end lines."""
    section = ReceiptSection(
        name="header",
        line_ids=[1, 2, 3],
        spatial_patterns=[],
        content_patterns=[],
        reasoning="test",
    )
    assert section.start_line == 1
    assert section.end_line == 3


@pytest.mark.unit
def test_receipt_section_to_dict(
    example_receipt_section, example_spatial_pattern, example_content_pattern
):
    """Test conversion to dictionary."""
    result = example_receipt_section.to_dict()
    expected = {
        "name": "header",
        "line_ids": [1, 2, 3],
        "spatial_patterns": [example_spatial_pattern.to_dict()],
        "content_patterns": [example_content_pattern.to_dict()],
        "reasoning": "Contains store name and logo",
        "start_line": 1,
        "end_line": 3,
        "metadata": {"confidence": 0.9},
    }
    assert result == expected


@pytest.mark.unit
def test_receipt_section_from_dict(
    example_spatial_pattern, example_content_pattern
):
    """Test creation from dictionary."""
    data = {
        "name": "header",
        "line_ids": [1, 2, 3],
        "spatial_patterns": [example_spatial_pattern.to_dict()],
        "content_patterns": [example_content_pattern.to_dict()],
        "reasoning": "Contains store name and logo",
        "start_line": 1,
        "end_line": 3,
        "metadata": {"confidence": 0.9},
    }
    section = ReceiptSection.from_dict(data)
    assert section.name == "header"
    assert section.line_ids == [1, 2, 3]
    assert len(section.spatial_patterns) == 1
    assert section.spatial_patterns[0].pattern_type == "alignment"
    assert len(section.content_patterns) == 1
    assert section.content_patterns[0].pattern_type == "format"
    assert section.reasoning == "Contains store name and logo"
    assert section.start_line == 1
    assert section.end_line == 3
    assert section.metadata == {"confidence": 0.9}


@pytest.mark.unit
def test_receipt_section_from_dict_with_string_patterns():
    """Test creation from dictionary with string patterns."""
    data = {
        "name": "header",
        "line_ids": [1, 2, 3],
        "spatial_patterns": ["alignment"],
        "content_patterns": ["price pattern"],
        "reasoning": "Contains store name and logo",
    }
    section = ReceiptSection.from_dict(data)
    assert section.name == "header"
    assert section.line_ids == [1, 2, 3]
    assert len(section.spatial_patterns) == 1
    assert section.spatial_patterns[0].pattern_type == "legacy"
    assert section.spatial_patterns[0].description == "alignment"
    assert len(section.content_patterns) == 1
    assert section.content_patterns[0].pattern_type == "legacy"
    assert section.content_patterns[0].description == "price pattern"


@pytest.mark.unit
def test_receipt_section_from_dict_invalid():
    """Test validation when creating from invalid dictionary."""
    with pytest.raises(TypeError):
        ReceiptSection.from_dict("not a dict")


@pytest.mark.unit
def test_receipt_section_eq(
    example_receipt_section, example_spatial_pattern, example_content_pattern
):
    """Test equality comparison."""
    same_section = ReceiptSection(
        name="header",
        line_ids=[1, 2, 3],
        spatial_patterns=[example_spatial_pattern],
        content_patterns=[example_content_pattern],
        reasoning="Contains store name and logo",
        start_line=1,
        end_line=3,
        metadata={"confidence": 0.9},
    )
    different_section = ReceiptSection(
        name="different",
        line_ids=[1, 2, 3],
        spatial_patterns=[example_spatial_pattern],
        content_patterns=[example_content_pattern],
        reasoning="Contains store name and logo",
        start_line=1,
        end_line=3,
        metadata={"confidence": 0.9},
    )

    assert example_receipt_section == same_section
    assert example_receipt_section != different_section
    assert example_receipt_section != "not a section"


@pytest.mark.unit
def test_receipt_section_repr(example_receipt_section):
    """Test string representation."""
    repr_str = repr(example_receipt_section)
    assert "ReceiptSection" in repr_str
    assert "header" in repr_str
    assert "lines=3" in repr_str


@pytest.mark.unit
def test_receipt_section_init_with_string_spatial_patterns():
    """Test handling of string spatial patterns during initialization."""
    section = ReceiptSection(
        name="header",
        line_ids=[1, 2, 3],
        spatial_patterns=["alignment"],  # String instead of SpatialPattern
        content_patterns=[],
        reasoning="test",
    )
    # Make sure the string was converted to a SpatialPattern
    assert len(section.spatial_patterns) == 1
    assert isinstance(section.spatial_patterns[0], SpatialPattern)
    assert section.spatial_patterns[0].pattern_type == "legacy"
    assert section.spatial_patterns[0].description == "alignment"


@pytest.mark.unit
def test_receipt_section_init_with_string_content_patterns():
    """Test handling of string content patterns during initialization."""
    section = ReceiptSection(
        name="header",
        line_ids=[1, 2, 3],
        spatial_patterns=[],
        content_patterns=["price pattern"],  # String instead of ContentPattern
        reasoning="test",
    )
    # Make sure the string was converted to a ContentPattern
    assert len(section.content_patterns) == 1
    assert isinstance(section.content_patterns[0], ContentPattern)
    assert section.content_patterns[0].pattern_type == "legacy"
    assert section.content_patterns[0].description == "price pattern"


# ReceiptStructureAnalysis Tests
@pytest.mark.unit
def test_receipt_structure_analysis_init_valid(
    example_receipt_structure_analysis, example_receipt_section
):
    """Test valid initialization of ReceiptStructureAnalysis."""
    assert example_receipt_structure_analysis.receipt_id == 123
    assert example_receipt_structure_analysis.image_id == "abc123"
    assert len(example_receipt_structure_analysis.sections) == 1
    assert (
        example_receipt_structure_analysis.sections[0]
        == example_receipt_section
    )
    assert (
        example_receipt_structure_analysis.overall_reasoning
        == "Clear structure with distinct sections"
    )
    assert example_receipt_structure_analysis.version == "1.0.0"
    assert example_receipt_structure_analysis.metadata == {"source": "test"}
    assert example_receipt_structure_analysis.timestamp_added == datetime(
        2023, 1, 1, 12, 0, 0
    )
    assert example_receipt_structure_analysis.timestamp_updated == datetime(
        2023, 1, 2, 12, 0, 0
    )
    assert example_receipt_structure_analysis.processing_metrics == {
        "time_ms": 150
    }
    assert example_receipt_structure_analysis.source_info == {
        "model": "test_model"
    }
    assert len(example_receipt_structure_analysis.processing_history) == 1


@pytest.mark.unit
def test_receipt_structure_analysis_init_invalid_receipt_id():
    """Test validation of receipt_id parameter."""
    with pytest.raises(TypeError):
        ReceiptStructureAnalysis(
            receipt_id=None,
            image_id="abc123",
            sections=[],
            overall_reasoning="test",
        )


@pytest.mark.unit
def test_receipt_structure_analysis_init_invalid_image_id():
    """Test validation of image_id parameter."""
    with pytest.raises(TypeError):
        ReceiptStructureAnalysis(
            receipt_id=123, image_id=123, sections=[], overall_reasoning="test"
        )


@pytest.mark.unit
def test_receipt_structure_analysis_init_invalid_sections():
    """Test validation of sections parameter."""
    with pytest.raises(TypeError):
        ReceiptStructureAnalysis(
            receipt_id=123,
            image_id="abc123",
            sections="invalid",
            overall_reasoning="test",
        )


@pytest.mark.unit
def test_receipt_structure_analysis_init_invalid_section_items():
    """Test validation of section items."""
    with pytest.raises(TypeError):
        ReceiptStructureAnalysis(
            receipt_id=123,
            image_id="abc123",
            sections=["invalid"],
            overall_reasoning="test",
        )


@pytest.mark.unit
def test_receipt_structure_analysis_init_invalid_overall_reasoning():
    """Test validation of overall_reasoning parameter."""
    with pytest.raises(TypeError):
        ReceiptStructureAnalysis(
            receipt_id=123,
            image_id="abc123",
            sections=[],
            overall_reasoning=123,
        )


@pytest.mark.unit
def test_receipt_structure_analysis_init_invalid_version():
    """Test validation of version parameter."""
    with pytest.raises(TypeError):
        ReceiptStructureAnalysis(
            receipt_id=123,
            image_id="abc123",
            sections=[],
            overall_reasoning="test",
            version=123,
        )


@pytest.mark.unit
def test_receipt_structure_analysis_init_invalid_metadata():
    """Test validation of metadata parameter."""
    with pytest.raises(TypeError):
        ReceiptStructureAnalysis(
            receipt_id=123,
            image_id="abc123",
            sections=[],
            overall_reasoning="test",
            metadata="invalid",
        )


@pytest.mark.unit
def test_receipt_structure_analysis_init_invalid_timestamp_added():
    """Test validation of timestamp_added parameter."""
    with pytest.raises(TypeError):
        ReceiptStructureAnalysis(
            receipt_id=123,
            image_id="abc123",
            sections=[],
            overall_reasoning="test",
            timestamp_added="invalid",
        )


@pytest.mark.unit
def test_receipt_structure_analysis_init_invalid_timestamp_updated():
    """Test validation of timestamp_updated parameter."""
    with pytest.raises(TypeError):
        ReceiptStructureAnalysis(
            receipt_id=123,
            image_id="abc123",
            sections=[],
            overall_reasoning="test",
            timestamp_updated="invalid",
        )


@pytest.mark.unit
def test_receipt_structure_analysis_init_invalid_processing_metrics():
    """Test validation of processing_metrics parameter."""
    with pytest.raises(TypeError):
        ReceiptStructureAnalysis(
            receipt_id=123,
            image_id="abc123",
            sections=[],
            overall_reasoning="test",
            processing_metrics="invalid",
        )


@pytest.mark.unit
def test_receipt_structure_analysis_init_invalid_source_info():
    """Test validation of source_info parameter."""
    with pytest.raises(TypeError):
        ReceiptStructureAnalysis(
            receipt_id=123,
            image_id="abc123",
            sections=[],
            overall_reasoning="test",
            source_info="invalid",
        )


@pytest.mark.unit
def test_receipt_structure_analysis_init_invalid_processing_history():
    """Test validation of processing_history parameter."""
    with pytest.raises(TypeError):
        ReceiptStructureAnalysis(
            receipt_id=123,
            image_id="abc123",
            sections=[],
            overall_reasoning="test",
            processing_history="invalid",
        )


@pytest.mark.unit
def test_receipt_structure_analysis_discovered_sections(
    example_receipt_structure_analysis, example_receipt_section
):
    """Test the discovered_sections property."""
    assert example_receipt_structure_analysis.discovered_sections == [
        example_receipt_section
    ]


@pytest.mark.unit
def test_receipt_structure_analysis_key(example_receipt_structure_analysis):
    """Test key generation."""
    key = example_receipt_structure_analysis.key
    assert key["PK"]["S"] == "IMAGE#abc123"
    assert key["SK"]["S"] == "RECEIPT#00123#ANALYSIS#STRUCTURE#1.0.0"


@pytest.mark.unit
def test_receipt_structure_analysis_gsi1_key(
    example_receipt_structure_analysis,
):
    """Test GSI1 key generation."""
    key = example_receipt_structure_analysis.gsi1_key()
    assert key["GSI1PK"]["S"] == "ANALYSIS_TYPE"
    assert key["GSI1SK"]["S"] == "STRUCTURE#2023-01-01T12:00:00"


@pytest.mark.unit
def test_receipt_structure_analysis_to_item(
    example_receipt_structure_analysis, example_receipt_section
):
    """Test conversion to DynamoDB item."""
    item = example_receipt_structure_analysis.to_item()
    assert item["PK"]["S"] == "IMAGE#abc123"
    assert item["SK"]["S"] == "RECEIPT#00123#ANALYSIS#STRUCTURE#1.0.0"
    assert item["GSI1PK"]["S"] == "ANALYSIS_TYPE"
    assert item["GSI1SK"]["S"] == "STRUCTURE#2023-01-01T12:00:00"
    assert item["receipt_id"]["N"] == "123"
    assert item["image_id"]["S"] == "abc123"
    assert item["entity_type"]["S"] == "STRUCTURE_ANALYSIS"
    section_item = item["sections"]["L"][0]["M"]
    assert section_item["name"]["S"] == "header"
    assert section_item["line_ids"]["L"] == [
        {"N": "1"},
        {"N": "2"},
        {"N": "3"},
    ]
    assert section_item["reasoning"]["S"] == "Contains store name and logo"
    assert section_item["start_line"]["N"] == "1"
    assert section_item["end_line"]["N"] == "3"
    assert section_item["metadata"]["M"]["confidence"]["S"] == "0.9"
    assert (
        item["overall_reasoning"]["S"]
        == "Clear structure with distinct sections"
    )
    assert item["version"]["S"] == "1.0.0"
    assert item["metadata"]["M"]["source"]["S"] == "test"
    assert item["timestamp_added"]["S"] == "2023-01-01T12:00:00"
    assert item["timestamp_updated"]["S"] == "2023-01-02T12:00:00"
    assert item["processing_metrics"]["M"]["time_ms"]["S"] == "150"
    assert item["source_info"]["M"]["model"]["S"] == "test_model"
    assert len(item["processing_history"]) == 1


@pytest.mark.unit
def test_receipt_structure_analysis_get_section_by_name(
    example_receipt_structure_analysis, example_receipt_section
):
    """Test getting section by name."""
    section = example_receipt_structure_analysis.get_section_by_name("header")
    assert section == example_receipt_section

    # Case insensitive
    section = example_receipt_structure_analysis.get_section_by_name("HEADER")
    assert section == example_receipt_section

    # Non-existent section
    section = example_receipt_structure_analysis.get_section_by_name("footer")
    assert section is None


@pytest.mark.unit
def test_receipt_structure_analysis_get_section_by_name_with_invalid_type():
    """Test handling of invalid type in get_section_by_name."""
    analysis = ReceiptStructureAnalysis(
        receipt_id=123,
        image_id="abc123",
        sections=[],
        overall_reasoning="test",
    )
    # Should return None for invalid types rather than raising exception
    section = analysis.get_section_by_name(123)
    assert section is None


@pytest.mark.unit
def test_receipt_structure_analysis_get_section_for_line(
    example_receipt_structure_analysis, example_receipt_section
):
    """Test getting section for line."""
    section = example_receipt_structure_analysis.get_section_for_line(1)
    assert section == example_receipt_section

    section = example_receipt_structure_analysis.get_section_for_line(2)
    assert section == example_receipt_section

    # Non-existent line
    section = example_receipt_structure_analysis.get_section_for_line(10)
    assert section is None


@pytest.mark.unit
def test_receipt_structure_analysis_get_section_for_line_with_invalid_type():
    """Test handling of invalid type in get_section_for_line."""
    analysis = ReceiptStructureAnalysis(
        receipt_id=123,
        image_id="abc123",
        sections=[],
        overall_reasoning="test",
    )
    # Should return None for invalid types rather than raising exception
    section = analysis.get_section_for_line("invalid")
    assert section is None


@pytest.mark.unit
def test_receipt_structure_analysis_get_sections_with_pattern(
    example_receipt_structure_analysis, example_receipt_section
):
    """Test getting sections with pattern type."""
    sections = example_receipt_structure_analysis.get_sections_with_pattern(
        "alignment"
    )
    assert sections == [example_receipt_section]

    sections = example_receipt_structure_analysis.get_sections_with_pattern(
        "format"
    )
    assert sections == [example_receipt_section]

    # Pattern that doesn't exist
    sections = example_receipt_structure_analysis.get_sections_with_pattern(
        "non-existent"
    )
    assert sections == []


@pytest.mark.unit
def test_receipt_structure_analysis_get_sections_with_pattern_with_invalid_type():
    """Test handling of invalid type in get_sections_with_pattern."""
    analysis = ReceiptStructureAnalysis(
        receipt_id=123,
        image_id="abc123",
        sections=[],
        overall_reasoning="test",
    )
    # Should return empty list for invalid types rather than raising exception
    sections = analysis.get_sections_with_pattern(123)
    assert sections == []


@pytest.mark.unit
def test_receipt_structure_analysis_summarize_structure(
    example_receipt_structure_analysis,
):
    """Test summarizing the structure."""
    summary = example_receipt_structure_analysis.summarize_structure()
    assert "Receipt contains 1 sections" in summary
    assert "header" in summary
    assert "Contains store name and logo" in summary
    assert "left-aligned text" in summary
    assert "price pattern" in summary


@pytest.mark.unit
def test_receipt_structure_analysis_eq(
    example_receipt_structure_analysis, example_receipt_section
):
    """Test equality comparison."""
    same_analysis = ReceiptStructureAnalysis(
        receipt_id=123,
        image_id="abc123",
        sections=[example_receipt_section],
        overall_reasoning="Clear structure with distinct sections",
        version="1.0.0",
        metadata={"source": "test"},
        timestamp_added=datetime(2023, 1, 1, 12, 0, 0),
        timestamp_updated=datetime(2023, 1, 2, 12, 0, 0),
        processing_metrics={"time_ms": 150},
        source_info={"model": "test_model"},
        processing_history=[
            {
                "event": "creation",
                "timestamp": "2023-01-01T12:00:00",
                "details": "Initial creation",
            }
        ],
    )
    different_analysis = ReceiptStructureAnalysis(
        receipt_id=456,
        image_id="abc123",
        sections=[example_receipt_section],
        overall_reasoning="Clear structure with distinct sections",
        version="1.0.0",
        metadata={"source": "test"},
        timestamp_added=datetime(2023, 1, 1, 12, 0, 0),
        timestamp_updated=datetime(2023, 1, 2, 12, 0, 0),
        processing_metrics={"time_ms": 150},
        source_info={"model": "test_model"},
        processing_history=[
            {
                "event": "creation",
                "timestamp": "2023-01-01T12:00:00",
                "details": "Initial creation",
            }
        ],
    )

    assert example_receipt_structure_analysis == same_analysis
    assert example_receipt_structure_analysis != different_analysis
    assert example_receipt_structure_analysis != "not an analysis"


@pytest.mark.unit
def test_receipt_structure_analysis_iter(
    example_receipt_structure_analysis, example_receipt_section
):
    """Test iteration."""
    attributes = dict(example_receipt_structure_analysis)
    assert attributes["receipt_id"] == 123
    assert attributes["image_id"] == "abc123"
    assert attributes["sections"] == [example_receipt_section]
    assert (
        attributes["overall_reasoning"]
        == "Clear structure with distinct sections"
    )
    assert attributes["version"] == "1.0.0"
    assert attributes["metadata"] == {"source": "test"}
    assert attributes["timestamp_added"] == datetime(2023, 1, 1, 12, 0, 0)
    assert attributes["timestamp_updated"] == datetime(2023, 1, 2, 12, 0, 0)
    assert attributes["processing_metrics"] == {"time_ms": 150}
    assert attributes["source_info"] == {"model": "test_model"}
    assert len(attributes["processing_history"]) == 1


@pytest.mark.unit
def test_receipt_structure_analysis_repr(example_receipt_structure_analysis):
    """Test string representation."""
    repr_str = repr(example_receipt_structure_analysis)
    assert "ReceiptStructureAnalysis" in repr_str
    assert "receipt_id=123" in repr_str
    assert "image_id='abc123'" in repr_str
    assert "sections=1" in repr_str
    assert "version='1.0.0'" in repr_str


@pytest.mark.unit
def test_receipt_structure_analysis_hash(example_receipt_structure_analysis):
    """Test hash generation."""
    hash_value = hash(example_receipt_structure_analysis)
    assert isinstance(hash_value, int)


@pytest.mark.unit
def test_receipt_structure_analysis_from_dynamo(
    example_dynamo_item, example_receipt_section
):
    """Test creating from DynamoDB item through item_to_receipt_structure_analysis function."""
    # Using the standalone function instead of class method
    analysis = item_to_receipt_structure_analysis(example_dynamo_item)
    assert analysis.receipt_id == 123
    assert analysis.image_id == "abc123"
    assert len(analysis.sections) == 1
    assert (
        analysis.overall_reasoning == "Clear structure with distinct sections"
    )


@pytest.mark.unit
def test_item_to_receipt_structure_analysis_invalid():
    """Test validation in item_to_receipt_structure_analysis."""
    with pytest.raises(ValueError):
        item_to_receipt_structure_analysis({})

    with pytest.raises(ValueError):
        item_to_receipt_structure_analysis({"image_id": "abc123"})

    with pytest.raises(ValueError):
        item_to_receipt_structure_analysis({"receipt_id": 123})
