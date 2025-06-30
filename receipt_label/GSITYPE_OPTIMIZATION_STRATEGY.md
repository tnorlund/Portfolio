# GSITYPE Index Optimization Strategy

## Overview

This document analyzes the current GSITYPE index usage patterns across all entities in the receipt_dynamo package and proposes a composite approach to eliminate remaining scan operations in the cost monitoring system.

## Current GSITYPE Usage Analysis

### Entity TYPE Field Patterns

All entities in the receipt_dynamo package use a consistent `TYPE` field pattern:

| Entity | TYPE Value | Purpose |
|--------|------------|---------|
| AIUsageMetric | `"AIUsageMetric"` | AI service usage tracking |
| Receipt | `"RECEIPT"` | Receipt document records |
| ReceiptWord | `"RECEIPT_WORD"` | Individual word entities |
| ReceiptMetadata | `"RECEIPT_METADATA"` | Receipt metadata |
| BatchSummary | `"BATCH_SUMMARY"` | ML batch processing jobs |
| ReceiptLine | `"RECEIPT_LINE"` | Receipt line items |
| ReceiptLetter | `"RECEIPT_LETTER"` | Character-level entities |

### Current GSI Utilization Status

| GSI | Current Usage | Efficiency |
|-----|---------------|------------|
| **GSI1** | ✅ Service/date queries (`AI_USAGE#{service}` / `DATE#{date}`) | High |
| **GSI2** | ✅ Cost aggregation (`AI_USAGE_COST` / `COST#{date}#{service}`) | High |
| **GSI3** | ✅ Job/batch queries (`JOB#{job_id}` / `AI_USAGE#{timestamp}`) | Medium |
| **GSITYPE** | ❌ **Underutilized** - Only has TYPE field | **Low** |

### Current Scan Operations (Problems to Solve)

1. **User scope queries**: `scope:user:{user_id}` - No efficient query path
2. **Environment scope queries**: `scope:environment:{env}` - No efficient query path

## Revised Strategy: Composite Keys with Existing GSIs

**Key Insight**: We can eliminate scans using composite keys within existing GSIs without adding new infrastructure.

### Option 1: Enhanced GSI3 Composite Keys (Recommended)

Extend the existing GSI3 to support user and environment scopes using a priority hierarchy:

#### **Enhanced GSI3 Design**
```
GSI3 Index (Enhanced):
- PK (GSI3PK): "JOB#{job_id}" | "USER#{user_id}" | "BATCH#{batch_id}" | "ENV#{environment}"
- SK (GSI3SK): "AI_USAGE#{timestamp}"
```

#### **Priority Hierarchy Implementation**
```python
@property
def gsi3pk(self) -> Optional[str]:
    """Enhanced GSI3 PK with priority hierarchy for scope-based queries."""
    # Priority: job_id > user_id > batch_id > environment
    if self.job_id:
        return f"JOB#{self.job_id}"
    elif self.user_id:
        return f"USER#{self.user_id}"  # NEW: User scope support
    elif self.batch_id:
        return f"BATCH#{self.batch_id}"
    elif self.environment:
        return f"ENV#{self.environment}"  # NEW: Environment scope support
    return None

@property  
def gsi3sk(self) -> Optional[str]:
    """GSI3 SK for temporal ordering."""
    if self.job_id or self.user_id or self.batch_id or self.environment:
        return f"AI_USAGE#{self.timestamp.isoformat()}"
    return None
```

#### **Query Examples**
```python
# User scope query using enhanced GSI3
query_params = {
    "IndexName": "GSI3",
    "KeyConditionExpression": "GSI3PK = :pk AND GSI3SK BETWEEN :start AND :end",
    "ExpressionAttributeValues": {
        ":pk": {"S": "USER#john_doe"},
        ":start": {"S": "AI_USAGE#2024-01-01T00:00:00Z"},
        ":end": {"S": "AI_USAGE#2024-01-31T23:59:59Z"}
    }
}

# Environment scope query using enhanced GSI3
query_params = {
    "IndexName": "GSI3",
    "KeyConditionExpression": "GSI3PK = :pk AND GSI3SK BETWEEN :start AND :end",
    "ExpressionAttributeValues": {
        ":pk": {"S": "ENV#production"},
        ":start": {"S": "AI_USAGE#2024-01-01T00:00:00Z"},
        ":end": {"S": "AI_USAGE#2024-01-31T23:59:59Z"}
    }
}
```

#### **Query Examples**
```python
# Query all AI usage for user "john_doe" in date range
query_params = {
    "IndexName": "GSITYPE",
    "KeyConditionExpression": "GSITYPEPK = :pk AND GSITYPESK BETWEEN :start AND :end",
    "ExpressionAttributeValues": {
        ":pk": {"S": "SCOPE#USER#john_doe#AIUsageMetric"},
        ":start": {"S": "DATE#2024-01-01#"},
        ":end": {"S": "DATE#2024-01-31#"}
    }
}

# Query all AI usage in production environment
query_params = {
    "IndexName": "GSITYPE", 
    "KeyConditionExpression": "GSITYPEPK = :pk AND GSITYPESK BETWEEN :start AND :end",
    "ExpressionAttributeValues": {
        ":pk": {"S": "SCOPE#ENV#production#AIUsageMetric"},
        ":start": {"S": "DATE#2024-01-01#"},
        ":end": {"S": "DATE#2024-01-31#"}
    }
}
```

### Option 2: Multiple Scope Items (Advanced)

Store multiple GSITYPE entries per AIUsageMetric to support all scope queries:

```python
def to_dynamodb_items(self) -> List[Dict]:
    """Generate multiple items for different scope access patterns."""
    items = [self.to_dynamodb_item()]  # Primary item
    
    # Add scope-specific index items
    scope_items = []
    if self.user_id:
        scope_items.append(self._create_scope_item("USER", self.user_id))
    if self.environment:
        scope_items.append(self._create_scope_item("ENV", self.environment))
    if self.job_id:
        scope_items.append(self._create_scope_item("JOB", self.job_id))
        
    return items + scope_items

def _create_scope_item(self, scope_type: str, scope_value: str) -> Dict:
    """Create a scope-specific index item."""
    return {
        "PK": {"S": f"SCOPE#{scope_type}#{scope_value}"},
        "SK": {"S": f"METRIC#{self.timestamp.isoformat()}#{self.request_id}"},
        "GSITYPEPK": {"S": f"SCOPE#{scope_type}#{scope_value}#{self.item_type}"},
        "GSITYPESK": {"S": f"DATE#{self.date}#{self.timestamp.isoformat()}"},
        "TYPE": {"S": f"{self.item_type}_SCOPE_INDEX"},
        "source_pk": {"S": self.pk},
        "source_sk": {"S": self.sk},
        "cost_usd": {"N": str(self.cost_usd)} if self.cost_usd else {"NULL": True},
        # Include other frequently queried fields
    }
```

## Implementation Roadmap

### Phase 1: GSITYPE Enhancement (Immediate)
1. **Update AIUsageMetric** to generate GSITYPE keys for user/environment scopes
2. **Add query methods** to CostMonitor for GSITYPE-based queries
3. **Migrate CostMonitor** to use GSITYPE instead of scans for user/environment
4. **Backfill existing records** with GSITYPE keys

### Phase 2: GSI Consolidation (Future)
1. **Evaluate GSI3 vs GSITYPE** overlap for job queries
2. **Consider consolidating** job queries into GSITYPE
3. **Optimize query routing** based on scope type

### Phase 3: Cross-Entity Optimization (Long-term)
1. **Extend GSITYPE pattern** to other entities (Receipt, ReceiptWord, etc.)
2. **Implement unified scope queries** across entity types
3. **Create entity-agnostic** querying interfaces

## Cost-Benefit Analysis

### Benefits
- **Eliminate remaining scans**: User and environment queries become O(log n)
- **Unified query interface**: Single pattern for all scope-based queries
- **Better utilization**: Transform underused GSITYPE into high-value index
- **Scalability**: Performance remains consistent as data grows

### Costs
- **Storage overhead**: Additional GSITYPE keys for each applicable record
- **Write amplification**: Each AIUsageMetric write updates multiple GSI entries
- **Migration effort**: Backfill existing records with GSITYPE keys
- **Complexity**: More sophisticated key generation and query logic

### Cost Estimation
- **Current cost**: Expensive scan operations on large tables
- **New cost**: Predictable GSI query costs + modest storage increase
- **Break-even point**: When table size > ~10,000 AIUsageMetric records
- **ROI**: Significant performance improvement with moderate cost increase

## Implementation Code Examples

### Enhanced CostMonitor Query Method
```python
def _query_by_scope_gsitype(
    self,
    scope_type: str,
    scope_value: str, 
    start_date: str,
    end_date: str,
    service: Optional[str] = None,
) -> List[AIUsageMetric]:
    """Query metrics using GSITYPE for user/environment scopes."""
    
    key_condition = "GSITYPEPK = :pk AND GSITYPESK BETWEEN :start AND :end"
    expression_values = {
        ":pk": {"S": f"SCOPE#{scope_type.upper()}#{scope_value}#AIUsageMetric"},
        ":start": {"S": f"DATE#{start_date}#"},
        ":end": {"S": f"DATE#{end_date}#~"}  # ~ sorts after all timestamps
    }
    
    query_params = {
        "TableName": self.dynamo_client.table_name,
        "IndexName": "GSITYPE", 
        "KeyConditionExpression": key_condition,
        "ExpressionAttributeValues": expression_values
    }
    
    if service:
        query_params["FilterExpression"] = "#service = :service"
        query_params["ExpressionAttributeNames"] = {"#service": "service"}
        expression_values[":service"] = {"S": service}
    
    response = self.dynamo_client._client.query(**query_params)
    
    # Convert to AIUsageMetric objects
    metrics = []
    for item in response.get("Items", []):
        metric = self._dynamodb_item_to_metric(item)
        if metric:
            metrics.append(metric)
    
    return metrics
```

### Updated CostMonitor _query_metrics Method
```python
def _query_metrics(self, scope_type: str, scope_value: str, start_date: str, end_date: str, service: Optional[str] = None) -> List[AIUsageMetric]:
    """Query metrics using the most efficient index for each scope type."""
    
    if scope_type == "service":
        # Use GSI1 (most efficient for service queries)
        return AIUsageMetric.query_by_service_date(
            self.dynamo_client, service=scope_value, start_date=start_date, end_date=end_date
        )
    elif scope_type in ["user", "environment"]: 
        # Use GSITYPE (eliminates scans)
        return self._query_by_scope_gsitype(scope_type, scope_value, start_date, end_date, service)
    elif scope_type == "job":
        # Use GSI3 (existing optimization)
        return self._query_by_job_gsi3(scope_value, start_date, end_date, service)
    elif scope_type == "global":
        # Aggregate across all services
        return self._query_global_metrics(start_date, end_date, service)
    else:
        logger.warning(f"Unsupported scope type: {scope_type}")
        return []
```

## Migration Strategy

### Step 1: Gradual Rollout
1. **Add GSITYPE key generation** to new AIUsageMetric records
2. **Implement GSITYPE queries** in CostMonitor with scan fallback
3. **Test with recent data** that has GSITYPE keys

### Step 2: Backfill Historical Data
1. **Create migration script** to scan existing records and add GSITYPE keys
2. **Process in batches** to avoid overwhelming DynamoDB throughput
3. **Monitor progress** and handle errors gracefully

### Step 3: Complete Migration
1. **Verify GSITYPE coverage** reaches acceptable threshold (e.g., 95%)
2. **Remove scan fallback** from CostMonitor
3. **Monitor performance improvements** and cost changes

## Conclusion

The composite GSITYPE approach offers a cost-effective way to eliminate remaining scan operations while maximizing the value of existing infrastructure. By transforming an underutilized index into a powerful multi-dimensional query interface, we can achieve significant performance improvements with minimal additional AWS costs.

The strategy provides a clear upgrade path from expensive scan operations to efficient indexed queries, supporting the cost monitoring system's scalability requirements as AI usage tracking data continues to grow.