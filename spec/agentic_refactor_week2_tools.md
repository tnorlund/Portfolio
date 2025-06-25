# Week 2: Wrap Existing Processors as Tools

## Overview

This week focuses on refactoring existing processors (ReceiptAnalyzer, LineItemProcessor, etc.) into standardized tools with consistent interfaces. This creates the foundation for the agentic architecture while maintaining backward compatibility.

## Goals

1. Define standard tool interface using Pydantic models
2. Wrap existing processors as tools
3. Create a tool registry for dynamic discovery
4. Maintain backward compatibility with current ReceiptLabeler

## Implementation Plan

### Step 1: Define Core Data Models

```python
# receipt_label/models/context.py

from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime
from decimal import Decimal

class ReceiptContext(BaseModel):
    """Shared context passed between tools."""
    
    # Input data
    receipt_id: str
    image_id: str
    s3_url: Optional[str] = None
    
    # OCR results
    receipt: Optional[Receipt] = None
    receipt_words: List[ReceiptWord] = Field(default_factory=list)
    receipt_lines: List[ReceiptLine] = Field(default_factory=list)
    
    # Analysis results
    structure_analysis: Optional[StructureAnalysis] = None
    field_labels: Optional[LabelAnalysis] = None
    line_items: Optional[LineItemAnalysis] = None
    places_data: Optional[Dict[str, Any]] = None
    similar_receipts: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Metadata
    confidence_scores: Dict[str, float] = Field(default_factory=dict)
    execution_times: Dict[str, float] = Field(default_factory=dict)
    errors: Dict[str, str] = Field(default_factory=dict)
    history: List['ToolEvent'] = Field(default_factory=list)
    
    class Config:
        arbitrary_types_allowed = True

class ToolEvent(BaseModel):
    """Record of a tool execution."""
    tool_name: str
    timestamp: datetime
    duration_ms: float
    success: bool
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
```

### Step 2: Define Tool Base Class

```python
# receipt_label/tools/base.py

from abc import ABC, abstractmethod
from typing import Generic, TypeVar, Optional, Dict, Any
from pydantic import BaseModel
import time
import logging

InputType = TypeVar('InputType', bound=BaseModel)
OutputType = TypeVar('OutputType', bound=BaseModel)

class Tool(ABC, Generic[InputType, OutputType]):
    """Base class for all tools in the receipt labeling pipeline."""
    
    def __init__(self, name: str, config: Optional[Dict[str, Any]] = None):
        self.name = name
        self.config = config or {}
        self.logger = logging.getLogger(f"tool.{name}")
    
    @abstractmethod
    def execute(self, context: ReceiptContext) -> ReceiptContext:
        """Execute the tool and update the context."""
        pass
    
    @abstractmethod
    def validate_input(self, context: ReceiptContext) -> bool:
        """Validate that the context has required inputs."""
        pass
    
    def __call__(self, context: ReceiptContext) -> ReceiptContext:
        """Execute the tool with timing and error handling."""
        start_time = time.time()
        event = ToolEvent(
            tool_name=self.name,
            timestamp=datetime.now(),
            duration_ms=0,
            success=False
        )
        
        try:
            # Validate input
            if not self.validate_input(context):
                raise ValueError(f"Invalid input for tool {self.name}")
            
            # Execute tool
            self.logger.info(f"Executing {self.name}")
            result = self.execute(context)
            
            # Record success
            event.success = True
            event.duration_ms = (time.time() - start_time) * 1000
            context.execution_times[self.name] = event.duration_ms / 1000
            
        except Exception as e:
            # Record error
            self.logger.error(f"Error in {self.name}: {str(e)}")
            event.error = str(e)
            event.duration_ms = (time.time() - start_time) * 1000
            context.errors[self.name] = str(e)
            result = context
        
        # Add event to history
        result.history.append(event)
        return result
```

### Step 3: Wrap ReceiptAnalyzer as Tools

```python
# receipt_label/tools/structure_analysis.py

from typing import Optional
from ..processors.receipt_analyzer import ReceiptAnalyzer
from .base import Tool

class StructureAnalysisTool(Tool):
    """Analyzes receipt structure to identify sections."""
    
    def __init__(self, gpt_api_key: Optional[str] = None):
        super().__init__("structure_analysis")
        self.analyzer = ReceiptAnalyzer(api_key=gpt_api_key)
    
    def validate_input(self, context: ReceiptContext) -> bool:
        return (
            context.receipt is not None and
            len(context.receipt_lines) > 0 and
            len(context.receipt_words) > 0
        )
    
    def execute(self, context: ReceiptContext) -> ReceiptContext:
        structure = self.analyzer.analyze_structure(
            receipt=context.receipt,
            receipt_lines=context.receipt_lines,
            receipt_words=context.receipt_words,
            places_api_data=context.places_data
        )
        context.structure_analysis = structure
        return context


class FieldLabelingTool(Tool):
    """Labels receipt fields based on structure analysis."""
    
    def __init__(self, gpt_api_key: Optional[str] = None):
        super().__init__("field_labeling")
        self.analyzer = ReceiptAnalyzer(api_key=gpt_api_key)
    
    def validate_input(self, context: ReceiptContext) -> bool:
        return (
            context.receipt is not None and
            context.structure_analysis is not None and
            len(context.receipt_lines) > 0
        )
    
    def execute(self, context: ReceiptContext) -> ReceiptContext:
        labels = self.analyzer.label_fields(
            receipt=context.receipt,
            receipt_lines=context.receipt_lines,
            receipt_words=context.receipt_words,
            section_boundaries=context.structure_analysis,
            places_api_data=context.places_data
        )
        context.field_labels = labels
        return context
```

### Step 4: Wrap LineItemProcessor as Tool

```python
# receipt_label/tools/line_item_extraction.py

from ..processors.line_item_processor import LineItemProcessor
from .base import Tool

class LineItemExtractionTool(Tool):
    """Extracts and analyzes line items from receipts."""
    
    def __init__(self, gpt_api_key: Optional[str] = None):
        super().__init__("line_item_extraction")
        self.processor = LineItemProcessor(gpt_api_key=gpt_api_key)
    
    def validate_input(self, context: ReceiptContext) -> bool:
        return (
            context.receipt is not None and
            len(context.receipt_lines) > 0
        )
    
    def execute(self, context: ReceiptContext) -> ReceiptContext:
        line_items = self.processor.analyze_line_items(
            receipt=context.receipt,
            receipt_lines=context.receipt_lines,
            receipt_words=context.receipt_words,
            places_data=context.places_data,
            structure_analysis=context.structure_analysis
        )
        context.line_items = line_items
        return context
```

### Step 5: Create Places API Tool

```python
# receipt_label/tools/places_lookup.py

from ..data.places_api import BatchPlacesProcessor
from .base import Tool

class PlacesLookupTool(Tool):
    """Looks up business information using Places API."""
    
    def __init__(self, api_key: Optional[str] = None):
        super().__init__("places_lookup")
        self.processor = BatchPlacesProcessor(api_key=api_key)
    
    def validate_input(self, context: ReceiptContext) -> bool:
        return len(context.receipt_words) > 0
    
    def execute(self, context: ReceiptContext) -> ReceiptContext:
        # Extract potential business names and addresses
        text_lines = [" ".join(word.text for word in context.receipt_words)]
        
        # Process and get results
        results = self.processor.process_batch(text_lines)
        if results and results[0]:
            context.places_data = results[0]
        
        return context
```

### Step 6: Create Tool Registry

```python
# receipt_label/tools/registry.py

from typing import Dict, Type, Optional, Any
from .base import Tool
from .structure_analysis import StructureAnalysisTool, FieldLabelingTool
from .line_item_extraction import LineItemExtractionTool
from .places_lookup import PlacesLookupTool

class ToolRegistry:
    """Registry for discovering and instantiating tools."""
    
    _tools: Dict[str, Type[Tool]] = {
        'structure_analysis': StructureAnalysisTool,
        'field_labeling': FieldLabelingTool,
        'line_item_extraction': LineItemExtractionTool,
        'places_lookup': PlacesLookupTool,
    }
    
    @classmethod
    def register(cls, name: str, tool_class: Type[Tool]):
        """Register a new tool."""
        cls._tools[name] = tool_class
    
    @classmethod
    def create(cls, name: str, config: Optional[Dict[str, Any]] = None) -> Tool:
        """Create a tool instance by name."""
        if name not in cls._tools:
            raise ValueError(f"Unknown tool: {name}")
        
        tool_class = cls._tools[name]
        return tool_class(**config) if config else tool_class()
    
    @classmethod
    def list_tools(cls) -> List[str]:
        """List all available tools."""
        return list(cls._tools.keys())
```

### Step 7: Update ReceiptLabeler to Use Tools

```python
# receipt_label/core/labeler.py

class ReceiptLabeler:
    """Receipt labeler that can use either legacy or tool-based approach."""
    
    def __init__(self, ..., use_tools: bool = False):
        # ... existing init ...
        self.use_tools = use_tools
        
        if use_tools:
            # Initialize tools
            self.tools = {
                'places_lookup': PlacesLookupTool(api_key=places_api_key),
                'structure_analysis': StructureAnalysisTool(gpt_api_key=gpt_api_key),
                'field_labeling': FieldLabelingTool(gpt_api_key=gpt_api_key),
                'line_item_extraction': LineItemExtractionTool(gpt_api_key=gpt_api_key),
            }
    
    def label_receipt(self, ...):
        if self.use_tools:
            return self._label_receipt_with_tools(...)
        else:
            return self._label_receipt_legacy(...)
    
    def _label_receipt_with_tools(
        self,
        receipt: Receipt,
        receipt_words: List[ReceiptWord],
        receipt_lines: List[ReceiptLine],
        **kwargs
    ) -> LabelingResult:
        """Label receipt using the new tool-based approach."""
        
        # Create context
        context = ReceiptContext(
            receipt_id=str(receipt.receipt_id),
            image_id=receipt.image_id,
            receipt=receipt,
            receipt_words=receipt_words,
            receipt_lines=receipt_lines
        )
        
        # Execute tools in sequence
        tool_sequence = [
            'places_lookup',
            'structure_analysis',
            'field_labeling',
            'line_item_extraction'
        ]
        
        for tool_name in tool_sequence:
            if tool_name in self.tools:
                context = self.tools[tool_name](context)
        
        # Convert to LabelingResult
        return self._context_to_result(context)
```

## Testing Strategy

### Unit Tests for Each Tool

```python
# test_structure_analysis_tool.py

def test_structure_analysis_tool():
    """Test structure analysis tool."""
    tool = StructureAnalysisTool(gpt_api_key="test-key")
    
    # Create test context
    context = ReceiptContext(
        receipt_id="test-123",
        image_id="img-123",
        receipt=test_receipt,
        receipt_words=test_words,
        receipt_lines=test_lines
    )
    
    # Execute tool
    result = tool(context)
    
    # Verify results
    assert result.structure_analysis is not None
    assert len(result.history) == 1
    assert result.history[0].tool_name == "structure_analysis"
    assert result.history[0].success
```

### Integration Tests

```python
def test_tool_pipeline():
    """Test full pipeline with all tools."""
    context = ReceiptContext(
        receipt_id="test-123",
        image_id="img-123",
        receipt=test_receipt,
        receipt_words=test_words,
        receipt_lines=test_lines
    )
    
    # Execute tools in sequence
    tools = [
        PlacesLookupTool(),
        StructureAnalysisTool(),
        FieldLabelingTool(),
        LineItemExtractionTool()
    ]
    
    for tool in tools:
        context = tool(context)
    
    # Verify all results present
    assert context.places_data is not None
    assert context.structure_analysis is not None
    assert context.field_labels is not None
    assert context.line_items is not None
    assert len(context.history) == 4
```

## Migration Strategy

1. **Phase 1**: Deploy with `use_tools=False` (default)
2. **Phase 2**: Enable for 10% of traffic with feature flag
3. **Phase 3**: Compare results between legacy and tool approaches
4. **Phase 4**: Gradually increase to 100% if metrics are good
5. **Phase 5**: Remove legacy code after 2 weeks of stability

## Success Criteria

- All tools have >95% test coverage
- Tool-based approach produces identical results to legacy
- No performance regression (±10% latency)
- Clean separation of concerns verified by dependency analysis

## Next Steps

- Week 3: Implement TriageAgent to orchestrate tools dynamically
- Week 4: Add streaming and observability
- Future: Create custom tools for specific merchants or receipt types