from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime


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
            "metadata": self.metadata
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
            metadata=data.get("metadata", {})
        )


@dataclass
class ContentPattern:
    """
    Represents a content pattern found in receipt sections.
    
    Content patterns describe the textual characteristics of receipt sections,
    such as containing dates, prices, or specific keywords.
    """
    pattern_type: str  # e.g., "keywords", "formatting", "semantic"
    description: str  # e.g., "contains business name", "has date format", "shows prices"
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
            "metadata": self.metadata
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
            metadata=data.get("metadata", {})
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
            "spatial_patterns": [pattern.to_dict() if hasattr(pattern, 'to_dict') else pattern 
                              for pattern in self.spatial_patterns],
            "content_patterns": [pattern.to_dict() if hasattr(pattern, 'to_dict') else pattern 
                              for pattern in self.content_patterns],
            "reasoning": self.reasoning,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "metadata": self.metadata
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
                content_patterns.append(ContentPattern(pattern_type="legacy", description=pattern))
            else:
                content_patterns.append(pattern)
                
        # Convert spatial patterns from dict to SpatialPattern objects if needed
        spatial_patterns = []
        for pattern in data.get("spatial_patterns", []):
            if isinstance(pattern, dict):
                spatial_patterns.append(SpatialPattern.from_dict(pattern))
            elif isinstance(pattern, str):
                # For backward compatibility with old data format
                spatial_patterns.append(SpatialPattern(pattern_type="legacy", description=pattern))
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
            metadata=data.get("metadata", {})
        )


@dataclass
class StructureAnalysis:
    """
    Comprehensive analysis of a receipt's structure.
    
    This class represents the result of analyzing a receipt's structure to identify
    distinct sections and their characteristics. Instead of using confidence scores,
    it provides detailed reasoning about the analysis process and decisions made.
    """
    sections: List[ReceiptSection]
    overall_reasoning: str
    metadata: Dict = field(default_factory=dict)
    version: str = "1.0"
    timestamp_added: Optional[str] = None
    
    def __post_init__(self):
        # Initialize timestamp if not provided
        if self.timestamp_added is None:
            self.timestamp_added = datetime.now().isoformat()
            
        # Sort sections by start line for consistent order
        self.sections.sort(key=lambda section: 
            section.start_line if section.start_line is not None else float('inf')
        )
    
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
                if pattern_type.lower() in pattern.pattern_type.lower() or pattern_type.lower() in pattern.description.lower():
                    matching_sections.append(section)
                    break
            
            # If already matched on spatial pattern, skip content patterns
            if section in matching_sections:
                continue
                
            # Check content patterns
            for pattern in section.content_patterns:
                if pattern_type.lower() in pattern.pattern_type.lower() or pattern_type.lower() in pattern.description.lower():
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
                section_summary.append(f"  Spatial patterns: {', '.join(pattern_texts[:3])}")
            
            if section.content_patterns:
                pattern_texts = [p.description for p in section.content_patterns]
                section_summary.append(f"  Content patterns: {', '.join(pattern_texts[:3])}")
            
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
        sections_data = response_data.get("sections", response_data.get("discovered_sections", []))
        for section_dict in sections_data:
            sections.append(ReceiptSection.from_dict(section_dict))
        
        return cls(
            sections=sections,
            overall_reasoning=response_data.get("overall_reasoning", ""),
            metadata={k: v for k, v in response_data.items() 
                     if k not in ["discovered_sections", "sections", "overall_reasoning"]},
            version=response_data.get("version", "1.0"),
            timestamp_added=response_data.get("timestamp_added")
        )
        
    def to_dynamo(self) -> Dict:
        """
        Convert the StructureAnalysis to a DynamoDB-compatible dictionary.
        
        Returns:
            Dict: A dictionary representation for DynamoDB
        """
        return {
            "sections": [section.to_dict() if hasattr(section, 'to_dict') else section for section in self.sections],
            "overall_reasoning": self.overall_reasoning,
            "metadata": self.metadata,
            "version": self.version,
            "timestamp_added": self.timestamp_added
        }
        
    @classmethod
    def from_dynamo(cls, data: Dict) -> "StructureAnalysis":
        """
        Create a StructureAnalysis instance from DynamoDB data.
        
        Args:
            data (Dict): Data from DynamoDB
            
        Returns:
            StructureAnalysis: A new instance populated with the DynamoDB data
        """
        sections = []
        for section_dict in data.get("sections", []):
            sections.append(ReceiptSection.from_dict(section_dict))
            
        return cls(
            sections=sections,
            overall_reasoning=data.get("overall_reasoning", ""),
            metadata=data.get("metadata", {}),
            version=data.get("version", "1.0"),
            timestamp_added=data.get("timestamp_added")
        ) 