"""
The Receipt Structure Analysis entity for a receipt represents the structural
analysis of receipt.
This is used for storing and retrieving data from DynamoDB.
"""

# pylint: disable=too-many-lines
# This module contains tightly coupled classes (SpatialPattern, ContentPattern,
# ReceiptSection, ReceiptStructureAnalysis) that represent a single entity's
# structure. Splitting would fragment related code and complicate imports.

import decimal
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any, Generator

from receipt_dynamo.entities.entity_mixins import SerializationMixin
from receipt_dynamo.entities.util import assert_type, format_type_error


@dataclass(eq=True, unsafe_hash=False)
class SpatialPattern:
    """
    Represents a spatial pattern found in receipt sections.

    Spatial patterns describe how elements are physically arranged in a
    receipt, such as being aligned, grouped, or separated by whitespace.
    """

    pattern_type: str
    description: str
    metadata: dict[str, Any] | None = None

    def __post_init__(self):
        """
        Initialize a SpatialPattern.

        Raises:
            TypeError: If the input types are not as expected
            ValueError: If required values are missing or invalid
        """
        assert_type("pattern_type", self.pattern_type, str)
        assert_type("description", self.description, str)
        if self.metadata is not None:
            assert_type("metadata", self.metadata, dict)

        if self.metadata is None:
            self.metadata = {}
        else:
            self.metadata = deepcopy(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        """Convert the SpatialPattern to a dictionary."""
        return {
            "pattern_type": self.pattern_type,
            "description": self.description,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SpatialPattern":
        """
        Create a SpatialPattern from a dictionary.

        Args:
            data: Dictionary containing pattern data

        Returns:
            SpatialPattern: The created pattern object

        Raises:
            TypeError: If data is not a dictionary
            KeyError: If required keys are missing
        """
        assert_type("data", data, dict)

        return cls(
            pattern_type=str(
                data.get("pattern_type", data.get("type", "generic"))
            ),
            description=str(data.get("description", "")),
            metadata=data.get("metadata", {}),
        )

    def __repr__(self) -> str:
        """Return a string representation of the SpatialPattern."""
        return (
            f"SpatialPattern(pattern_type={self.pattern_type!r}, "
            f"description={self.description!r})"
        )


@dataclass(eq=True, unsafe_hash=False)
class ContentPattern:
    """
    Represents a content pattern found in receipt sections.

    Content patterns describe the textual characteristics of receipt sections,
    such as containing dates, prices, or specific keywords.
    """

    pattern_type: str
    description: str
    examples: list[str] | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self):
        """
        Initialize a ContentPattern.

        Raises:
            TypeError: If the input types are not as expected
            ValueError: If required values are missing or invalid
        """
        assert_type("pattern_type", self.pattern_type, str)
        assert_type("description", self.description, str)
        if self.examples is not None:
            assert_type("examples", self.examples, list)
        if self.metadata is not None:
            assert_type("metadata", self.metadata, dict)

        if self.examples is None:
            self.examples = []
        else:
            self.examples = list(self.examples)
        if self.metadata is None:
            self.metadata = {}
        else:
            self.metadata = deepcopy(self.metadata)

        # Validate that all examples are strings
        for i, example in enumerate(self.examples):
            assert_type(f"examples[{i}]", example, str)

    def to_dict(self) -> dict[str, Any]:
        """Convert the ContentPattern to a dictionary."""
        return {
            "pattern_type": self.pattern_type,
            "description": self.description,
            "examples": self.examples,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContentPattern":
        """
        Create a ContentPattern from a dictionary.

        Args:
            data: Dictionary containing pattern data

        Returns:
            ContentPattern: The created pattern object

        Raises:
            TypeError: If data is not a dictionary
            KeyError: If required keys are missing
        """
        assert_type("data", data, dict)

        examples = data.get("examples", [])
        if examples and not isinstance(examples, list):
            examples = [str(ex) for ex in examples]
        else:
            # Ensure all examples are strings
            examples = [str(ex) for ex in examples]

        return cls(
            pattern_type=str(
                data.get("pattern_type", data.get("type", "generic"))
            ),
            description=str(data.get("description", "")),
            examples=examples,
            metadata=data.get("metadata", {}),
        )

    def __repr__(self) -> str:
        """Return a string representation of the ContentPattern."""
        return (
            f"ContentPattern(pattern_type={self.pattern_type!r}, "
            f"description={self.description!r}, "
            f"examples={len(self.examples or [])})"
        )


class ReceiptSection:
    """
    Represents a section identified within a receipt's structure.

    Each section is characterized by its name, the line IDs it encompasses,
    the spatial and content patterns it exhibits, and detailed reasoning
    explaining why it was identified as a distinct section.
    """

    # pylint: disable=too-many-positional-arguments
    def __init__(
        self,
        name: str,
        line_ids: list[int],
        spatial_patterns: list[SpatialPattern],
        content_patterns: list[ContentPattern],
        reasoning: str,
        start_line: int | None = None,
        end_line: int | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """
        Initialize a ReceiptSection.

        Args:
            name: The name of the section (e.g., "header", "body", "footer")
            line_ids: List of line IDs contained in this section
            spatial_patterns: List of spatial patterns observed in this section
            content_patterns: List of content patterns observed in this section
            reasoning: Explanation for why this was identified as a section
            start_line: The first line of the section
            end_line: The last line of the section
            metadata: Additional metadata for the section

        Raises:
            TypeError: If the input types are not as expected
            ValueError: If required values are missing or invalid
        """
        assert_type("name", name, str)
        assert_type("line_ids", line_ids, list)
        assert_type("spatial_patterns", spatial_patterns, list)
        assert_type("content_patterns", content_patterns, list)
        assert_type("reasoning", reasoning, str)
        if start_line is not None:
            assert_type("start_line", start_line, int)
        if end_line is not None:
            assert_type("end_line", end_line, int)
        if metadata is not None:
            assert_type("metadata", metadata, dict)

        # Validate line_ids are finite integers without mutating the input.
        validated_line_ids = []
        for i, line_id in enumerate(line_ids):
            if isinstance(line_id, bool) or not isinstance(
                line_id, (int, float, decimal.Decimal)
            ):
                raise TypeError(
                    format_type_error(
                        f"line_ids[{i}]",
                        line_id,
                        (int, float, decimal.Decimal),
                    )
                )
            if (
                isinstance(line_id, float)
                and not isfinite(line_id)
                or isinstance(line_id, decimal.Decimal)
                and not line_id.is_finite()
                or int(line_id) != line_id
            ):
                raise ValueError(f"line_ids[{i}] must be a finite integer")
            validated_line_ids.append(int(line_id))

        # Validate patterns are of correct type
        validated_spatial_patterns = []
        for i, pattern in enumerate(spatial_patterns):
            if not isinstance(pattern, SpatialPattern):
                if isinstance(pattern, dict):
                    pattern = SpatialPattern.from_dict(pattern)
                elif isinstance(pattern, str):
                    pattern = SpatialPattern(
                        pattern_type="legacy", description=pattern
                    )
                else:
                    raise TypeError(
                        format_type_error(
                            f"spatial_patterns[{i}]",
                            pattern,
                            (SpatialPattern, dict),
                        )
                    )
            validated_spatial_patterns.append(pattern)

        validated_content_patterns = []
        for i, content_pattern in enumerate(content_patterns):
            if not isinstance(content_pattern, ContentPattern):
                if isinstance(content_pattern, dict):
                    content_pattern = ContentPattern.from_dict(content_pattern)
                elif isinstance(content_pattern, str):
                    content_pattern = ContentPattern(
                        pattern_type="legacy", description=content_pattern
                    )
                else:
                    raise TypeError(
                        format_type_error(
                            f"content_patterns[{i}]",
                            content_pattern,
                            (ContentPattern, dict),
                        )
                    )
            validated_content_patterns.append(content_pattern)

        self.name = name
        self.line_ids = validated_line_ids
        self.spatial_patterns = validated_spatial_patterns
        self.content_patterns = validated_content_patterns
        self.reasoning = reasoning

        # Calculate start and end lines if not provided
        self.start_line: int | None
        self.end_line: int | None
        if line_ids:
            self.start_line = (
                start_line if start_line is not None else min(line_ids)
            )
            self.end_line = end_line if end_line is not None else max(line_ids)
        else:
            self.start_line = start_line
            self.end_line = end_line

        self.metadata = deepcopy(metadata) if metadata is not None else {}

    def to_dict(self) -> dict[str, Any]:
        """Convert the ReceiptSection to a dictionary."""
        return {
            "name": self.name,
            "line_ids": self.line_ids,
            "spatial_patterns": [
                pattern.to_dict() if hasattr(pattern, "to_dict") else pattern
                for pattern in self.spatial_patterns
            ],
            "content_patterns": [
                pattern.to_dict() if hasattr(pattern, "to_dict") else pattern
                for pattern in self.content_patterns
            ],
            "reasoning": self.reasoning,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReceiptSection":
        """
        Create a ReceiptSection from a dictionary.

        Args:
            data: Dictionary containing section data

        Returns:
            ReceiptSection: The created section object

        Raises:
            TypeError: If data is not a dictionary
            KeyError: If required keys are missing
        """
        assert_type("data", data, dict)

        # Convert content patterns from dict to ContentPattern objects if
        # needed
        content_patterns = []
        for pattern in data.get("content_patterns", []):
            if isinstance(pattern, dict):
                content_patterns.append(ContentPattern.from_dict(pattern))
            elif isinstance(pattern, str):
                # For backward compatibility with old data format
                content_patterns.append(
                    ContentPattern(
                        pattern_type="legacy", description=str(pattern)
                    )
                )
            elif isinstance(pattern, ContentPattern):
                content_patterns.append(pattern)
            else:
                raise TypeError(
                    format_type_error(
                        "content_pattern", pattern, (dict, str, ContentPattern)
                    )
                )

        # Convert spatial patterns from dict to SpatialPattern objects if
        # needed
        spatial_patterns = []
        for pattern in data.get("spatial_patterns", []):
            if isinstance(pattern, dict):
                spatial_patterns.append(SpatialPattern.from_dict(pattern))
            elif isinstance(pattern, str):
                # For backward compatibility with old data format
                spatial_patterns.append(
                    SpatialPattern(
                        pattern_type="legacy", description=str(pattern)
                    )
                )
            elif isinstance(pattern, SpatialPattern):
                spatial_patterns.append(pattern)
            else:
                raise TypeError(
                    format_type_error(
                        "spatial_pattern", pattern, (dict, str, SpatialPattern)
                    )
                )

        # Ensure line_ids are integers
        line_ids = []
        for line_id in data.get("line_ids", []):
            if isinstance(line_id, bool):
                raise TypeError(format_type_error("line_id", line_id, int))
            if isinstance(line_id, (int, float, decimal.Decimal)):
                if (
                    isinstance(line_id, float)
                    and not isfinite(line_id)
                    or isinstance(line_id, decimal.Decimal)
                    and not line_id.is_finite()
                    or int(line_id) != line_id
                ):
                    raise ValueError("line_id must be a finite integer")
                line_ids.append(int(line_id))
            else:
                try:
                    line_ids.append(int(line_id))
                except (ValueError, TypeError) as e:
                    raise TypeError(
                        format_type_error("line_id", line_id, int)
                    ) from e

        # Handle start_line and end_line
        start_line = data.get("start_line")
        if start_line is not None:
            try:
                start_line = int(start_line)
            except (ValueError, TypeError):
                start_line = None

        end_line = data.get("end_line")
        if end_line is not None:
            try:
                end_line = int(end_line)
            except (ValueError, TypeError):
                end_line = None

        return cls(
            name=str(data.get("name", "")),
            line_ids=line_ids,
            spatial_patterns=spatial_patterns,
            content_patterns=content_patterns,
            reasoning=str(data.get("reasoning", "")),
            start_line=start_line,
            end_line=end_line,
            metadata=data.get("metadata", {}),
        )

    def __eq__(self, other: object) -> bool:
        """Check if two ReceiptSection objects are equal."""
        if not isinstance(other, ReceiptSection):
            return False
        return (
            self.name == other.name
            and self.line_ids == other.line_ids
            and self.spatial_patterns == other.spatial_patterns
            and self.content_patterns == other.content_patterns
            and self.reasoning == other.reasoning
            and self.start_line == other.start_line
            and self.end_line == other.end_line
            and self.metadata == other.metadata
        )

    def __repr__(self) -> str:
        """Return a string representation of the ReceiptSection."""
        return (
            f"ReceiptSection(name={self.name!r}, lines={len(self.line_ids)})"
        )


class ReceiptStructureAnalysis(SerializationMixin):
    """
    Represents the structure analysis for a receipt in DynamoDB.

    This class contains information about the different sections identified
    in a receipt and the reasoning behind the analysis.
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "sections",
        "overall_reasoning",
    }

    # pylint: disable=too-many-positional-arguments
    def __init__(
        self,
        receipt_id: int,
        image_id: str,
        sections: list[ReceiptSection],
        overall_reasoning: str,
        version: str = "1.0.0",
        metadata: dict[str, Any] | None = None,
        timestamp_added: datetime | None = None,
        timestamp_updated: datetime | None = None,
        processing_metrics: dict[str, Any] | None = None,
        source_info: dict[str, Any] | None = None,
        processing_history: list[dict[str, Any]] | None = None,
    ):
        """
        Initialize a ReceiptStructureAnalysis.

        Args:
            receipt_id: The ID of the receipt
            image_id: The ID of the image
            sections: The sections identified in the receipt
            overall_reasoning: The overall reasoning for the structure analysis
            version: The version of the analysis
            metadata: Additional metadata
            timestamp_added: When the analysis was first created
            timestamp_updated: When the analysis was last updated
            processing_metrics: Metrics related to the processing
            source_info: Information about the source of the analysis
            processing_history: History of processing events

        Raises:
            TypeError: If the input types are not as expected
            ValueError: If required values are missing or invalid
        """
        # Type checking for each parameter
        if isinstance(receipt_id, bool) or not isinstance(
            receipt_id, (int, float, decimal.Decimal, str)
        ):
            raise TypeError(
                format_type_error(
                    "receipt_id",
                    receipt_id,
                    (int, float, decimal.Decimal, str),
                )
            )
        try:
            normalized_receipt_id = int(receipt_id)
        except (ValueError, TypeError, OverflowError) as e:
            raise ValueError(
                f"receipt_id must be convertible to int, got {receipt_id}"
            ) from e
        if str(receipt_id).lower() in {"nan", "inf", "-inf", "infinity"}:
            raise ValueError("receipt_id must be a finite integer")
        if isinstance(receipt_id, (float, decimal.Decimal)) and (
            normalized_receipt_id != receipt_id
        ):
            raise ValueError("receipt_id must be an integer")
        if normalized_receipt_id <= 0:
            raise ValueError("receipt_id must be positive")
        receipt_id = normalized_receipt_id

        assert_type("image_id", image_id, str)
        assert_type("sections", sections, list)
        assert_type("overall_reasoning", overall_reasoning, str)
        assert_type("version", version, str)
        if metadata is not None:
            assert_type("metadata", metadata, dict)
        if timestamp_added is not None:
            assert_type("timestamp_added", timestamp_added, datetime)
        if timestamp_updated is not None:
            assert_type("timestamp_updated", timestamp_updated, datetime)
        if processing_metrics is not None:
            assert_type("processing_metrics", processing_metrics, dict)
        if source_info is not None:
            assert_type("source_info", source_info, dict)
        if processing_history is not None:
            assert_type("processing_history", processing_history, list)
            for index, entry in enumerate(processing_history):
                assert_type(f"processing_history[{index}]", entry, dict)

        # Validate each section is a ReceiptSection or can be converted to one
        validated_sections = []
        for i, section in enumerate(sections):
            if isinstance(section, ReceiptSection):
                validated_sections.append(section)
            elif isinstance(section, dict):
                validated_sections.append(ReceiptSection.from_dict(section))
            else:
                raise TypeError(
                    format_type_error(
                        f"sections[{i}]", section, (ReceiptSection, dict)
                    )
                )

        self.receipt_id = receipt_id
        self.image_id = image_id
        self.sections = validated_sections
        self.overall_reasoning = overall_reasoning
        self.version = version
        self.metadata = deepcopy(metadata) if metadata is not None else {}
        self.timestamp_added = timestamp_added or datetime.now()
        self.timestamp_updated = timestamp_updated

        # Initialize processing metrics if not provided
        if processing_metrics is None:
            self.processing_metrics = {
                "section_count": len(self.sections),
                "section_types": list(
                    set(section.name for section in self.sections)
                ),
                "pattern_counts": {
                    "spatial_patterns": sum(
                        len(s.spatial_patterns) for s in self.sections
                    ),
                    "content_patterns": sum(
                        len(s.content_patterns) for s in self.sections
                    ),
                },
            }
        else:
            self.processing_metrics = deepcopy(processing_metrics)

        self.source_info = (
            deepcopy(source_info) if source_info is not None else {}
        )

        # Initialize processing history if not provided
        if processing_history is None:
            self.processing_history: list[dict[str, Any]] = [
                {
                    "event": "creation",
                    "timestamp": self.timestamp_added.isoformat(),
                    "details": "Initial structure analysis",
                }
            ]
        else:
            self.processing_history = deepcopy(processing_history)

        # Sort sections by start line for consistent order
        self.sections.sort(
            key=lambda section: (
                section.start_line
                if section.start_line is not None
                else float("inf")
            )
        )

    @property
    def discovered_sections(self) -> list[ReceiptSection]:
        """
        Backward compatibility property for code that still uses
        discovered_sections.

        Returns:
            list[ReceiptSection]: The sections in this analysis
        """
        return self.sections

    @property
    def key(self) -> dict[str, dict[str, str]]:
        """
        Get the primary key for the DynamoDB table.

        Returns:
            dict[str, dict[str, str]]: The primary key
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#ANALYSIS#STRUCTURE#"
                    f"{self.version}"
                )
            },
        }

    def gsi1_key(self) -> dict[str, dict[str, str]]:
        """
        Get the GSI1 key for the DynamoDB table.

        Returns:
            dict[str, str]: The GSI1 key
        """
        timestamp_str = self.timestamp_added.isoformat()
        return {
            "GSI1PK": {"S": "ANALYSIS_TYPE"},
            "GSI1SK": {"S": f"STRUCTURE#{timestamp_str}"},
        }

    def to_item(self) -> dict[str, Any]:
        """
        Convert to a DynamoDB item.

        Returns:
            dict[str, Any]: The item representation for DynamoDB
        """
        # Create the item with properly formatted values
        item = {
            **self.key,
            **self.gsi1_key(),
            "TYPE": {"S": "RECEIPT_STRUCTURE_ANALYSIS"},
            "receipt_id": {"N": str(self.receipt_id)},
            "image_id": {"S": self.image_id},
            "entity_type": {"S": "STRUCTURE_ANALYSIS"},
            "sections": self._python_to_dynamo(
                [section.to_dict() for section in self.sections]
            ),
            "overall_reasoning": {"S": self.overall_reasoning},
            "version": {"S": self.version},
            "metadata": self._python_to_dynamo(self.metadata),
            "timestamp_added": {"S": self.timestamp_added.isoformat()},
            "processing_metrics": self._python_to_dynamo(
                self.processing_metrics
            ),
            "source_info": self._python_to_dynamo(self.source_info),
            "processing_history": self._python_to_dynamo(
                self.processing_history
            ),
        }

        if self.timestamp_updated:
            item["timestamp_updated"] = {
                "S": self.timestamp_updated.isoformat()
            }

        return item

    def get_section_by_name(self, name: str) -> ReceiptSection | None:
        """Find a section by its name."""
        for section in self.sections:
            if section.name.lower() == name.lower():
                return section
        return None

    def get_section_for_line(self, line_id: int) -> ReceiptSection | None:
        """Find which section contains the given line ID."""
        for section in self.sections:
            if line_id in section.line_ids:
                return section
        return None

    def get_sections_with_pattern(
        self, pattern_type: str
    ) -> list[ReceiptSection]:
        """Find sections that contain a specific pattern type."""
        matching_sections = []
        for section in self.sections:
            # Check spatial patterns
            for pattern in section.spatial_patterns:
                if (
                    pattern_type.lower() in pattern.pattern_type.lower()
                    or pattern_type.lower() in pattern.description.lower()
                ):
                    matching_sections.append(section)
                    break

            # If already matched on spatial pattern, skip content patterns
            if section in matching_sections:
                continue

            # Check content patterns
            for content_pattern in section.content_patterns:
                if (
                    pattern_type.lower()
                    in content_pattern.pattern_type.lower()
                    or pattern_type.lower()
                    in content_pattern.description.lower()
                ):
                    matching_sections.append(section)
                    break

        return matching_sections

    def summarize_structure(self) -> str:
        """
        Generate a human-readable summary of the receipt structure.

        Returns:
            str: A text summary of the receipt's structure
        """
        result: list[str] = [
            f"Receipt contains {len(self.sections)} sections."
        ]

        # Add overall reasoning
        if self.overall_reasoning:
            result.append(f"Overall analysis: {self.overall_reasoning}")

        # Add section summaries
        for i, section in enumerate(self.sections):
            section_summary = [
                f"Section {i+1}: {section.name} "
                f"({len(section.line_ids)} lines)"
            ]

            if section.reasoning:
                section_summary.append(f"  Reasoning: {section.reasoning}")

            if section.spatial_patterns:
                pattern_texts = [
                    p.description for p in section.spatial_patterns
                ]
                section_summary.append(
                    f"  Spatial patterns: {', '.join(pattern_texts[:3])}"
                )

            if section.content_patterns:
                pattern_texts = [
                    p.description for p in section.content_patterns
                ]
                section_summary.append(
                    f"  Content patterns: {', '.join(pattern_texts[:3])}"
                )

            result.append("\n".join(section_summary))

        return "\n\n".join(result)

    def __eq__(self, other: object) -> bool:
        """Check if two ReceiptStructureAnalysis objects are equal."""
        if not isinstance(other, ReceiptStructureAnalysis):
            return False
        return (
            self.receipt_id == other.receipt_id
            and self.image_id == other.image_id
            and self.sections == other.sections
            and self.overall_reasoning == other.overall_reasoning
            and self.version == other.version
            and self.metadata == other.metadata
            and self.timestamp_added == other.timestamp_added
            and self.timestamp_updated == other.timestamp_updated
            and self.processing_metrics == other.processing_metrics
            and self.source_info == other.source_info
            and self.processing_history == other.processing_history
        )

    def __iter__(self) -> Generator[tuple[str, Any], None, None]:
        """Iterator for the analysis attributes."""
        yield "receipt_id", self.receipt_id
        yield "image_id", self.image_id
        yield "sections", self.sections
        yield "overall_reasoning", self.overall_reasoning
        yield "version", self.version
        yield "metadata", self.metadata
        yield "timestamp_added", self.timestamp_added
        if self.timestamp_updated:
            yield "timestamp_updated", self.timestamp_updated
        yield "processing_metrics", self.processing_metrics
        yield "source_info", self.source_info
        yield "processing_history", self.processing_history

    def __repr__(self) -> str:
        """Return a string representation of the ReceiptStructureAnalysis."""
        return (
            f"ReceiptStructureAnalysis(receipt_id={self.receipt_id}, "
            f"image_id='{self.image_id}', "
            f"sections={len(self.sections)}, "
            f"version='{self.version}')"
        )

    def __hash__(self) -> int:
        """Get the hash of the ReceiptStructureAnalysis."""
        return hash(
            (
                self.receipt_id,
                self.image_id,
                tuple(
                    hash(json.dumps(section.to_dict(), sort_keys=True))
                    for section in self.sections
                ),
                self.overall_reasoning,
                self.version,
                self.timestamp_added,
            )
        )

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptStructureAnalysis":
        """Convert a DynamoDB item to a ReceiptStructureAnalysis.

        Args:
            item: The DynamoDB item

        Returns:
            ReceiptStructureAnalysis: The structure analysis object

        Raises:
            ValueError: If required data is missing or invalid
        """
        if not item:
            raise ValueError(
                "Cannot create ReceiptStructureAnalysis from empty item"
            )

        if not cls.REQUIRED_KEYS.issubset(item.keys()):
            missing_keys = cls.REQUIRED_KEYS - item.keys()
            raise ValueError(f"Item is missing required keys: {missing_keys}")

        receipt_id = (
            item["SK"].get("S", "").split("#")[1] if "SK" in item else None
        )
        if receipt_id is None:
            raise ValueError(
                "receipt_id is required but was not found in item"
            )

        image_id = (
            item["PK"].get("S", "").split("#")[1] if "PK" in item else None
        )
        if image_id is None:
            raise ValueError("image_id is required but was not found in item")

        # Parse sections
        sections = cls._parse_sections(item)

        # Extract simple string fields
        overall_reasoning = _extract_string_field(
            item, "overall_reasoning", ""
        )
        version = _extract_string_field(item, "version", "1.0.0")

        # Parse timestamps
        timestamp_added = _parse_timestamp(item.get("timestamp_added"))
        if timestamp_added is None:
            timestamp_added = datetime.now()
        timestamp_updated = _parse_timestamp(item.get("timestamp_updated"))

        # Parse complex nested fields
        processing_metrics = cls._dynamo_to_python(
            item.get("processing_metrics", {"M": {}})
        )
        source_info = cls._dynamo_to_python(item.get("source_info", {"M": {}}))
        processing_history = cls._dynamo_to_python(
            item.get("processing_history", {"L": []})
        )
        overall_metadata = cls._dynamo_to_python(
            item.get("metadata", {"M": {}})
        )

        return cls(
            receipt_id=receipt_id,
            image_id=image_id,
            sections=sections,
            overall_reasoning=overall_reasoning,
            version=version,
            metadata=overall_metadata,
            timestamp_added=timestamp_added,
            timestamp_updated=timestamp_updated,
            processing_metrics=processing_metrics,
            source_info=source_info,
            processing_history=processing_history,
        )

    @classmethod
    def _parse_sections(cls, item: dict[str, Any]) -> list[ReceiptSection]:
        """Parse sections from DynamoDB item format."""
        sections = []
        sections_attr = item.get("sections", {"L": []})
        sections_list = (
            sections_attr.get("L", [])
            if isinstance(sections_attr, dict)
            else sections_attr
        )

        for section_dict in sections_list:
            if isinstance(section_dict, dict) and "M" in section_dict:
                section_data = cls._dynamo_to_python(section_dict)
                sections.append(ReceiptSection.from_dict(section_data))
            else:
                sections.append(ReceiptSection.from_dict(section_dict))

        return sections


# Helper functions for DynamoDB item parsing


def _extract_string_field(
    item: dict[str, Any], field_name: str, default: str
) -> str:
    """Extract a string field from DynamoDB item format."""
    if field_name not in item:
        return default
    field_attr = item[field_name]
    if (
        not isinstance(field_attr, dict)
        or set(field_attr) != {"S"}
        or not isinstance(field_attr["S"], str)
    ):
        raise ValueError(f"{field_name} must be a DynamoDB string attribute")
    return field_attr["S"]


def _parse_timestamp(attr: Any) -> datetime | None:
    """Parse a timestamp from DynamoDB attribute format."""
    if not attr or (isinstance(attr, dict) and attr.get("NULL")):
        return None
    if not isinstance(attr, dict) or "S" not in attr:
        raise ValueError("timestamp must be a DynamoDB string attribute")
    timestamp_str = attr["S"]
    try:
        return datetime.fromisoformat(timestamp_str)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            "timestamp must contain a valid ISO timestamp"
        ) from exc


def item_to_receipt_structure_analysis(
    item: dict[str, Any],
) -> ReceiptStructureAnalysis:
    """
    Convert a DynamoDB item to a ReceiptStructureAnalysis.

    Args:
        item: The DynamoDB item

    Returns:
        ReceiptStructureAnalysis: The structure analysis object

    Raises:
        ValueError: If required data is missing or invalid
    """
    return ReceiptStructureAnalysis.from_item(item)
