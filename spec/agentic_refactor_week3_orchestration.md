# Week 3: Implement TriageAgent and Basic Orchestration

## Overview

This week introduces intelligent orchestration through a TriageAgent that dynamically decides which tools to use based on receipt characteristics. This moves us from a static pipeline to an adaptive, context-aware system.

## Goals

1. Implement TriageAgent for dynamic tool selection
2. Create rule-based and ML-based routing strategies
3. Add confidence-based decision making
4. Enable parallel tool execution where possible

## Implementation Plan

### Step 1: Define Orchestration Interfaces

```python
# receipt_label/agents/base.py

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field
from enum import Enum

class ToolDecision(BaseModel):
    """Decision to execute a specific tool."""
    tool_name: str
    priority: int = 0  # Lower = higher priority
    required_tools: List[str] = Field(default_factory=list)  # Prerequisites
    parallel_group: Optional[str] = None  # Tools in same group can run parallel
    config_overrides: Dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""

class RouteStrategy(Enum):
    """Routing strategy for tool selection."""
    RULE_BASED = "rule_based"
    ML_BASED = "ml_based"
    HYBRID = "hybrid"

class Agent(ABC):
    """Base class for all agents."""
    
    @abstractmethod
    def decide(self, context: ReceiptContext) -> List[ToolDecision]:
        """Decide which tools to execute based on context."""
        pass
```

### Step 2: Implement Rule-Based TriageAgent

```python
# receipt_label/agents/triage.py

from typing import List, Optional
import logging
from .base import Agent, ToolDecision, RouteStrategy

class TriageAgent(Agent):
    """Decides which tools to apply based on receipt characteristics."""
    
    def __init__(
        self, 
        strategy: RouteStrategy = RouteStrategy.RULE_BASED,
        confidence_threshold: float = 0.8
    ):
        self.strategy = strategy
        self.confidence_threshold = confidence_threshold
        self.logger = logging.getLogger("agent.triage")
    
    def decide(self, context: ReceiptContext) -> List[ToolDecision]:
        """Decide which tools to execute."""
        if self.strategy == RouteStrategy.RULE_BASED:
            return self._rule_based_routing(context)
        elif self.strategy == RouteStrategy.ML_BASED:
            return self._ml_based_routing(context)
        else:  # HYBRID
            return self._hybrid_routing(context)
    
    def _rule_based_routing(self, context: ReceiptContext) -> List[ToolDecision]:
        """Apply rules to determine tool sequence."""
        decisions = []
        
        # Always start with OCR validation if not done
        if not context.receipt_words:
            decisions.append(ToolDecision(
                tool_name="ocr_extraction",
                priority=0,
                reasoning="No OCR data present, must extract text first"
            ))
        
        # Places API lookup for merchant identification
        if self._should_lookup_places(context):
            decisions.append(ToolDecision(
                tool_name="places_lookup",
                priority=1,
                parallel_group="data_enrichment",
                reasoning="Looking up merchant information for better context"
            ))
        
        # Retrieval for similar receipts
        if self._should_retrieve_similar(context):
            decisions.append(ToolDecision(
                tool_name="similar_receipt_retrieval",
                priority=1,
                parallel_group="data_enrichment",
                reasoning="Retrieving similar receipts for improved accuracy"
            ))
        
        # Structure analysis is always needed
        decisions.append(ToolDecision(
            tool_name="structure_analysis",
            priority=2,
            required_tools=["ocr_extraction"],
            reasoning="Analyzing receipt structure to identify sections"
        ))
        
        # Field labeling depends on structure
        decisions.append(ToolDecision(
            tool_name="field_labeling",
            priority=3,
            required_tools=["structure_analysis"],
            reasoning="Labeling fields based on structure analysis"
        ))
        
        # Line items extraction
        if self._should_extract_line_items(context):
            decisions.append(ToolDecision(
                tool_name="line_item_extraction",
                priority=4,
                required_tools=["structure_analysis"],
                parallel_group="extraction",
                reasoning="Receipt appears to have line items"
            ))
        
        # Validation for low confidence
        if self._needs_validation(context):
            decisions.append(ToolDecision(
                tool_name="gpt_validation",
                priority=5,
                required_tools=["field_labeling"],
                reasoning="Low confidence labels need validation"
            ))
        
        return decisions
    
    def _should_lookup_places(self, context: ReceiptContext) -> bool:
        """Determine if Places API lookup is needed."""
        # Skip if already have merchant info
        if context.places_data and context.places_data.get('name'):
            return False
        
        # Look for merchant indicators
        merchant_keywords = ['store', 'restaurant', 'market', 'shop', 'cafe']
        text = " ".join([w.text.lower() for w in context.receipt_words[:50]])
        return any(keyword in text for keyword in merchant_keywords)
    
    def _should_retrieve_similar(self, context: ReceiptContext) -> bool:
        """Determine if similar receipt retrieval would help."""
        # Always retrieve if we have a merchant guess
        if context.places_data and context.places_data.get('name'):
            return True
        
        # Retrieve for receipts with standard formats
        indicators = ['subtotal', 'tax', 'total', 'items']
        text = " ".join([w.text.lower() for w in context.receipt_words])
        matches = sum(1 for ind in indicators if ind in text)
        return matches >= 2
    
    def _should_extract_line_items(self, context: ReceiptContext) -> bool:
        """Determine if line item extraction is needed."""
        # Look for line item indicators
        indicators = ['qty', 'quantity', '@', 'x', 'item', 'product']
        text = " ".join([w.text.lower() for w in context.receipt_words])
        return any(ind in text for ind in indicators)
    
    def _needs_validation(self, context: ReceiptContext) -> bool:
        """Check if validation pass is needed."""
        if not context.confidence_scores:
            return True
        
        avg_confidence = sum(context.confidence_scores.values()) / len(context.confidence_scores)
        return avg_confidence < self.confidence_threshold
```

### Step 3: Implement ML-Based Routing

```python
# receipt_label/agents/ml_triage.py

import numpy as np
from typing import List, Dict
import pickle

class MLRouter:
    """ML-based tool routing using receipt features."""
    
    def __init__(self, model_path: Optional[str] = None):
        self.model = self._load_model(model_path) if model_path else None
        self.feature_extractor = ReceiptFeatureExtractor()
    
    def predict_tools(self, context: ReceiptContext) -> List[ToolDecision]:
        """Predict which tools to use based on receipt features."""
        if not self.model:
            return []  # Fallback to rules
        
        # Extract features
        features = self.feature_extractor.extract(context)
        
        # Predict tool probabilities
        tool_probs = self.model.predict_proba([features])[0]
        
        # Convert to decisions
        decisions = []
        tool_names = self.model.classes_
        
        for tool_name, prob in zip(tool_names, tool_probs):
            if prob > 0.5:  # Threshold for inclusion
                decisions.append(ToolDecision(
                    tool_name=tool_name,
                    priority=int((1 - prob) * 10),  # Higher prob = lower priority
                    reasoning=f"ML model confidence: {prob:.2f}"
                ))
        
        return decisions

class ReceiptFeatureExtractor:
    """Extract features from receipt for ML routing."""
    
    def extract(self, context: ReceiptContext) -> np.ndarray:
        """Extract numerical features from receipt context."""
        features = []
        
        # Text statistics
        word_count = len(context.receipt_words)
        line_count = len(context.receipt_lines)
        avg_words_per_line = word_count / max(line_count, 1)
        
        features.extend([word_count, line_count, avg_words_per_line])
        
        # Content indicators
        text = " ".join([w.text.lower() for w in context.receipt_words])
        
        # Check for various receipt elements
        has_total = 'total' in text
        has_tax = 'tax' in text
        has_items = any(x in text for x in ['item', 'product', 'qty'])
        has_date = any(x in text for x in ['date', '/', '-'])
        has_merchant = any(x in text for x in ['store', 'restaurant', 'market'])
        
        features.extend([
            float(has_total),
            float(has_tax),
            float(has_items),
            float(has_date),
            float(has_merchant)
        ])
        
        # Layout features
        if context.receipt_words:
            # Bounding box statistics
            widths = [w.bounding_box.get('width', 0) for w in context.receipt_words]
            heights = [w.bounding_box.get('height', 0) for w in context.receipt_words]
            
            features.extend([
                np.mean(widths),
                np.std(widths),
                np.mean(heights),
                np.std(heights)
            ])
        else:
            features.extend([0, 0, 0, 0])
        
        return np.array(features)
```

### Step 4: Create Orchestrator

```python
# receipt_label/orchestration/orchestrator.py

from typing import List, Dict, Optional, Set
import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging

class ToolOrchestrator:
    """Orchestrates tool execution based on agent decisions."""
    
    def __init__(
        self, 
        tool_registry: ToolRegistry,
        max_parallel: int = 3,
        timeout_seconds: int = 30
    ):
        self.tool_registry = tool_registry
        self.max_parallel = max_parallel
        self.timeout_seconds = timeout_seconds
        self.logger = logging.getLogger("orchestrator")
    
    async def execute_plan(
        self, 
        decisions: List[ToolDecision], 
        context: ReceiptContext
    ) -> ReceiptContext:
        """Execute tools according to the plan."""
        # Build dependency graph
        graph = self._build_dependency_graph(decisions)
        
        # Execute in topological order with parallelism
        executed: Set[str] = set()
        
        while len(executed) < len(decisions):
            # Find tools that can be executed now
            ready = self._find_ready_tools(decisions, executed, graph)
            
            if not ready:
                self.logger.error("Circular dependency detected!")
                break
            
            # Group by parallel_group
            groups = self._group_by_parallel(ready)
            
            # Execute each group
            for group in groups.values():
                if len(group) == 1:
                    # Single tool, execute directly
                    context = await self._execute_tool(group[0], context)
                    executed.add(group[0].tool_name)
                else:
                    # Multiple tools, execute in parallel
                    contexts = await self._execute_parallel(group, context)
                    context = self._merge_contexts(contexts)
                    executed.update(d.tool_name for d in group)
        
        return context
    
    def _build_dependency_graph(
        self, 
        decisions: List[ToolDecision]
    ) -> Dict[str, Set[str]]:
        """Build dependency graph from decisions."""
        graph = {}
        for decision in decisions:
            graph[decision.tool_name] = set(decision.required_tools)
        return graph
    
    def _find_ready_tools(
        self,
        decisions: List[ToolDecision],
        executed: Set[str],
        graph: Dict[str, Set[str]]
    ) -> List[ToolDecision]:
        """Find tools whose dependencies are satisfied."""
        ready = []
        for decision in decisions:
            if decision.tool_name in executed:
                continue
            
            deps = graph.get(decision.tool_name, set())
            if deps.issubset(executed):
                ready.append(decision)
        
        # Sort by priority
        return sorted(ready, key=lambda d: d.priority)
    
    def _group_by_parallel(
        self, 
        decisions: List[ToolDecision]
    ) -> Dict[str, List[ToolDecision]]:
        """Group decisions by parallel_group."""
        groups = {}
        for decision in decisions:
            group = decision.parallel_group or decision.tool_name
            if group not in groups:
                groups[group] = []
            groups[group].append(decision)
        return groups
    
    async def _execute_tool(
        self, 
        decision: ToolDecision, 
        context: ReceiptContext
    ) -> ReceiptContext:
        """Execute a single tool."""
        self.logger.info(f"Executing {decision.tool_name}: {decision.reasoning}")
        
        try:
            # Create tool with config overrides
            tool = self.tool_registry.create(
                decision.tool_name, 
                decision.config_overrides
            )
            
            # Execute with timeout
            return await asyncio.wait_for(
                asyncio.to_thread(tool, context),
                timeout=self.timeout_seconds
            )
            
        except asyncio.TimeoutError:
            self.logger.error(f"Tool {decision.tool_name} timed out")
            context.errors[decision.tool_name] = "Timeout"
            return context
        except Exception as e:
            self.logger.error(f"Tool {decision.tool_name} failed: {str(e)}")
            context.errors[decision.tool_name] = str(e)
            return context
    
    async def _execute_parallel(
        self,
        decisions: List[ToolDecision],
        context: ReceiptContext
    ) -> List[ReceiptContext]:
        """Execute multiple tools in parallel."""
        tasks = [
            self._execute_tool(decision, context.copy(deep=True))
            for decision in decisions[:self.max_parallel]
        ]
        
        return await asyncio.gather(*tasks, return_exceptions=False)
    
    def _merge_contexts(
        self, 
        contexts: List[ReceiptContext]
    ) -> ReceiptContext:
        """Merge multiple contexts from parallel execution."""
        if not contexts:
            return ReceiptContext()
        
        # Start with first context
        merged = contexts[0]
        
        # Merge results from other contexts
        for ctx in contexts[1:]:
            # Merge analysis results
            if ctx.structure_analysis:
                merged.structure_analysis = ctx.structure_analysis
            if ctx.field_labels:
                merged.field_labels = ctx.field_labels
            if ctx.line_items:
                merged.line_items = ctx.line_items
            
            # Merge metadata
            merged.places_data = {**merged.places_data, **ctx.places_data} if ctx.places_data else merged.places_data
            merged.confidence_scores.update(ctx.confidence_scores)
            merged.execution_times.update(ctx.execution_times)
            merged.errors.update(ctx.errors)
            merged.history.extend(ctx.history)
        
        return merged
```

### Step 5: Integration with ReceiptLabeler

```python
# receipt_label/core/labeler.py

class ReceiptLabeler:
    """Updated to use orchestration."""
    
    def __init__(self, ..., use_orchestration: bool = False):
        # ... existing init ...
        self.use_orchestration = use_orchestration
        
        if use_orchestration:
            self.triage_agent = TriageAgent(
                strategy=RouteStrategy.HYBRID,
                confidence_threshold=0.85
            )
            self.orchestrator = ToolOrchestrator(
                tool_registry=ToolRegistry(),
                max_parallel=3
            )
    
    def label_receipt(self, ...):
        if self.use_orchestration:
            return self._label_receipt_orchestrated(...)
        elif self.use_tools:
            return self._label_receipt_with_tools(...)
        else:
            return self._label_receipt_legacy(...)
    
    def _label_receipt_orchestrated(
        self,
        receipt: Receipt,
        receipt_words: List[ReceiptWord],
        receipt_lines: List[ReceiptLine],
        **kwargs
    ) -> LabelingResult:
        """Label receipt using agent orchestration."""
        
        # Create context
        context = ReceiptContext(
            receipt_id=str(receipt.receipt_id),
            image_id=receipt.image_id,
            receipt=receipt,
            receipt_words=receipt_words,
            receipt_lines=receipt_lines
        )
        
        # Get routing decisions from agent
        decisions = self.triage_agent.decide(context)
        
        self.logger.info(
            f"Triage agent selected {len(decisions)} tools: "
            f"{[d.tool_name for d in decisions]}"
        )
        
        # Execute plan
        result_context = asyncio.run(
            self.orchestrator.execute_plan(decisions, context)
        )
        
        # Convert to LabelingResult
        return self._context_to_result(result_context)
```

## Testing Strategy

### Unit Tests for TriageAgent

```python
# test_triage_agent.py

def test_triage_basic_receipt():
    """Test triage decisions for basic receipt."""
    agent = TriageAgent(strategy=RouteStrategy.RULE_BASED)
    
    context = ReceiptContext(
        receipt_id="test-123",
        receipt=test_receipt,
        receipt_words=test_words_with_total,
        receipt_lines=test_lines
    )
    
    decisions = agent.decide(context)
    
    # Should include core tools
    tool_names = [d.tool_name for d in decisions]
    assert "structure_analysis" in tool_names
    assert "field_labeling" in tool_names
    
    # Check ordering
    structure_idx = tool_names.index("structure_analysis")
    labeling_idx = tool_names.index("field_labeling")
    assert structure_idx < labeling_idx

def test_triage_merchant_receipt():
    """Test triage includes merchant lookup."""
    agent = TriageAgent()
    
    # Receipt with store name
    context = create_context_with_text("Whole Foods Market\n123 Main St\nTotal: $42.50")
    
    decisions = agent.decide(context)
    tool_names = [d.tool_name for d in decisions]
    
    assert "places_lookup" in tool_names
    assert "similar_receipt_retrieval" in tool_names
```

### Integration Tests for Orchestration

```python
# test_orchestrator.py

@pytest.mark.asyncio
async def test_orchestrator_parallel_execution():
    """Test parallel tool execution."""
    orchestrator = ToolOrchestrator(tool_registry=mock_registry)
    
    # Create decisions with parallel groups
    decisions = [
        ToolDecision(
            tool_name="places_lookup",
            priority=1,
            parallel_group="enrichment"
        ),
        ToolDecision(
            tool_name="similar_retrieval",
            priority=1,
            parallel_group="enrichment"
        ),
        ToolDecision(
            tool_name="structure_analysis",
            priority=2,
            required_tools=[]
        )
    ]
    
    context = create_test_context()
    result = await orchestrator.execute_plan(decisions, context)
    
    # Verify parallel execution
    assert "places_lookup" in result.execution_times
    assert "similar_retrieval" in result.execution_times
    
    # Check that parallel tools started at similar times
    history = result.history
    places_time = next(e.timestamp for e in history if e.tool_name == "places_lookup")
    retrieval_time = next(e.timestamp for e in history if e.tool_name == "similar_retrieval")
    assert abs((places_time - retrieval_time).total_seconds()) < 0.1
```

## Performance Optimization

### Caching Strategy

```python
# receipt_label/agents/cache.py

class DecisionCache:
    """Cache triage decisions for similar receipts."""
    
    def __init__(self, ttl_seconds: int = 3600):
        self.cache: Dict[str, Tuple[List[ToolDecision], float]] = {}
        self.ttl = ttl_seconds
    
    def get_key(self, context: ReceiptContext) -> str:
        """Generate cache key from receipt features."""
        features = [
            len(context.receipt_words),
            len(context.receipt_lines),
            bool(context.places_data),
            # Add more distinguishing features
        ]
        return hashlib.md5(str(features).encode()).hexdigest()
    
    def get(self, context: ReceiptContext) -> Optional[List[ToolDecision]]:
        """Get cached decisions if available."""
        key = self.get_key(context)
        if key in self.cache:
            decisions, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return decisions
        return None
    
    def set(self, context: ReceiptContext, decisions: List[ToolDecision]):
        """Cache decisions."""
        key = self.get_key(context)
        self.cache[key] = (decisions, time.time())
```

## Metrics and Monitoring

```python
# receipt_label/monitoring/metrics.py

class OrchestrationMetrics:
    """Track orchestration performance."""
    
    def __init__(self):
        self.tool_execution_times: Dict[str, List[float]] = {}
        self.tool_success_rates: Dict[str, Tuple[int, int]] = {}  # (success, total)
        self.parallel_execution_count = 0
        self.total_receipts = 0
    
    def record_execution(self, context: ReceiptContext):
        """Record metrics from executed context."""
        self.total_receipts += 1
        
        # Track execution times
        for tool_name, duration in context.execution_times.items():
            if tool_name not in self.tool_execution_times:
                self.tool_execution_times[tool_name] = []
            self.tool_execution_times[tool_name].append(duration)
        
        # Track success rates
        for event in context.history:
            if event.tool_name not in self.tool_success_rates:
                self.tool_success_rates[event.tool_name] = (0, 0)
            
            success, total = self.tool_success_rates[event.tool_name]
            if event.success:
                success += 1
            self.tool_success_rates[event.tool_name] = (success, total + 1)
    
    def get_summary(self) -> Dict[str, Any]:
        """Get metrics summary."""
        summary = {
            "total_receipts": self.total_receipts,
            "tool_stats": {}
        }
        
        for tool_name in self.tool_execution_times:
            times = self.tool_execution_times[tool_name]
            success, total = self.tool_success_rates.get(tool_name, (0, 0))
            
            summary["tool_stats"][tool_name] = {
                "avg_duration": np.mean(times),
                "p95_duration": np.percentile(times, 95),
                "success_rate": success / total if total > 0 else 0,
                "total_executions": total
            }
        
        return summary
```

## Success Criteria

- Triage agent makes correct routing decisions >95% of the time
- Parallel execution reduces latency by >30% for multi-tool flows  
- No deadlocks or circular dependencies in production
- ML routing (when enabled) improves accuracy by >5%

## Next Steps

- Week 4: Add streaming updates and real-time observability
- Future: Train ML router on production data
- Future: Add cost-based optimization to routing decisions