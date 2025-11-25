# Access Patterns for receipt_agent

This document reviews the access patterns for DynamoDB, ChromaDB, and Google Places API used by the `receipt_agent` package.

## Overview

The `receipt_agent` validation workflow accesses three main data sources:

| Data Source | Purpose | Cost Model |
|-------------|---------|------------|
| **DynamoDB** | Receipt metadata, lines, words, labels, Places cache | Per-request + storage |
| **ChromaDB** | Vector similarity search for receipts | Local compute (EFS) |
| **Google Places API** | Merchant verification | Per-API call ($$$) |

---

## 1. DynamoDB Access Patterns

### 1.1 Receipt Metadata Operations

#### `getReceiptMetadata(image_id, receipt_id)`
- **Purpose**: Load current metadata for a receipt
- **Access Pattern**: Direct get by primary key
- **Keys**: `PK=IMAGE#<image_id>`, `SK=RECEIPT#<receipt_id:05d>#METADATA`
- **Frequency**: Once per validation
- **Cost**: 1 RCU

#### `get_receipt_details(image_id, receipt_id)`
- **Purpose**: Load receipt context (lines, words, labels)
- **Access Pattern**: Query by partition key
- **Keys**: `PK=IMAGE#<image_id>`, `SK begins_with RECEIPT#<receipt_id:05d>`
- **Frequency**: Once per validation
- **Cost**: ~1-10 RCU depending on receipt size

#### `get_receipt_metadatas_by_merchant(merchant_name, limit)`
- **Purpose**: Find other receipts from same merchant for consistency check
- **Access Pattern**: GSI1 query
- **Keys**: `GSI1PK=MERCHANT#<normalized_name>`, `GSI1SK begins_with IMAGE#`
- **Frequency**: Once per validation
- **Cost**: ~1-5 RCU

### 1.2 Places Cache Operations (Cost Optimization)

The `PlacesCache` entity stores Google Places API responses to minimize API costs.

#### Cache Schema

```
PK: PLACES#<search_type>     (ADDRESS, PHONE, URL)
SK: VALUE#<padded_value>     (normalized search value)
GSI1: PLACE_ID -> PLACE_ID#<place_id>
GSI2: LAST_USED -> <timestamp>
TTL: time_to_live (30 days)
```

#### `get_places_cache(search_type, search_value)`
- **Purpose**: Check cache before API call
- **Access Pattern**: Direct get by primary key
- **Example**: `PK=PLACES#PHONE`, `SK=VALUE#5551234567`
- **Frequency**: Every Places lookup (address, phone)
- **Cost**: 1 RCU

#### `add_places_cache(item)`
- **Purpose**: Cache new API response
- **Access Pattern**: Put with condition
- **Frequency**: Only on cache miss
- **Cost**: 1 WCU

#### `increment_query_count(item)`
- **Purpose**: Track cache usage for analytics
- **Access Pattern**: Update expression
- **Frequency**: Every cache hit
- **Cost**: 1 WCU

### 1.3 DynamoDB Access Summary

| Operation | Pattern | Index | Frequency per Validation |
|-----------|---------|-------|--------------------------|
| Get metadata | GetItem | Base | 1 |
| Get receipt details | Query | Base | 1 |
| Get merchant receipts | Query | GSI1 | 1 |
| Check Places cache | GetItem | Base | 0-3 |
| Write Places cache | PutItem | Base | 0-3 (on miss) |
| Update cache stats | UpdateItem | Base | 0-3 (on hit) |

**Estimated cost per validation**: 5-15 RCU, 0-6 WCU

---

## 2. ChromaDB Access Patterns

ChromaDB stores receipt embeddings for similarity search. Currently deployed on EFS.

### 2.1 Collections

| Collection | Content | Embedding Dimension |
|------------|---------|---------------------|
| `lines` | Receipt line text | 1536 (OpenAI) |
| `words` | Individual words | 1536 (OpenAI) |

### 2.2 Query Operations

#### `query(collection, query_embeddings, n_results, where, include)`
- **Purpose**: Find similar receipts by embedding
- **Usage in Agent**:
  - Search by address line embedding
  - Search by phone line embedding
  - Search by merchant name embedding
- **Metadata Returned**: `image_id`, `receipt_id`, `line_id`, `merchant_name`, `normalized_phone_10`, `normalized_full_address`, `place_id`
- **Frequency**: 2-5 queries per validation

#### `get(collection, where, include)`
- **Purpose**: Retrieve documents by filter
- **Usage in Agent**: `search_by_place_id` to find all receipts with a place_id
- **Frequency**: 0-1 per validation

### 2.3 ChromaDB Metadata Schema (Lines)

```python
{
    "image_id": str,           # Receipt image UUID
    "receipt_id": int,         # Receipt ID within image
    "line_id": int,            # Line ID within receipt
    "merchant_name": str,      # Merchant name (if known)
    "normalized_phone_10": str,     # Normalized 10-digit phone
    "normalized_full_address": str, # Normalized address
    "place_id": str,           # Google Place ID (if matched)
    "section_label": str,      # Receipt section (header, items, totals)
}
```

### 2.4 ChromaDB Access Summary

| Operation | Purpose | Frequency per Validation |
|-----------|---------|--------------------------|
| Query lines by address | Find similar addresses | 0-1 |
| Query lines by phone | Find matching phones | 0-1 |
| Query lines by merchant | Find same merchant | 0-1 |
| Get by place_id | Find all for place_id | 0-1 |

**Total queries per validation**: 2-5

**Cost**: No per-query cost (EFS storage cost only)

---

## 3. Google Places API Access Patterns

### 3.1 API Endpoints Used

| Endpoint | Cost (per 1000) | Cached? |
|----------|-----------------|---------|
| Place Details | $17 | ❌ (by place_id) |
| Find Place (Phone) | $17 | ✅ PHONE cache |
| Find Place (Address) | $17 | ✅ ADDRESS cache |
| Text Search | $32 | ❌ |
| Autocomplete | $2.83 | ❌ |
| Nearby Search | $32 | ✅ ADDRESS cache |

### 3.2 Search Methods (Priority Order)

1. **`get_place_details(place_id)`**
   - Direct lookup, no cache needed
   - Cost: $0.017 per call

2. **`search_by_phone(phone_number)`**
   - Uses `PHONE` cache in DynamoDB
   - Cache key: digits-only phone number
   - On miss: API call + cache write
   - Cost: $0 (cache hit) or $0.017 (cache miss)

3. **`search_by_address(address)`**
   - Uses `ADDRESS` cache in DynamoDB
   - Skips cache for area searches (city-only)
   - Skips cache for route-level results
   - Cost: $0 (cache hit) or $0.017-$0.064 (cache miss, may chain calls)

4. **`search_by_text(query)`**
   - NOT cached (queries are too variable)
   - Cost: $0.032 per call
   - Used as fallback only

### 3.3 Cache Hit Rates

Based on typical usage patterns:

| Search Type | Expected Cache Hit Rate |
|-------------|-------------------------|
| Phone | 70-90% (phones are consistent) |
| Address | 40-60% (addresses vary in format) |
| Text Search | 0% (not cached) |

### 3.4 Cost Optimization Strategy

The `PlacesAPI` class implements these cost optimizations:

1. **Cache-first approach**: Always check DynamoDB cache before API call
2. **Normalized keys**: Phones are normalized to digits-only for better cache hits
3. **Smart cache exclusions**:
   - Skip caching area searches (city names without addresses)
   - Skip caching route-level results (street without building number)
   - Skip caching results without street numbers
4. **30-day TTL**: Automatic cache invalidation
5. **Query count tracking**: Monitor cache effectiveness

### 3.5 Places API Access Summary

| Scenario | API Calls | Cost |
|----------|-----------|------|
| All cache hits | 0 | $0 |
| Phone miss, address hit | 1 | $0.017 |
| Both miss | 2-3 | $0.034-$0.051 |
| Fallback to text search | +1 | +$0.032 |

**Estimated cost per validation**: $0-$0.083 (usually $0-$0.034 with good cache)

---

## 4. Validation Workflow Access Pattern

The `receipt_agent` validation workflow follows this access pattern:

```
1. load_metadata
   └── DynamoDB: getReceiptMetadata (1 RCU)
   └── DynamoDB: get_receipt_details (1-10 RCU)

2. search_similar_receipts
   └── ChromaDB: query lines by address (1 query)
   └── ChromaDB: query lines by phone (1 query)
   └── ChromaDB: query lines by merchant (1 query)

3. verify_consistency
   └── DynamoDB: get_receipt_metadatas_by_merchant (1-5 RCU)

4. check_google_places (optional)
   └── DynamoDB: get_places_cache PHONE (1 RCU)
   └── [If miss] Places API: search_by_phone ($0.017)
   └── [If miss] DynamoDB: add_places_cache (1 WCU)
   └── DynamoDB: get_places_cache ADDRESS (1 RCU)
   └── [If miss] Places API: search_by_address ($0.017)
   └── [If miss] DynamoDB: add_places_cache (1 WCU)

5. make_decision
   └── Ollama Cloud: LLM inference (per-token)
```

---

## 5. Cost Estimates

### Per-Validation Costs

| Resource | Min | Max | Notes |
|----------|-----|-----|-------|
| DynamoDB RCU | 5 | 20 | ~$0.00001 |
| DynamoDB WCU | 0 | 6 | ~$0.000005 |
| ChromaDB | 0 | 0 | Local storage only |
| Places API (cached) | $0 | $0.034 | With good cache |
| Places API (uncached) | $0.034 | $0.083 | Cold start |
| Ollama Cloud | ~$0.001 | ~$0.01 | Per-token |

### Batch Validation (100 receipts)

| Scenario | DynamoDB | Places API | Total |
|----------|----------|------------|-------|
| All cached | $0.01 | $0 | $0.01 |
| 70% cache hit | $0.02 | $1.02 | $1.04 |
| 50% cache hit | $0.03 | $1.70 | $1.73 |
| Cold cache | $0.05 | $3.40 | $3.45 |

---

## 6. Recommendations

### 6.1 Minimize Places API Costs

1. **Always use PlacesAPI from receipt_label** - it has built-in caching
2. **Normalize phone numbers** before search (digits-only)
3. **Skip Places verification** if ChromaDB confidence is high (>0.8)
4. **Batch similar receipts** to leverage cache warming

### 6.2 Optimize DynamoDB Access

1. **Use GSI queries** instead of scans
2. **Limit merchant queries** to reasonable bounds (50 receipts max)
3. **Project only needed attributes** when possible

### 6.3 Optimize ChromaDB Access

1. **Batch queries** when validating multiple receipts
2. **Use metadata filters** to reduce result set
3. **Cache query results** in memory for repeated access

---

## 7. Monitoring

### Key Metrics to Track

1. **Places Cache Hit Rate**: `cache_hits / total_lookups`
2. **Places API Costs**: Monthly spend by endpoint
3. **DynamoDB RCU/WCU**: Per-operation costs
4. **Validation Latency**: Time per validation step
5. **ChromaDB Query Latency**: Time per similarity search

### LangSmith Tracing

The `receipt_agent` package integrates with LangSmith for full observability:

```python
# All validation runs are traced
@traceable(name="validate_metadata")
async def validate(self, image_id, receipt_id):
    ...
```

View traces at: https://smith.langchain.com/o/default/projects/p/receipt-agent

