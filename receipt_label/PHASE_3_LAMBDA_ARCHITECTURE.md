# Phase 3: Lambda Architecture & State Management

## State Storage Options for LangGraph in Lambda

### Option 1: In-Memory State (Recommended for Receipt Processing)

**Why this works for receipts:**
- Each receipt is processed independently
- Total processing time < 3 seconds
- State doesn't need to persist between Lambda invocations
- No external state store = lower latency & cost

```python
# lambda_handler.py
import json
from langgraph.graph import StateGraph
from receipt_label.langgraph_integration.workflow import create_phase3_workflow

async def lambda_handler(event, context):
    """Process a single receipt entirely in memory"""
    
    # Extract receipt data from event
    receipt_id = event['receipt_id']
    receipt_words = event['words']
    
    # Create workflow (state is managed in-memory by LangGraph)
    workflow = create_phase3_workflow()
    
    # Initial state
    initial_state = {
        "receipt_words": receipt_words,
        "receipt_metadata": await get_receipt_metadata(receipt_id),
        "retry_count": 0
    }
    
    # Run workflow - all state transitions happen in memory
    final_state = await workflow.arun(initial_state)
    
    # Return results
    return {
        'statusCode': 200,
        'body': json.dumps({
            'receipt_id': receipt_id,
            'labels': final_state['final_labels'],
            'cost': final_state.get('total_cost', 0),
            'method': 'langgraph' if final_state.get('gpt_called') else 'patterns_only'
        })
    }
```

### Option 2: DynamoDB Checkpointing (For Long-Running or Resumable Workflows)

**When to use:**
- Processing that might timeout
- Need to resume failed workflows
- Audit trail requirements
- Batch processing with checkpoints

```python
# langgraph_integration/checkpointer.py
from langgraph.checkpoint import CheckpointStore
from typing import Dict, Any
import boto3
import json

class DynamoDBCheckpointer(CheckpointStore):
    """Store LangGraph checkpoints in DynamoDB"""
    
    def __init__(self, table_name: str = "receipt-processing-checkpoints"):
        self.table = boto3.resource('dynamodb').Table(table_name)
    
    async def save_checkpoint(self, workflow_id: str, checkpoint: Dict[str, Any]):
        """Save state after each node execution"""
        self.table.put_item(
            Item={
                'pk': f'CHECKPOINT#{workflow_id}',
                'sk': f'STEP#{checkpoint["step_number"]}',
                'state': json.dumps(checkpoint['state']),
                'node_name': checkpoint['node_name'],
                'timestamp': checkpoint['timestamp'],
                'ttl': int(time.time()) + 3600  # Expire after 1 hour
            }
        )
    
    async def load_checkpoint(self, workflow_id: str) -> Dict[str, Any]:
        """Load latest checkpoint for resuming"""
        response = self.table.query(
            KeyConditionExpression='pk = :pk',
            ExpressionAttributeValues={':pk': f'CHECKPOINT#{workflow_id}'},
            ScanIndexForward=False,  # Latest first
            Limit=1
        )
        
        if response['Items']:
            item = response['Items'][0]
            return {
                'state': json.loads(item['state']),
                'step_number': int(item['sk'].split('#')[1]),
                'node_name': item['node_name']
            }
        return None
```

### Option 3: Step Functions Integration (For Complex Orchestration)

**When to use:**
- Need visual workflow monitoring
- Complex retry/error handling
- Integration with other AWS services
- Long-running workflows (>15 min)

```python
# step_functions/receipt_workflow.json
{
  "Comment": "Receipt Processing Workflow",
  "StartAt": "RunPatternDetection",
  "States": {
    "RunPatternDetection": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:us-east-1:123456789:function:pattern-detection",
      "Next": "CheckIfGPTNeeded"
    },
    "CheckIfGPTNeeded": {
      "Type": "Choice",
      "Choices": [{
        "Variable": "$.decision_outcome",
        "StringEquals": "SKIP",
        "Next": "Finalize"
      }],
      "Default": "CallGPT"
    },
    "CallGPT": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:us-east-1:123456789:function:gpt-labeling",
      "Retry": [{
        "ErrorEquals": ["States.TaskFailed"],
        "MaxAttempts": 2
      }],
      "Next": "Validate"
    },
    "Validate": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:us-east-1:123456789:function:validation",
      "Next": "Finalize"
    },
    "Finalize": {
      "Type": "Pass",
      "End": true
    }
  }
}
```

## Recommended Architecture for Receipt Processing

### Pure In-Memory LangGraph (Simplest & Fastest)

```python
# receipt_label/lambda/handler.py
import os
from typing import Dict, Any
from langgraph.graph import StateGraph, END
from receipt_label.decision_engine import DecisionEngineOrchestrator
from receipt_label.langgraph_integration.nodes import (
    gap_analysis_node,
    build_gpt_prompt_node,
    call_gpt_node,
    validation_node
)

# Initialize once outside handler for Lambda reuse
decision_orchestrator = DecisionEngineOrchestrator()

def create_lightweight_workflow():
    """Create a lightweight workflow for Lambda"""
    workflow = StateGraph(dict)
    
    # Add nodes
    workflow.add_node("pattern_detection", run_pattern_detection)
    workflow.add_node("gap_analysis", gap_analysis_node)
    workflow.add_node("build_prompt", build_gpt_prompt_node)
    workflow.add_node("call_gpt", call_gpt_node)
    workflow.add_node("validate", validation_node)
    
    # Define flow
    workflow.set_entry_point("pattern_detection")
    workflow.add_edge("pattern_detection", "gap_analysis")
    
    # Conditional routing
    workflow.add_conditional_edges(
        "gap_analysis",
        lambda x: "validate" if not x.get("missing_labels") else "build_prompt"
    )
    
    workflow.add_edge("build_prompt", "call_gpt")
    workflow.add_edge("call_gpt", "validate")
    workflow.add_edge("validate", END)
    
    return workflow.compile()

# Create workflow once
workflow = create_lightweight_workflow()

async def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for receipt processing.
    
    Expected event structure:
    {
        "receipt_id": "RECEIPT#12345",
        "words": [...],  # List of ReceiptWord objects
        "merchant_hint": "Walmart"  # Optional
    }
    """
    
    try:
        # Extract inputs
        receipt_id = event['receipt_id']
        words = event['words']
        
        # Get merchant data from DynamoDB (cached in Lambda container)
        merchant_data = await get_merchant_data_cached(
            event.get('merchant_hint', '')
        )
        
        # Run Phase 2 pattern detection
        pattern_results = await decision_orchestrator.process_receipt(words)
        
        # Check if patterns are sufficient (81.7% of cases)
        if pattern_results.decision.outcome == "SKIP":
            return {
                'statusCode': 200,
                'body': {
                    'receipt_id': receipt_id,
                    'labels': pattern_results.pattern_results,
                    'method': 'patterns_only',
                    'cost': 0.0,
                    'processing_time_ms': pattern_results.processing_time_ms
                }
            }
        
        # Run LangGraph workflow for complex cases
        initial_state = {
            "receipt_id": receipt_id,
            "receipt_words": words,
            "pattern_results": pattern_results.pattern_results,
            "merchant_data": merchant_data,
            "missing_labels": [],
            "retry_count": 0
        }
        
        # Execute workflow (all in memory)
        final_state = await workflow.arun(initial_state)
        
        # Save results to DynamoDB
        await save_labels_to_dynamo(
            receipt_id,
            final_state['final_labels'],
            final_state.get('total_cost', 0)
        )
        
        return {
            'statusCode': 200,
            'body': {
                'receipt_id': receipt_id,
                'labels': final_state['final_labels'],
                'method': 'langgraph_gpt',
                'cost': final_state.get('total_cost', 0),
                'processing_time_ms': final_state.get('total_time_ms', 0),
                'gpt_calls': final_state.get('gpt_call_count', 0)
            }
        }
        
    except Exception as e:
        logger.error(f"Error processing receipt {receipt_id}: {str(e)}")
        return {
            'statusCode': 500,
            'body': {'error': str(e)}
        }
```

### Lambda Configuration

```yaml
# serverless.yml or SAM template
ReceiptProcessingLambda:
  Type: AWS::Lambda::Function
  Properties:
    FunctionName: receipt-label-processor
    Runtime: python3.11
    Handler: handler.lambda_handler
    MemorySize: 1024  # Increase if needed for LangGraph
    Timeout: 30       # Max time for GPT calls + processing
    Architecture: arm64  # Must match layers
    Layers:
      - !Ref DynamoLayer
      - !Ref LabelLayer
    Environment:
      Variables:
        OPENAI_API_KEY: !Ref OpenAIApiKey
        DYNAMODB_TABLE: !Ref DynamoTable
        SKIP_PERFORMANCE_TESTS: true
        USE_BATCH_API: false  # Can't use batch in sync Lambda
    ReservedConcurrentExecutions: 100  # Limit concurrent GPT calls
```

### Cost Optimization for Lambda

1. **Lambda Memory Sizing**:
   - Start with 1024 MB
   - Use AWS Lambda Power Tuning to find optimal size
   - More memory = faster CPU = shorter duration

2. **Caching Strategy**:
   ```python
   # Cache merchant data in Lambda container
   MERCHANT_CACHE = {}
   CACHE_TTL = 300  # 5 minutes
   
   async def get_merchant_data_cached(merchant_name: str):
       cache_key = f"{merchant_name}:{int(time.time() / CACHE_TTL)}"
       
       if cache_key not in MERCHANT_CACHE:
           # Load from DynamoDB
           MERCHANT_CACHE[cache_key] = await load_merchant_data(merchant_name)
       
       return MERCHANT_CACHE[cache_key]
   ```

3. **Connection Pooling**:
   ```python
   # Initialize outside handler
   http_session = aiohttp.ClientSession()
   dynamo_client = boto3.client('dynamodb')
   
   # Reuse across invocations
   ```

## State Storage Decision Matrix

| Approach | Use When | Pros | Cons |
|----------|----------|------|------|
| **In-Memory (Recommended)** | - Receipt processing < 30s<br>- No resume needed<br>- Simple workflows | - Lowest latency<br>- No external dependencies<br>- Cheapest | - Can't resume failed workflows<br>- No audit trail |
| **DynamoDB Checkpoints** | - Need audit trail<br>- Resumable workflows<br>- Debugging complex flows | - Can resume<br>- Full history<br>- Debugging friendly | - Extra latency<br>- Additional cost<br>- More complex |
| **Step Functions** | - Complex orchestration<br>- Visual monitoring<br>- Long-running (>15min) | - Visual workflow<br>- Built-in retry<br>- AWS integration | - Higher cost<br>- More complex<br>- Separate deployment |

## Recommendation

For Phase 3 receipt processing, use **pure in-memory LangGraph** because:

1. **Each receipt is independent** - No need to share state
2. **Fast processing** - Total time < 3 seconds even with GPT
3. **Simpler deployment** - No external state store to manage
4. **Lower cost** - No additional DynamoDB writes for state
5. **Lambda-optimized** - Leverages Lambda's ephemeral compute model

The only state that needs persistence is:
- **Input**: Receipt words (from event)
- **Output**: Final labels (saved to DynamoDB)
- **Merchant data**: Cached in Lambda container

This keeps your Lambda lightweight, fast, and cost-effective while still leveraging LangGraph's powerful orchestration capabilities.