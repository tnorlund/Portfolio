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
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional, Tuple

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
    metadata: Optional[Dict[str, Any]] = None

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

    def to_dict(self) -> Dict[str, Any]:
        """Convert the SpatialPattern to a dictionary."""
        return {
            "pattern_type": self.pattern_type,
            "description": self.description,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SpatialPattern":
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
    examples: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None

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
        if self.metadata is None:
            self.metadata = {}

        # Validate that all examples are strings
        for i, example in enumerate(self.examples):
            assert_type(f"examples[{i}]", example, str)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the ContentPattern to a dictionary."""
        return {
            "pattern_type": self.pattern_type,
            "description": self.description,
            "examples": self.examples,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContentPattern":
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
            f"examples={len(self.examples)})"
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
        line_ids: List[int],
        spatial_patterns: List[SpatialPattern],
        content_patterns: List[ContentPattern],
        reasoning: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
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

        # Validate line_ids are integers
        for i, line_id in enumerate(line_ids):
            if not isinstance(line_id, (int, float, decimal.Decimal)):
                raise TypeError(
                    format_type_error(
                        f"line_ids[{i}]",
                        line_id,
                        (int, float, decimal.Decimal),
                    )
                )
            if isinstance(line_id, (float, decimal.Decimal)):
                line_ids[i] = int(line_id)

        # Validate patterns are of correct type
        for i, pattern in enumerate(spatial_patterns):
            if not isinstance(pattern, SpatialPattern):
                if isinstance(pattern, dict):
                    spatial_patterns[i] = SpatialPattern.from_dict(pattern)
                elif isinstance(pattern, str):
                    spatial_patterns[i] = SpatialPattern(
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

        for i, content_pattern in enumerate(content_patterns):
            if not isinstance(content_pattern, ContentPattern):
                if isinstance(content_pattern, dict):
                    content_patterns[i] = ContentPattern.from_dict(
                        content_pattern
                    )
                elif isinstance(content_pattern, str):
                    content_patterns[i] = ContentPattern(
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

        self.name = name
        self.line_ids = line_ids
        self.spatial_patterns = spatial_patterns
        self.content_patterns = content_patterns
        self.reasoning = reasoning

        # Calculate start and end lines if not provided
        self.start_line: Optional[int]
        self.end_line: Optional[int]
        if line_ids:
            self.start_line = (
                start_line if start_line is not None else min(line_ids)
            )
            self.end_line = end_line if end_line is not None else max(line_ids)
        else:
            self.start_line = start_line
            self.end_line = end_line

        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
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
    def from_dict(cls, data: Dict[str, Any]) -> "ReceiptSection":
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
            if isinstance(line_id, (int, float, decimal.Decimal)):
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


class ReceiptStructureAnalysis:
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
        sections: List[ReceiptSection],
        overall_reasoning: str,
        version: str = "1.0.0",
        metadata: Optional[Dict[str, Any]] = None,
        timestamp_added: Optional[datetime] = None,
        timestamp_updated: Optional[datetime] = None,
        processing_metrics: Optional[Dict[str, Any]] = None,
        source_info: Optional[Dict[str, Any]] = None,
        processing_history: Optional[List[Dict[str, Any]]] = None,
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
        if not isinstance(receipt_id, (int, float, decimal.Decimal, str)):
            raise TypeError(
                format_type_error(
                    "receipt_id",
                    receipt_id,
                    (int, float, decimal.Decimal, str),
                )
            )
        try:
            receipt_id = int(receipt_id)
        except (ValueError, TypeError) as e:
            raise ValueError(
                f"receipt_id must be convertible to int, got {receipt_id}"
            ) from e

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
        self.metadata = metadata or {}
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
            self.processing_metrics = processing_metrics

        self.source_info = source_info or {}

        # Initialize processing history if not provided
        if processing_history is None:
            self.processing_history = [
                {
                    "event": "creation",
                    "timestamp": self.timestamp_added.isoformat(),
                    "details": "Initial structure analysis",
                }
            ]
        else:
            self.processing_history = processing_history

        # Sort sections by start line for consistent order
        self.sections.sort(
            key=lambda section: (
                section.start_line
                if section.start_line is not None
                else float("inf")
            )
        )

    @property
    def discovered_sections(self) -> List[ReceiptSection]:
        """
        Backward compatibility property for code that still uses
        discovered_sections.

        Returns:
            List[ReceiptSection]: The sections in this analysis
        """
        return self.sections

    @property
    def key(self) -> Dict[str, Dict[str, str]]:
        """
        Get the primary key for the DynamoDB table.

        Returns:
            Dict[str, Dict[str, str]]: The primary key
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

    def gsi1_key(self) -> Dict[str, Dict[str, str]]:
        """
        Get the GSI1 key for the DynamoDB table.

        Returns:
            Dict[str, str]: The GSI1 key
        """
        timestamp_str = self.timestamp_added.isoformat()
        return {
            "GSI1PK": {"S": "ANALYSIS_TYPE"},
            "GSI1SK": {"S": f"STRUCTURE#{timestamp_str}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """
        Convert to a DynamoDB item.

        Returns:
            Dict[str, Any]: The item representation for DynamoDB
        """
        # Create a list of sections in DynamoDB format
        section_list = []
        for section in self.sections:
            section_dict = {
                "M": {
                    "name": {"S": section.name},
                    "line_ids": {
                        "L": [
                            {"N": str(line_id)} for line_id in section.line_ids
                        ]
                    },
                    "reasoning": {"S": section.reasoning},
                    "start_line": {"N": str(section.start_line)},
                    "end_line": {"N": str(section.end_line)},
                    "spatial_patterns": {
                        "L": [
                            {
                                "M": {
                                    "pattern_type": {
                                        "S": pattern.pattern_type
                                    },
                                    "description": {"S": pattern.description},
                                    "metadata": {
                                        "M": {
                                            k: {"S": str(v)}
                                            for k, v in (
                                                pattern.metadata or {}
                                            ).items()
                                        }
                                    },
                                }
                            }
                            for pattern in section.spatial_patterns
                        ]
                    },
                    "content_patterns": {
                        "L": [
                            {
                                "M": {
                                    "pattern_type": {
                                        "S": pattern.pattern_type
                                    },
                                    "description": {"S": pattern.description},
                                    "examples": {
                                        "L": [
                                            {"S": ex}
                                            for ex in (pattern.examples or [])
                                        ]
                                    },
                                    "metadata": {
                                        "M": {
                                            k: {"S": str(v)}
                                            for k, v in (
                                                pattern.metadata or {}
                                            ).items()
                                        }
                                    },
                                }
                            }
                            for pattern in section.content_patterns
                        ]
                    },
                    "metadata": {
                        "M": {
                            k: {"S": str(v)}
                            for k, v in (section.metadata or {}).items()
                        }
                    },
                }
            }
            section_list.append(section_dict)

        # Format history entries
        history_list = []
        for entry in self.processing_history or []:
            history_dict = {
                "M": {
                    "event": {"S": entry.get("event", "")},
                    "timestamp": {"S": entry.get("timestamp", "")},
                    "details": {"S": entry.get("details", "")},
                }
            }
            history_list.append(history_dict)

        # Format metrics
        metrics_dict: Dict[str, Any] = {"M": {}}
        if self.processing_metrics:
            for key, value in self.processing_metrics.items():
                if key == "section_count":
                    metrics_dict["M"][key] = {"N": str(value)}
                elif key == "section_types":
                    if isinstance(value, list):
                        metrics_dict["M"][key] = {
                            "L": [{"S": str(t)} for t in value]
                        }
                    else:
                        metrics_dict["M"][key] = {"S": str(value)}
                elif key == "pattern_counts":
                    if isinstance(value, dict):
                        metrics_dict["M"][key] = {
                            "M": {k: {"N": str(v)} for k, v in value.items()}
                        }
                    else:
                        metrics_dict["M"][key] = {"S": str(value)}
                else:
                    # Default to string for unknown values
                    metrics_dict["M"][key] = {"S": str(value)}

        # Format source info
        source_info_dict: Dict[str, Any] = {"M": {}}
        if self.source_info:
            for key, value in self.source_info.items():
                source_info_dict["M"][key] = {"S": str(value)}

        # Create the item with properly formatted values
        item = {
            **self.key,
            **self.gsi1_key(),
            "TYPE": {"S": "RECEIPT_STRUCTURE_ANALYSIS"},
            "receipt_id": {"N": str(self.receipt_id)},
            "image_id": {"S": self.image_id},
            "entity_type": {"S": "STRUCTURE_ANALYSIS"},
            "sections": {"L": section_list},
            "overall_reasoning": {"S": self.overall_reasoning},
            "version": {"S": self.version},
            "metadata": {
                "M": {
                    k: {"S": str(v)} for k, v in (self.metadata or {}).items()
                }
            },
            "timestamp_added": {"S": self.timestamp_added.isoformat()},
            "processing_metrics": metrics_dict,
            "source_info": source_info_dict,
            "processing_history": {"L": history_list},
        }

        if self.timestamp_updated:
            item["timestamp_updated"] = {
                "S": self.timestamp_updated.isoformat()
            }

        return item

    def get_section_by_name(self, name: str) -> Optional[ReceiptSection]:
        """Find a section by its name."""
        for section in self.sections:
            if section.name.lower() == name.lower():
                return section
        return None

    def get_section_for_line(self, line_id: int) -> Optional[ReceiptSection]:
        """Find which section contains the given line ID."""
        for section in self.sections:
            if line_id in section.line_ids:
                return section
        return None

    def get_sections_with_pattern(
        self, pattern_type: str
    ) -> List[ReceiptSection]:
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
        result: List[str] = [
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
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
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
    def from_item(cls, item: Dict[str, Any]) -> "ReceiptStructureAnalysis":
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
        processing_metrics = _parse_processing_metrics(
            item.get("processing_metrics")
        )
        source_info = _parse_string_map(item.get("source_info"))
        processing_history = _parse_processing_history(
            item.get("processing_history")
        )
        overall_metadata = _parse_string_map(item.get("metadata"))

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
    def _parse_sections(cls, item: Dict[str, Any]) -> List[ReceiptSection]:
        """Parse sections from DynamoDB item format."""
        sections = []
        sections_attr = item.get("sections", {"L": []})
        sections_list = (
            sections_attr.get("L", [])
            if isinstance(sections_attr, dict)
            else sections_attr
        )

        for section_dict in sections_list:
            try:
                if isinstance(section_dict, dict) and "M" in section_dict:
                    section_data = _parse_section_map(section_dict["M"])
                    sections.append(ReceiptSection.from_dict(section_data))
                else:
                    sections.append(ReceiptSection.from_dict(section_dict))
            except (TypeError, ValueError):
                continue

        return sections


# Helper functions for DynamoDB item parsing


def _extract_string_field(
    item: Dict[str, Any], field_name: str, default: str
) -> str:
    """Extract a string field from DynamoDB item format."""
    field_attr = item.get(field_name, {"S": default})
    if isinstance(field_attr, dict):
        return field_attr.get("S", default)
    return str(field_attr)


def _parse_timestamp(attr: Any) -> Optional[datetime]:
    """Parse a timestamp from DynamoDB attribute format."""
    if not attr:
        return None
    timestamp_str = attr.get("S", "") if isinstance(attr, dict) else str(attr)
    try:
        return datetime.fromisoformat(timestamp_str)
    except (ValueError, TypeError):
        return None


def _parse_string_map(attr: Any) -> Dict[str, Any]:
    """Parse a simple string map from DynamoDB attribute format."""
    if not attr or not isinstance(attr, dict):
        return {}
    if "M" not in attr:
        return attr
    result: Dict[str, Any] = {}
    for k, v in attr["M"].items():
        if isinstance(v, dict) and "S" in v:
            result[k] = v["S"]
        else:
            result[k] = str(v)
    return result


def _parse_processing_metrics(attr: Any) -> Dict[str, Any]:
    """Parse processing metrics from DynamoDB attribute format."""
    if not attr or not isinstance(attr, dict):
        return {}
    if "M" not in attr:
        return attr

    result: Dict[str, Any] = {}
    for k, v in attr["M"].items():
        if not isinstance(v, dict):
            result[k] = v
        elif "N" in v:
            result[k] = int(v["N"])
        elif "S" in v:
            result[k] = v["S"]
        elif "L" in v:
            result[k] = [li.get("S", "") for li in v["L"]]
        elif "M" in v:
            result[k] = {
                sub_k: (
                    int(sub_v.get("N", 0))
                    if "N" in sub_v
                    else sub_v.get("S", "")
                )
                for sub_k, sub_v in v["M"].items()
            }
    return result


def _parse_processing_history(attr: Any) -> List[Dict[str, Any]]:
    """Parse processing history from DynamoDB attribute format."""
    if not attr or not isinstance(attr, dict):
        return []
    if "L" not in attr:
        return attr if isinstance(attr, list) else []

    result = []
    for entry in attr["L"]:
        if isinstance(entry, dict) and "M" in entry:
            entry_map = entry["M"]
            result.append({
                "event": entry_map.get("event", {}).get("S", ""),
                "timestamp": entry_map.get("timestamp", {}).get("S", ""),
                "details": entry_map.get("details", {}).get("S", ""),
            })
        else:
            result.append(entry)
    return result


def _parse_section_map(section_map: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a section map from DynamoDB format to plain dict."""
    section_data: Dict[str, Any] = {}

    # Basic properties
    section_data["name"] = section_map.get("name", {}).get("S", "")
    section_data["reasoning"] = section_map.get("reasoning", {}).get("S", "")

    # Line IDs
    section_data["line_ids"] = _parse_line_ids(
        section_map.get("line_ids", {}).get("L", [])
    )

    # Start/end lines
    start_line_attr = section_map.get("start_line", {}).get("N", "0")
    section_data["start_line"] = int(start_line_attr) if start_line_attr else 0
    end_line_attr = section_map.get("end_line", {}).get("N", "0")
    section_data["end_line"] = int(end_line_attr) if end_line_attr else 0

    # Patterns
    section_data["spatial_patterns"] = _parse_spatial_patterns(
        section_map.get("spatial_patterns", {}).get("L", [])
    )
    section_data["content_patterns"] = _parse_content_patterns(
        section_map.get("content_patterns", {}).get("L", [])
    )

    # Metadata
    section_data["metadata"] = _parse_pattern_metadata(
        section_map.get("metadata", {}).get("M", {})
    )

    return section_data


def _parse_line_ids(line_ids_attr: List[Any]) -> List[int]:
    """Parse line IDs from DynamoDB list format."""
    return [
        int(line_id["N"])
        for line_id in line_ids_attr
        if isinstance(line_id, dict) and "N" in line_id
    ]


def _parse_spatial_patterns(patterns_attr: List[Any]) -> List[Dict[str, Any]]:
    """Parse spatial patterns from DynamoDB list format."""
    patterns = []
    for sp in patterns_attr:
        if isinstance(sp, dict) and "M" in sp:
            sp_map = sp["M"]
            patterns.append({
                "pattern_type": sp_map.get("pattern_type", {}).get("S", ""),
                "description": sp_map.get("description", {}).get("S", ""),
                "metadata": _parse_pattern_metadata(
                    sp_map.get("metadata", {}).get("M", {})
                ),
            })
    return patterns


def _parse_content_patterns(patterns_attr: List[Any]) -> List[Dict[str, Any]]:
    """Parse content patterns from DynamoDB list format."""
    patterns = []
    for cp in patterns_attr:
        if isinstance(cp, dict) and "M" in cp:
            cp_map = cp["M"]
            examples = [
                ex["S"]
                for ex in cp_map.get("examples", {}).get("L", [])
                if isinstance(ex, dict) and "S" in ex
            ]
            patterns.append({
                "pattern_type": cp_map.get("pattern_type", {}).get("S", ""),
                "description": cp_map.get("description", {}).get("S", ""),
                "examples": examples,
                "metadata": _parse_pattern_metadata(
                    cp_map.get("metadata", {}).get("M", {})
                ),
            })
    return patterns


def _parse_pattern_metadata(metadata_attr: Dict[str, Any]) -> Dict[str, Any]:
    """Parse pattern metadata from DynamoDB map format."""
    return {k: v.get("S", "") for k, v in metadata_attr.items()}


def item_to_receipt_structure_analysis(
    item: Dict[str, Any],
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
