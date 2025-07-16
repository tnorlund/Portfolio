# Phase 3: Cost & Performance Monitoring Strategy

## Overview

Since you already have feedback loops (Pinecone metadata + DynamoDB audit trail), HITL (needs_review state), and merchant querying capabilities, this document focuses on the critical missing pieces: cost and performance monitoring for your agentic workflow.

## 1. Cost Monitoring Integration

### Cost Points in LangGraph Workflow

```python
COST_POINTS = {
    # API Calls
    "openai_gpt4": {
        "prompt_tokens": 0.01 / 1000,  # $0.01 per 1K tokens
        "completion_tokens": 0.03 / 1000,  # $0.03 per 1K tokens
    },
    "openai_embedding": {
        "tokens": 0.0001 / 1000,  # $0.0001 per 1K tokens
    },
    
    # AWS Services
    "dynamodb": {
        "read_request": 0.00025 / 1000,  # $0.25 per million
        "write_request": 0.00125 / 1000,  # $1.25 per million
        "query": 0.00025 / 1000,
    },
    "lambda": {
        "invocation": 0.0000002,  # $0.20 per million
        "gb_second": 0.0000166667,  # $0.0000166667 per GB-second
    },
    
    # Pinecone
    "pinecone": {
        "query": 0.00001,  # Depends on plan
        "upsert": 0.00001,
    }
}
```

### Cost Tracking in Workflow State

```python
class CostAwareState(TypedDict):
    """Enhanced state with cost tracking"""
    # Existing state fields...
    receipt_words: List[ReceiptWord]
    pattern_results: Dict[str, Any]
    
    # Cost tracking
    cost_breakdown: Dict[str, float]
    total_cost: float
    cost_optimization_notes: List[str]
    
    # Token usage
    gpt_prompt_tokens: int
    gpt_completion_tokens: int
    embedding_tokens: int
    
    # Service calls
    api_calls: Dict[str, int]  # service -> count
```

### Cost Monitoring Nodes

```python
# Add cost tracking to each node
async def gpt_labeling_node_with_cost(state: CostAwareState) -> CostAwareState:
    """GPT labeling with cost tracking"""
    
    # Track tokens before call
    prompt = state["gpt_prompt"]
    prompt_tokens = count_tokens(prompt)
    
    # Make GPT call
    response = await call_gpt(prompt)
    completion_tokens = response.usage.completion_tokens
    
    # Calculate cost
    gpt_cost = (
        prompt_tokens * COST_POINTS["openai_gpt4"]["prompt_tokens"] +
        completion_tokens * COST_POINTS["openai_gpt4"]["completion_tokens"]
    )
    
    # Update state
    state["gpt_prompt_tokens"] += prompt_tokens
    state["gpt_completion_tokens"] += completion_tokens
    state["cost_breakdown"]["gpt"] = state["cost_breakdown"].get("gpt", 0) + gpt_cost
    state["total_cost"] += gpt_cost
    state["api_calls"]["openai_gpt4"] = state["api_calls"].get("openai_gpt4", 0) + 1
    
    # Add optimization note if expensive
    if gpt_cost > 0.05:  # More than 5 cents
        state["cost_optimization_notes"].append(
            f"High GPT cost: ${gpt_cost:.3f} for {prompt_tokens + completion_tokens} tokens"
        )
    
    return state
```

### Cost Optimization Strategies

```python
class CostOptimizer:
    """Real-time cost optimization decisions"""
    
    def __init__(self, budget_limits: Dict[str, float]):
        self.budget_limits = budget_limits  # e.g., {"per_receipt": 0.10, "hourly": 10.0}
        self.hourly_spend = deque(maxlen=3600)  # Track last hour
        
    async def should_use_batch_api(self, state: CostAwareState) -> bool:
        """Decide if should queue for batch processing"""
        
        # If already over per-receipt budget, use batch
        if state["total_cost"] > self.budget_limits["per_receipt"] * 0.5:
            return True
        
        # If hourly spend is high, use batch
        if sum(self.hourly_spend) > self.budget_limits["hourly"] * 0.8:
            return True
        
        # If not urgent (no needs_review flag), use batch
        if not state.get("urgent", False):
            return True
            
        return False
    
    async def select_model(self, state: CostAwareState) -> str:
        """Choose model based on cost/performance tradeoff"""
        
        # Start with GPT-4 for complex receipts
        if state.get("merchant_unknown", False):
            return "gpt-4"
        
        # Use GPT-3.5 for simple gap-filling
        if len(state["missing_labels"]) < 3:
            return "gpt-3.5-turbo"
        
        # Use GPT-4 if previous attempts failed
        if state.get("retry_count", 0) > 1:
            return "gpt-4"
            
        return "gpt-3.5-turbo"
```

## 2. Performance Monitoring

### Key Performance Metrics

```python
@dataclass
class AgentPerformanceMetrics:
    """Core metrics for agent performance"""
    
    # Latency metrics (milliseconds)
    pattern_detection_time: float
    gpt_labeling_time: float
    validation_time: float
    total_processing_time: float
    
    # Accuracy metrics
    pattern_coverage: float  # % words labeled by patterns
    validation_pass_rate: float  # % labels passing validation
    confidence_average: float  # Avg confidence of all labels
    
    # Efficiency metrics
    gpt_skip_rate: float  # % receipts not needing GPT
    retry_count: int  # Number of validation retries
    tokens_per_label: float  # Efficiency of GPT usage
    
    # Quality metrics
    essential_fields_found: bool  # All 4 essential fields
    line_items_matched: float  # % prices matched to products
    math_validation_passed: bool  # Financial math checks out
```

### Performance Tracking Nodes

```python
async def performance_monitoring_node(state: CostAwareState) -> CostAwareState:
    """Track performance metrics throughout workflow"""
    
    # Calculate metrics
    metrics = AgentPerformanceMetrics(
        pattern_detection_time=state["timings"]["pattern_detection"],
        gpt_labeling_time=state["timings"].get("gpt_labeling", 0),
        validation_time=state["timings"]["validation"],
        total_processing_time=sum(state["timings"].values()),
        
        pattern_coverage=len(state["pattern_labels"]) / len(state["receipt_words"]),
        validation_pass_rate=sum(
            1 for v in state["validation_results"].values() if v
        ) / len(state["validation_results"]),
        confidence_average=np.mean([
            l["confidence"] for l in state["all_labels"].values()
        ]),
        
        gpt_skip_rate=1.0 if not state.get("gpt_called") else 0.0,
        retry_count=state.get("retry_count", 0),
        tokens_per_label=state.get("gpt_completion_tokens", 0) / max(
            len(state.get("gpt_labels", {})), 1
        ),
        
        essential_fields_found=all(
            field in state["all_labels"] 
            for field in ["MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL"]
        ),
        line_items_matched=calculate_line_item_match_rate(state),
        math_validation_passed=state["validation_results"].get("mathematical", False)
    )
    
    # Store metrics
    await store_performance_metrics(state["receipt_id"], metrics)
    
    # Check for performance issues
    if metrics.total_processing_time > 3000:  # Over 3 seconds
        state["performance_warnings"].append("Slow processing time")
    
    if metrics.pattern_coverage < 0.5:
        state["performance_warnings"].append("Low pattern coverage")
    
    return state
```

### Real-time Performance Dashboard

```python
class AgentHealthDashboard:
    """Real-time monitoring of agent health"""
    
    def __init__(self):
        self.metrics_buffer = {
            "last_5_min": deque(maxlen=300),
            "last_hour": deque(maxlen=3600),
            "last_24h": deque(maxlen=86400)
        }
        
    async def get_dashboard_snapshot(self) -> Dict:
        """Current health snapshot"""
        
        last_5_min = list(self.metrics_buffer["last_5_min"])
        
        return {
            "current_status": {
                "receipts_per_minute": len(last_5_min) / 5,
                "avg_processing_time_ms": np.mean([m.total_processing_time for m in last_5_min]),
                "gpt_skip_rate": np.mean([m.gpt_skip_rate for m in last_5_min]),
                "validation_success_rate": np.mean([m.validation_pass_rate for m in last_5_min]),
                "avg_cost_per_receipt": np.mean([m.cost for m in last_5_min])
            },
            
            "performance_trends": {
                "pattern_coverage_trend": self.calculate_trend("pattern_coverage"),
                "cost_trend": self.calculate_trend("cost"),
                "latency_trend": self.calculate_trend("total_processing_time")
            },
            
            "alerts": self.check_alert_conditions(),
            
            "cost_breakdown": {
                "gpt_costs": sum(m.cost_breakdown.get("gpt", 0) for m in last_5_min),
                "aws_costs": sum(m.cost_breakdown.get("aws", 0) for m in last_5_min),
                "pinecone_costs": sum(m.cost_breakdown.get("pinecone", 0) for m in last_5_min)
            }
        }
    
    def check_alert_conditions(self) -> List[Alert]:
        """Check for issues requiring attention"""
        
        alerts = []
        recent = list(self.metrics_buffer["last_5_min"])
        
        # Cost spike
        if recent:
            recent_avg_cost = np.mean([m.cost for m in recent])
            if recent_avg_cost > 0.10:  # Over 10 cents per receipt
                alerts.append(Alert(
                    level="warning",
                    message=f"High cost per receipt: ${recent_avg_cost:.3f}",
                    action="Consider using batch API or GPT-3.5"
                ))
        
        # Performance degradation
        if recent:
            recent_pattern_coverage = np.mean([m.pattern_coverage for m in recent])
            if recent_pattern_coverage < 0.5:
                alerts.append(Alert(
                    level="warning",
                    message=f"Low pattern coverage: {recent_pattern_coverage:.1%}",
                    action="Review pattern detection rules"
                ))
        
        return alerts
```

## 3. Integration with LangGraph

### Enhanced Workflow with Monitoring

```python
def create_monitored_workflow():
    """LangGraph workflow with cost and performance monitoring"""
    
    workflow = StateGraph(CostAwareState)
    
    # Initialize monitoring
    workflow.add_node("init_monitoring", init_monitoring_node)
    
    # Core nodes with timing decorators
    workflow.add_node("pattern_detection", timed(pattern_detection_node))
    workflow.add_node("gpt_labeling", timed(gpt_labeling_node_with_cost))
    workflow.add_node("validation", timed(validation_node))
    
    # Monitoring nodes
    workflow.add_node("calculate_costs", calculate_total_costs_node)
    workflow.add_node("performance_check", performance_monitoring_node)
    workflow.add_node("store_metrics", store_metrics_node)
    
    # Cost-based routing
    workflow.add_conditional_edges(
        "pattern_detection",
        lambda x: "batch_queue" if cost_optimizer.should_use_batch_api(x) else "gpt_labeling"
    )
    
    # Always end with metrics
    workflow.add_edge("validation", "calculate_costs")
    workflow.add_edge("calculate_costs", "performance_check")
    workflow.add_edge("performance_check", "store_metrics")
    workflow.add_edge("store_metrics", END)
    
    return workflow.compile()

# Timing decorator
def timed(func):
    """Decorator to track node execution time"""
    async def wrapper(state):
        start = time.time()
        result = await func(state)
        elapsed = (time.time() - start) * 1000
        
        result["timings"][func.__name__] = elapsed
        return result
    return wrapper
```

## 4. Cost Control Strategies

### Dynamic Budget Management

```python
class BudgetManager:
    """Enforce cost limits dynamically"""
    
    def __init__(self, daily_budget: float = 100.0):
        self.daily_budget = daily_budget
        self.spent_today = 0.0
        self.reset_time = datetime.utcnow().replace(hour=0, minute=0)
        
    async def check_budget(self, estimated_cost: float) -> bool:
        """Check if operation fits in budget"""
        
        # Reset daily counter
        if datetime.utcnow().date() > self.reset_time.date():
            self.spent_today = 0.0
            self.reset_time = datetime.utcnow().replace(hour=0, minute=0)
        
        # Check if over budget
        if self.spent_today + estimated_cost > self.daily_budget:
            # Log budget exceeded
            logger.warning(f"Daily budget exceeded: ${self.spent_today:.2f} + ${estimated_cost:.2f} > ${self.daily_budget:.2f}")
            return False
            
        return True
    
    async def record_spend(self, actual_cost: float):
        """Record actual spend"""
        self.spent_today += actual_cost
        
        # Alert if approaching limit
        if self.spent_today > self.daily_budget * 0.8:
            await send_budget_alert(
                f"80% of daily budget used: ${self.spent_today:.2f} / ${self.daily_budget:.2f}"
            )
```

## Summary

This monitoring strategy provides:

1. **Cost Visibility**: Track every API call and its cost
2. **Performance Metrics**: Measure latency, accuracy, and efficiency
3. **Real-time Dashboards**: Monitor agent health continuously
4. **Budget Controls**: Prevent runaway costs
5. **Optimization Decisions**: Route to cheaper options when possible
6. **Alert System**: Proactive issue detection

The key is integrating these monitors directly into your LangGraph nodes so every decision is tracked and optimized in real-time.