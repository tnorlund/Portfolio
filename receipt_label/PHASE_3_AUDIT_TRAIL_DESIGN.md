# Phase 3: DynamoDB Audit Trail Design

## Overview

The audit trail captures every decision, cost, and outcome in our labeling workflow. This enables:
- Learning what works/fails for each merchant
- Debugging specific receipt issues
- Cost tracking and optimization
- Pattern effectiveness measurement

## DynamoDB Schema Design

### 1. Workflow Execution Records

```python
# Track each workflow execution
{
    "pk": "AUDIT#2024-01-15",                    # Date partitioning
    "sk": "WORKFLOW#{receipt_id}#{timestamp}",   # Unique execution
    "gsi1pk": "RECEIPT#{receipt_id}",           # Query by receipt
    "gsi2pk": "MERCHANT#Walmart",               # Query by merchant
    
    # Execution metadata
    "receipt_id": "12345",
    "image_id": "uuid-123",
    "merchant_name": "Walmart",
    "start_time": "2024-01-15T10:30:00Z",
    "end_time": "2024-01-15T10:30:01.5Z",
    "duration_ms": 1500,
    
    # High-level outcome
    "workflow_path": ["load_merchant", "pattern_labeling", "decision_engine", "validation"],
    "gpt_used": false,
    "final_status": "success",
    "needs_review": false,
    
    # Cost tracking
    "costs": {
        "gpt_usd": 0.0,
        "pinecone_queries": 1,
        "dynamodb_reads": 3,
        "total_usd": 0.0001
    },
    
    # Coverage metrics
    "coverage": {
        "total_words": 150,
        "labeled_words": 142,
        "pattern_labeled": 142,
        "gpt_labeled": 0,
        "coverage_percentage": 94.7
    },
    
    # TTL for cost management
    "ttl": 1737846600  # 30 days
}
```

### 2. Node Decision Records

```python
# Detailed record for each node execution
{
    "pk": "AUDIT#2024-01-15",
    "sk": "NODE#{receipt_id}#001#load_merchant",
    "gsi1pk": "NODE_TYPE#load_merchant",         # Query by node type
    
    # Node execution details
    "node_name": "load_merchant",
    "start_time": "2024-01-15T10:30:00.000Z",
    "duration_ms": 125,
    
    # Inputs (compressed)
    "input_summary": {
        "merchant_name": "Walmart",
        "has_metadata": true
    },
    
    # Decisions made
    "decisions": {
        "action": "loaded_patterns",
        "patterns_count": 15,
        "source": "dynamodb_cache"
    },
    
    # Outputs (what changed)
    "state_changes": {
        "merchant_patterns": ["TC#", "ST#", "OP#"],
        "merchant_validation_status": "MATCHED"
    },
    
    # Errors/warnings
    "status": "success",
    "errors": [],
    "warnings": []
}
```

### 3. Pattern Effectiveness Records

```python
# Track which patterns work for which merchants
{
    "pk": "PATTERN_STATS#Walmart",
    "sk": "PATTERN#TC_NUMBER#2024-01",          # Monthly aggregation
    
    # Pattern performance
    "pattern": "TC#",
    "pattern_type": "MERCHANT_ID",
    "month": "2024-01",
    "usage_count": 1523,
    "success_count": 1520,
    "success_rate": 0.998,
    
    # Where it worked
    "successful_labels": {
        "TRANSACTION_ID": 1520
    },
    
    # Common failures
    "failure_examples": [
        {
            "receipt_id": "789",
            "expected": "TC#12345",
            "actual": "TC# 12345",  # Space issue
            "reason": "unexpected_space"
        }
    ]
}
```

### 4. GPT Usage Records

```python
# Track every GPT call for cost analysis
{
    "pk": "GPT_USAGE#2024-01-15",
    "sk": "CALL#{timestamp}#{receipt_id}",
    "gsi1pk": "MERCHANT#Walmart",
    
    # What triggered GPT
    "trigger": {
        "missing_essentials": ["GRAND_TOTAL"],
        "coverage_before": 73.2,
        "decision": "REQUIRED"
    },
    
    # Prompt details (compressed)
    "prompt_summary": {
        "context_words": 20,
        "target_fields": ["GRAND_TOTAL"],
        "prompt_template": "find_total_v1"
    },
    
    # Results
    "response": {
        "found_labels": {
            "GRAND_TOTAL": {
                "word_id": 145,
                "confidence": 0.85
            }
        },
        "tokens_used": 150,
        "cost_usd": 0.003
    },
    
    # Effectiveness
    "validation_passed": true,
    "human_corrected": false
}
```

### 5. Validation Failure Records

```python
# Learn from validation failures
{
    "pk": "VALIDATION_FAIL#2024-01",
    "sk": "FAIL#{timestamp}#{receipt_id}",
    "gsi1pk": "FAIL_TYPE#math_consistency",
    
    # What failed
    "validation_type": "math_consistency",
    "receipt_id": "12345",
    "merchant_name": "Target",
    
    # Failure details
    "failure": {
        "expected_total": 45.99,
        "found_total": 44.99,
        "difference": 1.00,
        "likely_cause": "missing_tax_line"
    },
    
    # Context for learning
    "receipt_context": {
        "had_tax_keyword": false,
        "line_count": 25,
        "currency_column_count": 1
    },
    
    # Resolution (if any)
    "resolution": {
        "method": "human_review",
        "corrected_labels": {
            "TAX": {"word_id": 89, "value": 1.00}
        }
    }
}
```

## Query Patterns

### 1. Daily Cost Report
```python
# Get today's costs
response = dynamodb.query(
    KeyConditionExpression="pk = :pk AND begins_with(sk, :sk_prefix)",
    ExpressionAttributeValues={
        ":pk": f"AUDIT#{date.today()}",
        ":sk_prefix": "WORKFLOW#"
    },
    ProjectionExpression="costs, merchant_name"
)

# Aggregate costs by merchant
costs_by_merchant = defaultdict(float)
for item in response['Items']:
    costs_by_merchant[item['merchant_name']] += item['costs']['total_usd']
```

### 2. Pattern Effectiveness Analysis
```python
# Find best patterns for a merchant
response = dynamodb.query(
    KeyConditionExpression="pk = :pk",
    ExpressionAttributeValues={
        ":pk": f"PATTERN_STATS#{merchant_name}"
    },
    ScanIndexForward=False  # Sort by success rate
)

# Use high-performing patterns first
best_patterns = [
    p for p in response['Items'] 
    if p['success_rate'] > 0.95
]
```

### 3. Debug Specific Receipt
```python
# Get complete audit trail for a receipt
response = dynamodb.query(
    IndexName="gsi1",
    KeyConditionExpression="gsi1pk = :pk",
    ExpressionAttributeValues={
        ":pk": f"RECEIPT#{receipt_id}"
    }
)

# Reconstruct exact workflow
workflow = sorted(response['Items'], key=lambda x: x['sk'])
for step in workflow:
    print(f"{step['node_name']}: {step['decisions']}")
```

### 4. GPT Optimization Opportunities
```python
# Find patterns in GPT usage
response = dynamodb.query(
    KeyConditionExpression="pk = :pk",
    ExpressionAttributeValues={
        ":pk": f"GPT_USAGE#{date.today()}"
    }
)

# Analyze what triggers GPT
triggers = defaultdict(int)
for item in response['Items']:
    for field in item['trigger']['missing_essentials']:
        triggers[field] += 1

# Most common missing field → improve patterns
```

## Implementation in Workflow

### Audit Writer Node
```python
async def audit_trail_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Write comprehensive audit trail to DynamoDB.
    
    This node runs at the end to capture all decisions.
    """
    
    # Prepare audit records
    audit_records = []
    
    # 1. Workflow execution record
    workflow_record = {
        "pk": f"AUDIT#{datetime.now().strftime('%Y-%m-%d')}",
        "sk": f"WORKFLOW#{state['receipt_id']}#{datetime.now().isoformat()}",
        "gsi1pk": f"RECEIPT#{state['receipt_id']}",
        "gsi2pk": f"MERCHANT#{state.get('merchant_name', 'unknown')}",
        
        "receipt_id": state["receipt_id"],
        "image_id": state["image_id"],
        "merchant_name": state.get("merchant_name"),
        
        "workflow_path": [d["node"] for d in state["decisions"]],
        "gpt_used": state.get("needs_gpt", False),
        "final_status": "success" if not state.get("errors") else "failed",
        "needs_review": state.get("needs_review", False),
        
        "costs": {
            "gpt_usd": state["metrics"].get("gpt_cost_usd", 0),
            "total_usd": state["metrics"].get("total_cost_usd", 0)
        },
        
        "coverage": {
            "total_words": state.get("total_words", 0),
            "labeled_words": state.get("labeled_words", 0),
            "coverage_percentage": state.get("coverage_percentage", 0)
        },
        
        "ttl": int((datetime.now() + timedelta(days=30)).timestamp())
    }
    audit_records.append(workflow_record)
    
    # 2. Node-level records
    for i, decision in enumerate(state["decisions"]):
        node_record = {
            "pk": f"AUDIT#{datetime.now().strftime('%Y-%m-%d')}",
            "sk": f"NODE#{state['receipt_id']}#{i:03d}#{decision['node']}",
            "gsi1pk": f"NODE_TYPE#{decision['node']}",
            
            "node_name": decision["node"],
            "timestamp": decision["timestamp"],
            "decisions": decision,
            
            "ttl": int((datetime.now() + timedelta(days=30)).timestamp())
        }
        audit_records.append(node_record)
    
    # 3. GPT usage record (if used)
    if state.get("gpt_responses"):
        for gpt_resp in state["gpt_responses"]:
            gpt_record = {
                "pk": f"GPT_USAGE#{datetime.now().strftime('%Y-%m-%d')}",
                "sk": f"CALL#{gpt_resp['timestamp']}#{state['receipt_id']}",
                "gsi1pk": f"MERCHANT#{state.get('merchant_name', 'unknown')}",
                
                "trigger": {
                    "missing_essentials": state.get("missing_essentials", []),
                    "coverage_before": state.get("coverage_percentage", 0),
                    "decision": state.get("decision_outcome", "UNKNOWN")
                },
                
                "response": gpt_resp,
                "validation_passed": state["validation_results"].get("passed", False),
                
                "ttl": int((datetime.now() + timedelta(days=90)).timestamp())
            }
            audit_records.append(gpt_record)
    
    # Batch write to DynamoDB
    # TODO: Implement actual batch write
    state["audit_records"] = audit_records
    
    return state
```

## Benefits

1. **Cost Tracking**: Know exactly where money is spent
2. **Pattern Learning**: Identify what works for each merchant
3. **Debugging**: Complete trail for any receipt
4. **Optimization**: Find opportunities to skip GPT
5. **Compliance**: Audit trail for decisions made

## Cost Management

- 30-day TTL on workflow records (~$0.00001/receipt)
- 90-day TTL on GPT usage (learn from expensive calls)
- No TTL on pattern stats (valuable learning data)
- Total storage: ~1KB per receipt = $0.25 per million receipts

This audit trail pays for itself by helping optimize the 94.4% skip rate even higher!