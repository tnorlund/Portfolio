# Phase 3: LangGraph Development Strategy

## Overview

This document outlines a practical, incremental approach to developing the Phase 3 receipt labeling system using LangGraph. We'll build from simple to complex, testing at each stage.

## Development Principles

1. **Start Simple**: Basic linear flow first, add complexity incrementally
2. **Test Early**: Validate each node before connecting them
3. **Mock External Services**: Use stubs for GPT/Pinecone during development
4. **Visualize Often**: Use LangGraph's built-in visualization
5. **State-First Design**: Define your state schema before building nodes

## Phase 1: Foundation (Days 1-3)

### Step 1.1: Environment Setup

```bash
# Create development environment
cd receipt_label
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install langgraph langchain langchain-openai pydantic typing-extensions
pip install pytest pytest-asyncio pytest-mock  # For testing
```

### Step 1.2: Define State Schema

```python
# receipt_label/langgraph_integration/state.py
from typing import TypedDict, List, Dict, Optional, Any
from datetime import datetime
from receipt_dynamo.entities.receipt_word import ReceiptWord

class LabelInfo(TypedDict):
    label_type: str
    confidence: float
    source: str  # "pattern", "gpt", "manual"
    validation_status: str  # "pending", "validated", "failed"

class ValidationResult(TypedDict):
    passed: bool
    errors: List[str]
    warnings: List[str]

class ProcessingMetrics(TypedDict):
    pattern_detection_ms: float
    gpt_labeling_ms: float
    validation_ms: float
    total_ms: float
    tokens_used: int
    cost_usd: float

class ReceiptProcessingState(TypedDict):
    # Input data
    receipt_id: str
    receipt_words: List[ReceiptWord]
    merchant_metadata: Optional[Dict[str, Any]]
    
    # Phase 2 results
    pattern_results: Dict[str, Any]
    currency_columns: List[Dict]
    math_solutions: List[Dict]
    
    # Labeling progress
    labels: Dict[int, LabelInfo]  # word_id -> label
    missing_essentials: List[str]
    
    # GPT context (if needed)
    needs_gpt: bool
    gpt_prompt: Optional[str]
    gpt_response: Optional[Dict]
    
    # Validation
    validation_results: Dict[str, ValidationResult]
    needs_review: bool
    
    # Metrics
    metrics: ProcessingMetrics
    
    # Control flow
    retry_count: int
    error_messages: List[str]
```

### Step 1.3: Create Minimal Linear Workflow

```python
# receipt_label/langgraph_integration/workflow_v1.py
from langgraph.graph import StateGraph, END
from .state import ReceiptProcessingState
import logging

logger = logging.getLogger(__name__)

async def load_merchant_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Load merchant data from DynamoDB"""
    logger.info(f"Loading merchant data for receipt {state['receipt_id']}")
    
    # TODO: Implement actual DynamoDB lookup
    # For now, return mock data
    state["merchant_metadata"] = {
        "name": "Walmart",
        "patterns": ["TC#", "ST#"],
        "common_products": ["Great Value"]
    }
    return state

async def pattern_labeling_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Apply pattern-based labels"""
    logger.info("Applying pattern-based labels")
    
    # Initialize labels dict
    state["labels"] = {}
    
    # TODO: Implement actual pattern application
    # For now, mock some labels
    if state["pattern_results"].get("merchant_patterns"):
        state["labels"][0] = {
            "label_type": "MERCHANT_NAME",
            "confidence": 0.95,
            "source": "pattern",
            "validation_status": "pending"
        }
    
    return state

async def validation_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Validate labels"""
    logger.info("Validating labels")
    
    # TODO: Implement actual validation
    state["validation_results"] = {
        "essential_fields": {
            "passed": True,
            "errors": [],
            "warnings": []
        }
    }
    
    state["needs_review"] = False
    return state

def create_simple_workflow():
    """Create a minimal linear workflow for testing"""
    workflow = StateGraph(ReceiptProcessingState)
    
    # Add nodes
    workflow.add_node("load_merchant", load_merchant_node)
    workflow.add_node("pattern_labeling", pattern_labeling_node)
    workflow.add_node("validation", validation_node)
    
    # Define flow
    workflow.set_entry_point("load_merchant")
    workflow.add_edge("load_merchant", "pattern_labeling")
    workflow.add_edge("pattern_labeling", "validation")
    workflow.add_edge("validation", END)
    
    return workflow.compile()
```

### Step 1.4: Test the Minimal Workflow

```python
# tests/langgraph_integration/test_workflow_v1.py
import pytest
from receipt_label.langgraph_integration.workflow_v1 import create_simple_workflow
from receipt_label.langgraph_integration.state import ReceiptProcessingState

@pytest.mark.asyncio
async def test_minimal_workflow():
    """Test basic workflow execution"""
    # Create workflow
    workflow = create_simple_workflow()
    
    # Create test state
    initial_state: ReceiptProcessingState = {
        "receipt_id": "TEST_001",
        "receipt_words": [],  # Mock receipt words
        "pattern_results": {
            "merchant_patterns": [{"text": "Walmart", "confidence": 0.9}]
        },
        "labels": {},
        "metrics": {}
    }
    
    # Run workflow
    final_state = await workflow.ainvoke(initial_state)
    
    # Assertions
    assert final_state["merchant_metadata"]["name"] == "Walmart"
    assert 0 in final_state["labels"]  # Merchant label added
    assert final_state["validation_results"]["essential_fields"]["passed"]
```

## Phase 2: Add Conditional Logic (Days 4-6)

### Step 2.1: Add Gap Detection and Branching

```python
# receipt_label/langgraph_integration/workflow_v2.py
from langgraph.graph import StateGraph, END
from .state import ReceiptProcessingState

ESSENTIAL_FIELDS = ["MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL"]

async def gap_detection_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Identify missing essential fields"""
    found_types = {label["label_type"] for label in state["labels"].values()}
    missing = [field for field in ESSENTIAL_FIELDS if field not in found_types]
    
    state["missing_essentials"] = missing
    state["needs_gpt"] = len(missing) > 0
    
    logger.info(f"Missing essentials: {missing}")
    return state

def routing_function(state: ReceiptProcessingState) -> str:
    """Decide next node based on gap analysis"""
    if state["needs_gpt"]:
        return "gpt_context"
    else:
        return "extended_labeling"

async def gpt_context_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Build context for GPT"""
    # TODO: Implement spatial context building
    state["gpt_prompt"] = f"Find these fields: {state['missing_essentials']}"
    return state

async def gpt_labeling_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Call GPT for missing labels"""
    # TODO: Implement actual GPT call
    # For now, mock the response
    state["gpt_response"] = {"labels": {}}
    return state

async def extended_labeling_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Find tier 2-4 labels when essentials are present"""
    # TODO: Implement line item detection, etc.
    return state

def create_conditional_workflow():
    """Workflow with conditional branching"""
    workflow = StateGraph(ReceiptProcessingState)
    
    # Add all nodes
    workflow.add_node("load_merchant", load_merchant_node)
    workflow.add_node("pattern_labeling", pattern_labeling_node)
    workflow.add_node("gap_detection", gap_detection_node)
    workflow.add_node("gpt_context", gpt_context_node)
    workflow.add_node("gpt_labeling", gpt_labeling_node)
    workflow.add_node("extended_labeling", extended_labeling_node)
    workflow.add_node("validation", validation_node)
    
    # Define flow with conditional
    workflow.set_entry_point("load_merchant")
    workflow.add_edge("load_merchant", "pattern_labeling")
    workflow.add_edge("pattern_labeling", "gap_detection")
    
    # Conditional branching
    workflow.add_conditional_edges(
        "gap_detection",
        routing_function,
        {
            "gpt_context": "gpt_context",
            "extended_labeling": "extended_labeling"
        }
    )
    
    # GPT path
    workflow.add_edge("gpt_context", "gpt_labeling")
    workflow.add_edge("gpt_labeling", "validation")
    
    # Extended path
    workflow.add_edge("extended_labeling", "validation")
    
    # End
    workflow.add_edge("validation", END)
    
    return workflow.compile()
```

### Step 2.2: Visualize the Workflow

```python
# scripts/visualize_workflow.py
from receipt_label.langgraph_integration.workflow_v2 import create_conditional_workflow
import matplotlib.pyplot as plt

def visualize_workflow():
    """Generate workflow visualization"""
    workflow = create_conditional_workflow()
    
    # Get mermaid diagram
    mermaid_diagram = workflow.get_graph().draw_mermaid()
    print("Mermaid Diagram:")
    print(mermaid_diagram)
    
    # Save as PNG if possible
    try:
        workflow.get_graph().draw_mermaid_png(output_file_path="workflow_diagram.png")
        print("Saved workflow diagram to workflow_diagram.png")
    except Exception as e:
        print(f"Could not save PNG: {e}")

if __name__ == "__main__":
    visualize_workflow()
```

## Phase 3: Implement Core Nodes (Days 7-10)

### Step 3.1: Pattern Labeling with Real Logic

```python
# receipt_label/langgraph_integration/nodes/pattern_labeling.py
from typing import Dict, List
from ..state import ReceiptProcessingState, LabelInfo

async def pattern_labeling_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Apply pattern-based labels from Phase 2 results"""
    
    labels: Dict[int, LabelInfo] = {}
    pattern_results = state["pattern_results"]
    
    # Apply date patterns
    if "date_patterns" in pattern_results:
        for match in pattern_results["date_patterns"]:
            word = match["word"]
            labels[word.word_id] = {
                "label_type": "DATE",
                "confidence": match["confidence"],
                "source": "pattern",
                "validation_status": "pending"
            }
    
    # Apply merchant patterns
    if "merchant_patterns" in pattern_results:
        for match in pattern_results["merchant_patterns"]:
            word = match["word"]
            labels[word.word_id] = {
                "label_type": "MERCHANT_NAME",
                "confidence": match["confidence"],
                "source": "pattern",
                "validation_status": "pending"
            }
    
    # Apply currency patterns with position heuristics
    if "currency_columns" in pattern_results:
        labels.update(
            apply_currency_labels(
                pattern_results["currency_columns"],
                state["receipt_words"]
            )
        )
    
    state["labels"] = labels
    logger.info(f"Applied {len(labels)} pattern-based labels")
    
    return state

def apply_currency_labels(
    currency_columns: List[Dict], 
    receipt_words: List
) -> Dict[int, LabelInfo]:
    """Apply labels to currency values based on position"""
    labels = {}
    
    # Find the largest value at the bottom (likely total)
    all_prices = []
    for column in currency_columns:
        all_prices.extend(column["prices"])
    
    if all_prices:
        # Sort by value
        largest_price = max(all_prices, key=lambda p: p["value"])
        
        # Check if it's at the bottom
        max_y = max(w.y for w in receipt_words)
        if largest_price["word"].y > 0.8 * max_y:
            labels[largest_price["word"].word_id] = {
                "label_type": "GRAND_TOTAL",
                "confidence": 0.8,
                "source": "position_heuristic",
                "validation_status": "pending"
            }
    
    return labels
```

### Step 3.2: GPT Integration with Mocking

```python
# receipt_label/langgraph_integration/nodes/gpt_labeling.py
import os
from typing import Dict, Optional
from ..state import ReceiptProcessingState

# Allow mocking for development
USE_MOCK_GPT = os.getenv("USE_MOCK_GPT", "true").lower() == "true"

async def gpt_labeling_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Call GPT to fill missing labels"""
    
    prompt = state["gpt_prompt"]
    
    if USE_MOCK_GPT:
        # Mock response for development
        response = create_mock_gpt_response(state)
    else:
        # Real GPT call
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)
        response = await llm.ainvoke(prompt)
        response = parse_gpt_response(response.content)
    
    # Update labels with GPT results
    state["gpt_response"] = response
    
    for word_id, label_info in response.get("labels", {}).items():
        state["labels"][int(word_id)] = {
            "label_type": label_info["type"],
            "confidence": label_info.get("confidence", 0.7),
            "source": "gpt",
            "validation_status": "pending"
        }
    
    # Track metrics
    state["metrics"]["tokens_used"] = response.get("tokens", 0)
    state["metrics"]["gpt_labeling_ms"] = response.get("latency", 0)
    
    return state

def create_mock_gpt_response(state: ReceiptProcessingState) -> Dict:
    """Create realistic mock response for testing"""
    mock_labels = {}
    
    # Fill in missing essentials with mock data
    for field in state["missing_essentials"]:
        if field == "DATE":
            mock_labels[1] = {"type": "DATE", "confidence": 0.85}
        elif field == "GRAND_TOTAL":
            mock_labels[10] = {"type": "GRAND_TOTAL", "confidence": 0.9}
    
    return {
        "labels": mock_labels,
        "tokens": 150,
        "latency": 500
    }
```

## Phase 4: Testing Strategy (Days 11-13)

### Step 4.1: Unit Test Each Node

```python
# tests/langgraph_integration/nodes/test_pattern_labeling.py
import pytest
from receipt_label.langgraph_integration.nodes.pattern_labeling import pattern_labeling_node
from receipt_label.langgraph_integration.state import ReceiptProcessingState

@pytest.mark.asyncio
async def test_pattern_labeling_finds_merchant():
    """Test that merchant patterns are applied"""
    state: ReceiptProcessingState = {
        "pattern_results": {
            "merchant_patterns": [
                {"word": MockWord(word_id=1, text="Walmart"), "confidence": 0.9}
            ]
        },
        "labels": {}
    }
    
    result = await pattern_labeling_node(state)
    
    assert 1 in result["labels"]
    assert result["labels"][1]["label_type"] == "MERCHANT_NAME"
    assert result["labels"][1]["confidence"] == 0.9
```

### Step 4.2: Integration Test Full Workflows

```python
# tests/langgraph_integration/test_end_to_end.py
import pytest
from receipt_label.langgraph_integration.workflow import create_full_workflow

@pytest.mark.asyncio
async def test_pattern_only_path():
    """Test workflow when patterns find everything"""
    workflow = create_full_workflow()
    
    # State with all essentials found by patterns
    state = create_test_state_with_all_essentials()
    
    final_state = await workflow.ainvoke(state)
    
    # Should not have called GPT
    assert not final_state["needs_gpt"]
    assert final_state["metrics"].get("tokens_used", 0) == 0
    
    # Should have extended labels
    assert any(
        label["label_type"] == "PRODUCT_NAME" 
        for label in final_state["labels"].values()
    )

@pytest.mark.asyncio
async def test_gpt_fallback_path():
    """Test workflow when patterns miss essentials"""
    workflow = create_full_workflow()
    
    # State missing GRAND_TOTAL
    state = create_test_state_missing_total()
    
    final_state = await workflow.ainvoke(state)
    
    # Should have called GPT
    assert final_state["needs_gpt"]
    assert final_state["metrics"]["tokens_used"] > 0
    
    # Should have found the total
    assert any(
        label["label_type"] == "GRAND_TOTAL" 
        for label in final_state["labels"].values()
    )
```

### Step 4.3: Performance Testing

```python
# tests/langgraph_integration/test_performance.py
import time
import asyncio

async def test_workflow_performance():
    """Ensure workflow completes within Lambda timeout"""
    workflow = create_full_workflow()
    
    # Test 10 receipts
    receipts = load_test_receipts(10)
    
    times = []
    for receipt in receipts:
        start = time.time()
        await workflow.ainvoke(receipt)
        elapsed = time.time() - start
        times.append(elapsed)
    
    # All should complete under 30s (Lambda timeout)
    assert all(t < 30 for t in times)
    
    # Average should be under 3s
    assert sum(times) / len(times) < 3
```

## Phase 5: Production Readiness (Days 14-16)

### Step 5.1: Add Monitoring and Error Handling

```python
# receipt_label/langgraph_integration/nodes/monitoring.py
from ..state import ReceiptProcessingState
import logging
import time

logger = logging.getLogger(__name__)

def timed(node_name: str):
    """Decorator to time node execution"""
    def decorator(func):
        async def wrapper(state: ReceiptProcessingState) -> ReceiptProcessingState:
            start = time.time()
            try:
                result = await func(state)
                elapsed = (time.time() - start) * 1000
                
                # Update metrics
                if "metrics" not in result:
                    result["metrics"] = {}
                result["metrics"][f"{node_name}_ms"] = elapsed
                
                logger.info(f"{node_name} completed in {elapsed:.2f}ms")
                return result
                
            except Exception as e:
                logger.error(f"{node_name} failed: {str(e)}")
                if "error_messages" not in state:
                    state["error_messages"] = []
                state["error_messages"].append(f"{node_name}: {str(e)}")
                
                # Mark for review
                state["needs_review"] = True
                raise
                
        return wrapper
    return decorator

# Apply to all nodes
pattern_labeling_node = timed("pattern_labeling")(pattern_labeling_node)
gpt_labeling_node = timed("gpt_labeling")(gpt_labeling_node)
validation_node = timed("validation")(validation_node)
```

### Step 5.2: Create Lambda Handler

```python
# receipt_label/lambda_handler.py
import json
import asyncio
from typing import Dict, Any
from .langgraph_integration.workflow import create_full_workflow
from .langgraph_integration.state import ReceiptProcessingState

# Initialize workflow once for Lambda reuse
workflow = create_full_workflow()

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda handler for receipt processing"""
    
    try:
        # Parse input
        receipt_id = event["receipt_id"]
        receipt_words = event["receipt_words"]
        pattern_results = event.get("pattern_results", {})
        
        # Create initial state
        initial_state: ReceiptProcessingState = {
            "receipt_id": receipt_id,
            "receipt_words": receipt_words,
            "pattern_results": pattern_results,
            "labels": {},
            "metrics": {},
            "retry_count": 0,
            "error_messages": []
        }
        
        # Run workflow
        final_state = asyncio.run(workflow.ainvoke(initial_state))
        
        # Return results
        return {
            "statusCode": 200,
            "body": json.dumps({
                "receipt_id": receipt_id,
                "labels": final_state["labels"],
                "needs_review": final_state.get("needs_review", False),
                "metrics": final_state["metrics"]
            })
        }
        
    except Exception as e:
        logger.error(f"Lambda handler error: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": str(e),
                "receipt_id": event.get("receipt_id", "unknown")
            })
        }
```

## Development Best Practices

### 1. State Design First
- Define your complete state schema before building nodes
- Include all data that needs to flow between nodes
- Add fields for metrics and error handling

### 2. Node Development Pattern
```python
async def my_node(state: StateType) -> StateType:
    """One responsibility per node"""
    # 1. Validate inputs
    if not state.get("required_field"):
        raise ValueError("Missing required field")
    
    # 2. Do the work
    result = await do_something(state["input"])
    
    # 3. Update state
    state["output"] = result
    
    # 4. Return modified state
    return state
```

### 3. Testing Pyramid
- **Unit tests**: Each node in isolation
- **Integration tests**: Common paths through workflow
- **End-to-end tests**: Full workflows with real data
- **Performance tests**: Ensure Lambda constraints

### 4. Debugging Tools
```python
# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Add state inspection
async def debug_node(state: StateType) -> StateType:
    """Print state for debugging"""
    print(f"=== State at {datetime.now()} ===")
    print(json.dumps(state, indent=2, default=str))
    return state

# Insert between nodes
workflow.add_node("debug", debug_node)
```

### 5. Gradual Rollout
```python
# Use feature flags
if os.getenv("USE_LANGGRAPH", "false").lower() == "true":
    return await langgraph_workflow.ainvoke(state)
else:
    return legacy_processing(state)
```

## Common Pitfalls and Solutions

### 1. State Mutation Issues
**Problem**: Modifying nested objects doesn't trigger updates
```python
# Wrong
state["labels"][word_id] = new_label  # Might not propagate

# Right
labels = state["labels"].copy()
labels[word_id] = new_label
state["labels"] = labels
```

### 2. Async Confusion
**Problem**: Mixing sync and async code
```python
# Use async throughout
async def all_nodes_must_be_async(state):
    return state
```

### 3. Memory Leaks
**Problem**: State accumulating data over time
```python
# Clear unnecessary data
state["temp_data"] = None  # Clear after use
```

### 4. Error Propagation
**Problem**: One node failure crashes workflow
```python
# Add try-except in critical nodes
try:
    result = await risky_operation()
except Exception as e:
    state["fallback_result"] = default_value
    state["errors"].append(str(e))
```

## Deployment Checklist

- [ ] All nodes have error handling
- [ ] Metrics tracking implemented
- [ ] Mock flags disabled for production
- [ ] Lambda handler tested
- [ ] Monitoring dashboards ready
- [ ] Cost tracking enabled
- [ ] Performance within limits
- [ ] Rollback plan documented

## Summary

This development strategy provides a methodical approach to building the LangGraph workflow:

1. **Start simple** with a linear flow
2. **Add complexity** incrementally
3. **Test thoroughly** at each stage
4. **Monitor everything** in production
5. **Iterate based on data**

The key is to build confidence at each stage before moving to the next. By the end, you'll have a robust, production-ready agentic workflow for receipt labeling.