from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime
from .metadata import MetadataMixin
from receipt_dynamo.entities.receipt_structure_analysis import (
    ReceiptStructureAnalysis,
    ReceiptSection as DynamoReceiptSection,
    SpatialPattern as DynamoSpatialPattern,
    ContentPattern as DynamoContentPattern,
)


@dataclass
class SpatialPattern:
    """
    Represents a spatial pattern found in receipt sections.

    Spatial patterns describe how elements are physically arranged in a receipt,
    such as being aligned, grouped, or separated by whitespace.
    """

    pattern_type: str  # e.g., "alignment", "grouping", "spacing", "position"
    description: str  # e.g., "left-aligned", "top of receipt", "items grouped together"
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        """
        Convert the SpatialPattern to a dictionary representation.

        Returns:
            Dict: Dictionary representation of the pattern
        """
        return {
            "pattern_type": self.pattern_type,
            "description": self.description,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "SpatialPattern":
        """
        Create a SpatialPattern from a dictionary representation.

        Args:
            data (Dict): Dictionary containing pattern data

        Returns:
            SpatialPattern: The created pattern object
        """
        return cls(
            pattern_type=data.get("pattern_type", data.get("type", "generic")),
            description=data.get("description", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass
class ContentPattern:
    """
    Represents a content pattern found in receipt sections.

    Content patterns describe the textual characteristics of receipt sections,
    such as containing dates, prices, or specific keywords.
    """

    pattern_type: str  # e.g., "keywords", "formatting", "semantic"
    description: (
        str  # e.g., "contains business name", "has date format", "shows prices"
    )
    examples: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        """
        Convert the ContentPattern to a dictionary representation.

        Returns:
            Dict: Dictionary representation of the pattern
        """
        return {
            "pattern_type": self.pattern_type,
            "description": self.description,
            "examples": self.examples,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ContentPattern":
        """
        Create a ContentPattern from a dictionary representation.

        Args:
            data (Dict): Dictionary containing pattern data

        Returns:
            ContentPattern: The created pattern object
        """
        return cls(
            pattern_type=data.get("pattern_type", data.get("type", "generic")),
            description=data.get("description", ""),
            examples=data.get("examples", []),
            metadata=data.get("metadata", {}),
        )


@dataclass
class ReceiptSection:
    """
    Represents a section identified within a receipt's structure.

    Each section is characterized by its name, the line IDs it encompasses,
    the spatial and content patterns it exhibits, and detailed reasoning
    explaining why it was identified as a distinct section.
    """

    name: str  # e.g., "header", "body", "footer", "payment_details"
    line_ids: List[int]
    spatial_patterns: List[SpatialPattern]
    content_patterns: List[ContentPattern]
    reasoning: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    metadata: Dict = field(default_factory=dict)

    def __post_init__(self):
        # Calculate start and end lines if not provided
        if self.start_line is None and self.line_ids:
            self.start_line = min(self.line_ids)
        if self.end_line is None and self.line_ids:
            self.end_line = max(self.line_ids)

    def to_dict(self) -> Dict:
        """
        Convert the ReceiptSection to a dictionary representation.

        Returns:
            Dict: Dictionary representation of the section
        """
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
    def from_dict(cls, data: Dict) -> "ReceiptSection":
        """
        Create a ReceiptSection from a dictionary representation.

        Args:
            data (Dict): Dictionary containing section data

        Returns:
            ReceiptSection: The created section object
        """
        # Convert content patterns from dict to ContentPattern objects if needed
        content_patterns = []
        for pattern in data.get("content_patterns", []):
            if isinstance(pattern, dict):
                content_patterns.append(ContentPattern.from_dict(pattern))
            elif isinstance(pattern, str):
                # For backward compatibility with old data format
                content_patterns.append(
                    ContentPattern(pattern_type="legacy", description=pattern)
                )
            else:
                content_patterns.append(pattern)

        # Convert spatial patterns from dict to SpatialPattern objects if needed
        spatial_patterns = []
        for pattern in data.get("spatial_patterns", []):
            if isinstance(pattern, dict):
                spatial_patterns.append(SpatialPattern.from_dict(pattern))
            elif isinstance(pattern, str):
                # For backward compatibility with old data format
                spatial_patterns.append(
                    SpatialPattern(pattern_type="legacy", description=pattern)
                )
            else:
                spatial_patterns.append(pattern)

        return cls(
            name=data.get("name", ""),
            line_ids=data.get("line_ids", []),
            spatial_patterns=spatial_patterns,
            content_patterns=content_patterns,
            reasoning=data.get("reasoning", ""),
            start_line=data.get("start_line"),
            end_line=data.get("end_line"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class StructureAnalysis(MetadataMixin):
    """
    Comprehensive analysis of a receipt's structure.

    This class represents the result of analyzing a receipt's structure to identify
    distinct sections and their characteristics. Instead of using confidence scores,
    it provides detailed reasoning about the analysis process and decisions made.
    """

    sections: List[ReceiptSection]
    overall_reasoning: str
    receipt_id: Optional[int] = None
    image_id: Optional[str] = None
    version: str = "1.0.0"
    metadata: Dict = field(default_factory=dict)
    timestamp_added: Optional[str] = None
    timestamp_updated: Optional[str] = None

    def __post_init__(self):
        # Initialize timestamp if not provided
        if self.timestamp_added is None:
            self.timestamp_added = datetime.now().isoformat()

        # Sort sections by start line for consistent order
        self.sections.sort(
            key=lambda section: (
                section.start_line if section.start_line is not None else float("inf")
            )
        )

        # Initialize metadata
        self.initialize_metadata()

        # Add structure-specific metrics
        self.add_processing_metric("section_count", len(self.sections))
        section_types = set(section.name for section in self.sections)
        self.add_processing_metric("section_types", list(section_types))

        # Track patterns
        pattern_counts = {
            "spatial_patterns": sum(len(s.spatial_patterns) for s in self.sections),
            "content_patterns": sum(len(s.content_patterns) for s in self.sections),
        }
        self.add_processing_metric("pattern_counts", pattern_counts)

    @property
    def discovered_sections(self) -> List[ReceiptSection]:
        """
        Backward compatibility property for code that still uses discovered_sections.

        Returns:
            List[ReceiptSection]: The sections in this analysis
        """
        return self.sections

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

    def get_sections_with_pattern(self, pattern_type: str) -> List[ReceiptSection]:
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
            for pattern in section.content_patterns:
                if (
                    pattern_type.lower() in pattern.pattern_type.lower()
                    or pattern_type.lower() in pattern.description.lower()
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
        result = [f"Receipt contains {len(self.sections)} sections."]

        # Add overall reasoning
        if self.overall_reasoning:
            result.append(f"Overall analysis: {self.overall_reasoning}")

        # Add section summaries
        for i, section in enumerate(self.sections):
            section_summary = [
                f"Section {i+1}: {section.name} ({len(section.line_ids)} lines)"
            ]

            if section.reasoning:
                section_summary.append(f"  Reasoning: {section.reasoning}")

            if section.spatial_patterns:
                pattern_texts = [p.description for p in section.spatial_patterns]
                section_summary.append(
                    f"  Spatial patterns: {', '.join(pattern_texts[:3])}"
                )

            if section.content_patterns:
                pattern_texts = [p.description for p in section.content_patterns]
                section_summary.append(
                    f"  Content patterns: {', '.join(pattern_texts[:3])}"
                )

            result.append("\n".join(section_summary))

        return "\n\n".join(result)

    @classmethod
    def from_gpt_response(cls, response_data: Dict) -> "StructureAnalysis":
        """
        Create a StructureAnalysis instance from GPT API response data.

        Args:
            response_data (Dict): The processed response from gpt_request_structure_analysis

        Returns:
            StructureAnalysis: A new instance populated with the response data
        """
        sections = []
        # Handle both old format ("discovered_sections") and new format ("sections")
        sections_data = response_data.get(
            "sections", response_data.get("discovered_sections", [])
        )
        for section_dict in sections_data:
            sections.append(ReceiptSection.from_dict(section_dict))

        return cls(
            sections=sections,
            overall_reasoning=response_data.get("overall_reasoning", ""),
            receipt_id=response_data.get("receipt_id"),
            image_id=response_data.get("image_id"),
            version=response_data.get("version", "1.0.0"),
            metadata={
                k: v
                for k, v in response_data.items()
                if k
                not in [
                    "discovered_sections",
                    "sections",
                    "overall_reasoning",
                    "receipt_id",
                    "image_id",
                    "version",
                ]
            },
            timestamp_added=response_data.get("timestamp_added"),
        )

    @classmethod
    def from_dynamo(cls, analysis: "ReceiptStructureAnalysis") -> "StructureAnalysis":
        """
        Create a StructureAnalysis instance from a DynamoDB ReceiptStructureAnalysis entity.

        Args:
            analysis: A ReceiptStructureAnalysis object from the DynamoDB entities

        Returns:
            StructureAnalysis: A new instance populated with the data from the DynamoDB entity
        """
        # Process sections from the DynamoDB entity
        sections = []
        for section in analysis.sections:
            sections.append(
                ReceiptSection(
                    name=section.name,
                    line_ids=section.line_ids,
                    spatial_patterns=[
                        SpatialPattern(
                            pattern_type=sp.pattern_type,
                            description=sp.description,
                            metadata=sp.metadata,
                        )
                        for sp in section.spatial_patterns
                    ],
                    content_patterns=[
                        ContentPattern(
                            pattern_type=cp.pattern_type,
                            description=cp.description,
                            examples=cp.examples,
                            metadata=cp.metadata,
                        )
                        for cp in section.content_patterns
                    ],
                    reasoning=section.reasoning,
                    start_line=section.start_line,
                    end_line=section.end_line,
                    metadata=section.metadata,
                )
            )

        # Create instance but bypass normal initialization to preserve timestamps
        instance = cls.__new__(cls)

        # Set attributes manually
        instance.sections = sections
        instance.overall_reasoning = analysis.overall_reasoning
        instance.receipt_id = analysis.receipt_id
        instance.image_id = analysis.image_id
        instance.version = analysis.version
        instance.timestamp_added = (
            analysis.timestamp_added.isoformat() if analysis.timestamp_added else None
        )
        instance.timestamp_updated = (
            analysis.timestamp_updated.isoformat()
            if analysis.timestamp_updated
            else None
        )

        # Create metadata from the DynamoDB entity's fields
        instance.metadata = {
            **analysis.metadata,
            "processing_metrics": analysis.processing_metrics,
            "source_info": analysis.source_info,
            "processing_history": analysis.processing_history,
        }

        # Minimal initialization without updating timestamps
        instance._initialize_from_dynamo()

        return instance

    def to_dynamo(self) -> Optional["ReceiptStructureAnalysis"]:
        """
        Convert this StructureAnalysis to a DynamoDB ReceiptStructureAnalysis entity.

        Returns:
            ReceiptStructureAnalysis: A DynamoDB entity object

        Raises:
            ValueError: If receipt_id or image_id are not set
        """
        # Check if required fields are set
        if self.receipt_id is None:
            raise ValueError("receipt_id must be set before calling to_dynamo()")
        if self.image_id is None:
            raise ValueError("image_id must be set before calling to_dynamo()")

        # Convert sections to DynamoDB format
        dynamo_sections = []
        for section in self.sections:
            # Convert spatial patterns
            spatial_patterns = []
            for sp in section.spatial_patterns:
                if isinstance(sp, str):
                    spatial_patterns.append(
                        DynamoSpatialPattern(pattern_type="legacy", description=sp)
                    )
                else:
                    spatial_patterns.append(
                        DynamoSpatialPattern(
                            pattern_type=sp.pattern_type,
                            description=sp.description,
                            metadata=sp.metadata,
                        )
                    )

            # Convert content patterns
            content_patterns = []
            for cp in section.content_patterns:
                if isinstance(cp, str):
                    content_patterns.append(
                        DynamoContentPattern(
                            pattern_type="legacy", description=cp, examples=[]
                        )
                    )
                else:
                    content_patterns.append(
                        DynamoContentPattern(
                            pattern_type=cp.pattern_type,
                            description=cp.description,
                            examples=cp.examples,
                            metadata=cp.metadata,
                        )
                    )

            # Create DynamoDB section
            dynamo_sections.append(
                DynamoReceiptSection(
                    name=section.name,
                    line_ids=section.line_ids,
                    spatial_patterns=spatial_patterns,
                    content_patterns=content_patterns,
                    reasoning=section.reasoning,
                    start_line=section.start_line,
                    end_line=section.end_line,
                    metadata=section.metadata,
                )
            )

        # Parse timestamps if they are strings
        timestamp_added = None
        if self.timestamp_added:
            if isinstance(self.timestamp_added, str):
                from datetime import datetime

                try:
                    timestamp_added = datetime.fromisoformat(self.timestamp_added)
                except ValueError:
                    timestamp_added = datetime.now()
            else:
                timestamp_added = self.timestamp_added

        timestamp_updated = None
        if self.timestamp_updated:
            if isinstance(self.timestamp_updated, str):
                from datetime import datetime

                try:
                    timestamp_updated = datetime.fromisoformat(self.timestamp_updated)
                except ValueError:
                    timestamp_updated = datetime.now()
            else:
                timestamp_updated = self.timestamp_updated

        # Extract processing metrics and source info from metadata if present
        processing_metrics = self.metadata.get("processing_metrics", {})
        source_info = self.metadata.get("source_info", {})
        processing_history = self.metadata.get("processing_history", [])

        # Create metadata without the extracted fields to avoid duplication
        metadata = {
            k: v
            for k, v in self.metadata.items()
            if k not in ["processing_metrics", "source_info", "processing_history"]
        }

        # Create the DynamoDB ReceiptStructureAnalysis
        return ReceiptStructureAnalysis(
            receipt_id=self.receipt_id,
            image_id=self.image_id,
            sections=dynamo_sections,
            overall_reasoning=self.overall_reasoning,
            version=self.version,
            metadata=metadata,
            timestamp_added=timestamp_added,
            timestamp_updated=timestamp_updated,
            processing_metrics=processing_metrics,
            source_info=source_info,
            processing_history=processing_history,
        )

    def _initialize_from_dynamo(self):
        """
        Perform minimal initialization when creating from DynamoDB data.
        This avoids overriding timestamps that came from the database.
        """
        # Sort sections by start line for consistent order
        self.sections.sort(
            key=lambda section: (
                section.start_line if section.start_line is not None else float("inf")
            )
        )

        # Ensure basic metadata structure exists
        if "processing_metrics" not in self.metadata:
            self.metadata["processing_metrics"] = {}

        if "source_info" not in self.metadata:
            self.metadata["source_info"] = {}

        if "processing_history" not in self.metadata:
            self.metadata["processing_history"] = []
