from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class SpatialPattern:
    """
    Represents a spatial pattern detected in a receipt section.
    
    Spatial patterns include alignment, indentation, spacing, and other
    visual layout characteristics of text elements.
    """
    pattern_type: str  # e.g., "alignment", "indentation", "spacing"
    description: str
    metadata: Dict = field(default_factory=dict)


@dataclass
class ContentPattern:
    """
    Represents a content pattern detected in a receipt section.
    
    Content patterns include text formats, recurring phrases, lexical patterns,
    and other textual characteristics.
    """
    pattern_type: str  # e.g., "date_format", "price_format", "keyword"
    description: str
    examples: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


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
    
    @classmethod
    def from_dict(cls, section_dict: Dict) -> "ReceiptSection":
        """Create a ReceiptSection from a dictionary representation."""
        # Process spatial patterns
        spatial_patterns = []
        for pattern in section_dict.get("spatial_patterns", []):
            if isinstance(pattern, str):
                # Simple string pattern
                spatial_patterns.append(SpatialPattern(
                    pattern_type="generic",
                    description=pattern
                ))
            elif isinstance(pattern, dict):
                # Detailed pattern object
                spatial_patterns.append(SpatialPattern(
                    pattern_type=pattern.get("type", "generic"),
                    description=pattern.get("description", ""),
                    metadata=pattern.get("metadata", {})
                ))
        
        # Process content patterns
        content_patterns = []
        for pattern in section_dict.get("content_patterns", []):
            if isinstance(pattern, str):
                # Simple string pattern
                content_patterns.append(ContentPattern(
                    pattern_type="generic",
                    description=pattern
                ))
            elif isinstance(pattern, dict):
                # Detailed pattern object
                content_patterns.append(ContentPattern(
                    pattern_type=pattern.get("type", "generic"),
                    description=pattern.get("description", ""),
                    examples=pattern.get("examples", []),
                    metadata=pattern.get("metadata", {})
                ))
        
        # Create and return the section
        return cls(
            name=section_dict.get("name", "Unknown Section"),
            line_ids=section_dict.get("line_ids", []),
            spatial_patterns=spatial_patterns,
            content_patterns=content_patterns,
            reasoning=section_dict.get("reasoning", ""),
            metadata=section_dict.get("metadata", {})
        )


@dataclass
class StructureAnalysis:
    """
    Comprehensive analysis of a receipt's structure.
    
    This class represents the result of analyzing a receipt's structure to identify
    distinct sections and their characteristics. Instead of using confidence scores,
    it provides detailed reasoning about the analysis process and decisions made.
    """
    discovered_sections: List[ReceiptSection]
    overall_reasoning: str
    metadata: Dict = field(default_factory=dict)
    
    def __post_init__(self):
        # Sort sections by start line for consistent order
        self.discovered_sections.sort(key=lambda section: 
            section.start_line if section.start_line is not None else float('inf')
        )
    
    def get_section_by_name(self, name: str) -> Optional[ReceiptSection]:
        """Find a section by its name."""
        for section in self.discovered_sections:
            if section.name.lower() == name.lower():
                return section
        return None
    
    def get_section_for_line(self, line_id: int) -> Optional[ReceiptSection]:
        """Find which section contains the given line ID."""
        for section in self.discovered_sections:
            if line_id in section.line_ids:
                return section
        return None
    
    def get_sections_with_pattern(self, pattern_type: str) -> List[ReceiptSection]:
        """Find sections that contain a specific pattern type."""
        matching_sections = []
        for section in self.discovered_sections:
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
        result = [f"Receipt contains {len(self.discovered_sections)} sections."]
        
        # Add overall reasoning
        if self.overall_reasoning:
            result.append(f"Overall analysis: {self.overall_reasoning}")
        
        # Add section summaries
        for i, section in enumerate(self.discovered_sections):
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
        for section_dict in response_data.get("discovered_sections", []):
            sections.append(ReceiptSection.from_dict(section_dict))
        
        return cls(
            discovered_sections=sections,
            overall_reasoning=response_data.get("overall_reasoning", ""),
            metadata={k: v for k, v in response_data.items() 
                     if k not in ["discovered_sections", "overall_reasoning"]}
        ) 