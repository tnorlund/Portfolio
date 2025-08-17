# Migration Guide: Batch Processing to Real-Time LangChain Graphs

## Overview

This guide outlines the migration from AWS Step Functions + OpenAI Batch API to real-time LangChain graphs with tool calling.

## Current vs New Architecture

### Current (Batch Processing)
```
Step Functions → Lambda → Format Prompts → OpenAI Batch API (24hr) → Lambda → DynamoDB
```

### New (Real-Time LangChain)
```
API Request → LangChain Graph → Tool Calling → Immediate Response → DynamoDB
```

## Key Benefits of Migration

1. **Real-time Processing**: Results in seconds instead of 24 hours
2. **Tool Calling**: More structured and reliable than function calling
3. **Stream Processing**: Process receipts as they arrive
4. **Better Error Handling**: Immediate feedback on validation failures
5. **Cost Flexibility**: Can still batch for cost savings when needed

## Access Pattern Transformations

### 1. Label Fetching
**Current**: Query all labels with `ValidationStatus.NONE`, chunk into batches
**New**: Stream labels as they're created, validate immediately

### 2. Context Gathering
**Current**: Fetch all receipt data upfront for batch
**New**: Lazy load context per receipt as needed

### 3. Result Processing
**Current**: Poll for batch completion, process all results at once
**New**: Stream results back to client as they complete

## Implementation Steps

### Step 1: Install Dependencies
```bash
pip install langchain langgraph langchain-openai
```

### Step 2: Create Graph Infrastructure
```python
from receipt_label.langchain_validation.graph_design import create_validation_graph

# Initialize the graph once
validation_graph = create_validation_graph()
```

### Step 3: Replace Step Function Triggers

Instead of triggering Step Functions:
```python
# Old way
sfn_client.start_execution(
    stateMachineArn=SUBMIT_BATCH_SFN_ARN,
    input=json.dumps({"batch_id": batch_id})
)
```

Use the LangChain graph:
```python
# New way
result = await validation_graph.ainvoke({
    "image_id": image_id,
    "receipt_id": receipt_id,
    "labels_to_validate": labels
})
```

### Step 4: API Endpoint Implementation
```python
from fastapi import FastAPI, BackgroundTasks
from receipt_label.langchain_validation import validate_receipt_labels

app = FastAPI()

@app.post("/validate/realtime")
async def validate_realtime(image_id: str, receipt_id: int):
    """Real-time validation endpoint"""
    labels = get_labels_needing_validation(image_id, receipt_id)
    result = await validate_receipt_labels(image_id, receipt_id, labels)
    return result

@app.post("/validate/batch")
async def validate_batch(background_tasks: BackgroundTasks):
    """Batch validation for cost optimization"""
    labels = list_all_pending_labels()
    receipts = group_labels_by_receipt(labels)
    
    # Queue for background processing
    for image_id, receipt_id, labels in receipts:
        background_tasks.add_task(
            validate_receipt_labels, 
            image_id, 
            receipt_id, 
            labels
        )
    
    return {"queued": len(receipts)}
```

## Tool Calling Implementation

### Current Function Calling Format
```python
functions = [{
    "name": "validate_labels",
    "parameters": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {...}
            }
        }
    }
}]
```

### New Tool Calling Format (LangChain)
```python
from langchain_core.tools import tool
from pydantic import BaseModel

class ValidateLabelsInput(BaseModel):
    results: List[LabelValidationResult]

@tool
def validate_labels(input: ValidateLabelsInput) -> dict:
    """Validate multiple receipt-word labels"""
    return process_validation_results(input.results)

# Bind to LLM
llm = ChatOpenAI(model="gpt-4o-mini").bind_tools([validate_labels])
```

## Gradual Migration Strategy

### Phase 1: Dual Mode Operation
- Keep Step Functions for batch processing
- Add LangChain for real-time validation
- Route based on urgency

### Phase 2: Performance Monitoring
```python
class ValidationMetrics:
    def __init__(self):
        self.batch_latency = []
        self.realtime_latency = []
        self.cost_per_receipt = []
    
    async def track_validation(self, method: str, start_time: float):
        latency = time.time() - start_time
        if method == "batch":
            self.batch_latency.append(latency)
        else:
            self.realtime_latency.append(latency)
```

### Phase 3: Full Migration
- Deprecate Step Functions
- Use LangChain for all validation
- Implement smart batching for cost optimization

## Cost Optimization Strategies

### 1. Smart Batching
```python
class SmartBatcher:
    def __init__(self, batch_threshold: int = 50):
        self.pending = []
        self.batch_threshold = batch_threshold
    
    async def add_receipt(self, receipt_data):
        self.pending.append(receipt_data)
        
        # Process immediately if urgent
        if receipt_data.get("urgent"):
            return await self.process_single(receipt_data)
        
        # Batch if we hit threshold
        if len(self.pending) >= self.batch_threshold:
            return await self.process_batch()
```

### 2. Caching Layer
```python
from functools import lru_cache
import hashlib

@lru_cache(maxsize=1000)
def get_cached_validation(receipt_hash: str):
    """Cache validation results for similar receipts"""
    return dynamo.get_cached_result(receipt_hash)

def compute_receipt_hash(lines: List[str], merchant: str) -> str:
    """Generate hash for receipt similarity"""
    content = f"{merchant}:{''.join(sorted(lines))}"
    return hashlib.sha256(content.encode()).hexdigest()
```

### 3. Pattern-First Validation
```python
async def validate_with_patterns_first(labels):
    """Use patterns before LLM"""
    pattern_results = await detect_patterns(labels)
    
    # Only send unmatched labels to LLM
    unmatched = [l for l in labels if l not in pattern_results]
    
    if unmatched:
        llm_results = await validate_with_llm(unmatched)
        return {**pattern_results, **llm_results}
    
    return pattern_results
```

## Database Schema Updates

### Add Real-Time Tracking
```python
class ValidationMetadata:
    validation_method: str  # "batch" or "realtime"
    processing_time_ms: int
    llm_model_used: str
    tool_calls_made: List[str]
    pattern_match_rate: float
```

### Update DynamoDB Entity
```python
def update_label_with_metadata(label: ReceiptWordLabel, metadata: ValidationMetadata):
    label.validation_metadata = metadata.__dict__
    dynamo.update_item(label)
```

## Monitoring and Observability

### CloudWatch Metrics
```python
import boto3
cloudwatch = boto3.client('cloudwatch')

def emit_validation_metric(method: str, latency: float, cost: float):
    cloudwatch.put_metric_data(
        Namespace='ReceiptValidation',
        MetricData=[
            {
                'MetricName': f'{method}_latency',
                'Value': latency,
                'Unit': 'Seconds'
            },
            {
                'MetricName': f'{method}_cost',
                'Value': cost,
                'Unit': 'None'
            }
        ]
    )
```

### LangSmith Integration
```python
from langsmith import Client

client = Client()

# Track LangChain graph execution
with client.trace() as trace:
    result = await validation_graph.ainvoke(state)
    trace.log_metrics({
        "tokens_used": result.get("tokens"),
        "tools_called": len(result.get("tool_calls", [])),
        "validation_accuracy": calculate_accuracy(result)
    })
```

## Rollback Plan

### Feature Flags
```python
class ValidationRouter:
    def __init__(self):
        self.use_langchain = os.getenv("USE_LANGCHAIN", "false") == "true"
    
    async def validate(self, labels):
        if self.use_langchain:
            return await langchain_validate(labels)
        else:
            return await stepfunction_validate(labels)
```

### Gradual Rollout
```python
import random

def should_use_langchain(user_id: str) -> bool:
    """Gradual rollout based on user ID"""
    rollout_percentage = int(os.getenv("LANGCHAIN_ROLLOUT", "0"))
    user_hash = int(hashlib.md5(user_id.encode()).hexdigest(), 16)
    return (user_hash % 100) < rollout_percentage
```

## Performance Benchmarks

### Expected Improvements
| Metric | Step Functions | LangChain | Improvement |
|--------|---------------|-----------|-------------|
| Latency | 24 hours | 2-5 seconds | 99.99% |
| Cost (per 1K receipts) | $50 (batch) | $55 (realtime) | -10% |
| Error Recovery | Manual | Automatic | ∞ |
| Scalability | Limited by batch | Unlimited | ∞ |

### Break-Even Analysis
- Real-time processing worth it if user waiting
- Batch processing still optimal for bulk imports
- Hybrid approach recommended for production

## Testing Strategy

### Unit Tests
```python
import pytest
from unittest.mock import Mock

@pytest.mark.asyncio
async def test_langchain_validation():
    # Mock LLM responses
    mock_llm = Mock()
    mock_llm.ainvoke.return_value = create_mock_response()
    
    # Test graph execution
    graph = create_validation_graph(llm=mock_llm)
    result = await graph.ainvoke(test_state)
    
    assert result["completed"] == True
    assert len(result["validation_results"]) > 0
```

### Integration Tests
```python
@pytest.mark.integration
async def test_end_to_end_validation():
    # Use test receipt data
    test_receipt = load_test_receipt()
    
    # Run through full pipeline
    result = await validate_receipt_labels(
        test_receipt["image_id"],
        test_receipt["receipt_id"],
        test_receipt["labels"]
    )
    
    # Verify DynamoDB updates
    updated_labels = dynamo.get_labels(test_receipt["image_id"])
    assert all(l.validation_status != "NONE" for l in updated_labels)
```

## Migration Timeline

### Week 1-2: Setup
- Install dependencies
- Create LangChain graph components
- Set up monitoring

### Week 3-4: Testing
- Unit and integration tests
- Performance benchmarking
- Cost analysis

### Week 5-6: Gradual Rollout
- Deploy dual-mode system
- Monitor metrics
- Gather feedback

### Week 7-8: Full Migration
- Deprecate Step Functions
- Update documentation
- Train team

## Conclusion

The migration to LangChain provides immediate benefits in latency and user experience while maintaining cost efficiency through smart batching. The tool calling pattern is more reliable than function calling, and the graph architecture provides better observability and error handling.

Key takeaways:
1. Start with dual-mode operation for safe rollout
2. Use pattern detection to minimize LLM calls
3. Implement smart batching for cost optimization
4. Monitor performance metrics closely
5. Keep rollback plan ready

The new architecture supports both real-time and batch processing, giving you flexibility to optimize for either latency or cost based on business needs.