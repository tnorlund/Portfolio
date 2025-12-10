# Metadata Agents Review: receipt_agent Package Structure

## Overview

The `receipt_agent` package uses LLM agents (via LangGraph) to intelligently find, validate, and harmonize receipt metadata. It produces four key metadata values:
- **place_id**: Google Place ID
- **merchant_name**: Business name
- **address**: Formatted address
- **phone_number**: Phone number

## Package Structure

```
receipt_agent/
├── agent/                    # Agent classes
│   ├── metadata_validator.py  # Validates existing metadata
│   └── combination_selector.py
├── graph/                     # LangGraph workflows (main agents)
│   ├── harmonizer_workflow.py           # Harmonizes metadata within place_id groups
│   ├── receipt_metadata_finder_workflow.py  # Finds missing metadata for receipts
│   ├── label_validation_workflow.py     # Validates word labels
│   ├── cove_text_consistency_workflow.py # Verifies text consistency
│   └── ...
├── tools/                     # Tools available to agents
│   ├── agentic.py             # Core agentic tools (ChromaDB, DynamoDB, Places)
│   ├── harmonizer_v3.py        # Harmonizer tool wrapper
│   ├── receipt_metadata_finder.py  # Metadata finder tool wrapper
│   └── ...
├── clients/                   # Client factories
│   └── factory.py             # Creates DynamoDB, ChromaDB, Places, embedding clients
├── config/                    # Configuration
│   └── settings.py            # Settings from environment variables
└── state/                     # State models
    └── models.py              # Pydantic models for agent state
```

## Main Agents/Workflows

### 1. Receipt Metadata Finder Agent
**Purpose**: Finds ALL missing metadata for a receipt (place_id, merchant_name, address, phone_number)

**Location**: `graph/receipt_metadata_finder_workflow.py`

**How it works**:
1. **Examines receipt content**: Uses tools to get receipt lines, words, and labels
2. **Extracts from receipt**: Looks for MERCHANT_NAME, ADDRESS, PHONE labels in words
3. **Searches Google Places**: Uses phone → address → merchant_name search priority
4. **Uses similarity search**: Finds similar receipts via ChromaDB to verify findings
5. **Submits metadata**: Returns all found fields with confidence scores

**Key Tools Used**:
- `get_my_metadata`, `get_my_lines`, `get_my_words`: Context about current receipt
- `verify_with_google_places`: Search Google Places API
- `find_similar_to_my_line`, `search_lines`: Similarity search via ChromaDB
- `get_merchant_consensus`: Get canonical data from other receipts
- `submit_metadata`: Submit findings (REQUIRED)

**Entry Point**: `run_receipt_metadata_finder()` in `receipt_metadata_finder_workflow.py`

**Used By**:
- `ReceiptMetadataFinder` class (tool wrapper)
- Harmonizer agent (as sub-agent via `find_correct_metadata` tool)

### 2. Harmonizer Agent
**Purpose**: Ensures metadata consistency across receipts sharing the same place_id

**Location**: `graph/harmonizer_workflow.py`

**How it works**:
1. **Groups receipts by place_id**: All receipts with same place_id should have identical metadata
2. **Analyzes group**: Gets summary of all receipts and their metadata variations
3. **Validates with Google Places**: Gets official data from Google Places (source of truth)
4. **Verifies addresses match receipt text**: Critical check to catch wrong place_id assignments
5. **Finds correct metadata**: Uses metadata finder sub-agent if receipts have wrong metadata
6. **Verifies text consistency**: Uses CoVe (Consistency Verification) to check for outliers
7. **Submits harmonization**: Determines canonical values and which receipts need updates

**Key Tools Used**:
- `get_group_summary`: See all receipts in the place_id group
- `verify_place_id`: Get official Google Places data
- `verify_address_on_receipt`: Verify address matches receipt text
- `find_correct_metadata`: Sub-agent to find correct metadata for wrong receipts
- `verify_text_consistency`: CoVe sub-agent to check text consistency
- `submit_harmonization`: Submit canonical values (REQUIRED)

**Entry Point**: `run_harmonizer_agent()` in `harmonizer_workflow.py`

**Used By**:
- `MerchantHarmonizerV3` class (tool wrapper)
- Lambda handler in `infra/metadata_harmonizer_step_functions/lambdas/harmonize_metadata.py`

### 3. CoVe Text Consistency Agent
**Purpose**: Verifies that all receipts in a place_id group actually contain text consistent with canonical metadata

**Location**: `graph/cove_text_consistency_workflow.py`

**How it works**:
1. **Checks each receipt**: Compares receipt text against canonical metadata
2. **Identifies outliers**: Finds receipts that may be from a different place
3. **Returns verdicts**: MATCH, MISMATCH, or UNSURE for each receipt

**Used By**: Harmonizer agent (via `verify_text_consistency` tool)

## How Metadata is Produced

### Flow 1: Finding Missing Metadata (Metadata Finder)

```
Receipt (missing metadata)
    ↓
Metadata Finder Agent
    ↓
1. Get receipt content (lines, words, labels)
    ↓
2. Extract metadata from receipt labels
    ↓
3. Search Google Places (phone → address → name)
    ↓
4. Verify with similar receipts (ChromaDB)
    ↓
5. Submit metadata (place_id, merchant_name, address, phone)
    ↓
ReceiptMetadata updated in DynamoDB
```

**Example**:
- Receipt has phone number "805-555-1234" but missing place_id
- Agent extracts phone from receipt
- Searches Google Places by phone → finds place_id "ChIJ..."
- Also gets merchant_name, address from Google Places
- Submits all fields with confidence scores

### Flow 2: Harmonizing Inconsistent Metadata (Harmonizer)

```
Place ID Group (receipts with same place_id but different metadata)
    ↓
Harmonizer Agent
    ↓
1. Get group summary (all receipts and their metadata)
    ↓
2. Verify with Google Places (source of truth)
    ↓
3. Verify addresses match receipt text (catch wrong place_ids)
    ↓
4. If wrong place_id → use Metadata Finder sub-agent
    ↓
5. Verify text consistency (CoVe sub-agent)
    ↓
6. Submit canonical values
    ↓
Receipts updated to match canonical values
```

**Example**:
- 5 receipts share place_id "ChIJ..." but have different merchant names:
  - 3 receipts: "Starbucks"
  - 2 receipts: "STARBUCKS" (OCR error)
- Agent sees Google Places says "Starbucks"
- Determines canonical merchant_name = "Starbucks"
- Submits harmonization: 2 receipts need merchant_name update

### Flow 3: Lambda Processing (Production)

```
Step Functions → Lambda Handler
    ↓
harmonize_metadata.py handler
    ↓
1. Load receipts for place_ids (efficient GSI query)
    ↓
2. Group by place_id
    ↓
3. For each inconsistent group:
    - Create harmonizer graph
    - Run harmonizer agent
    - Get canonical values
    ↓
4. Apply fixes (update DynamoDB if not dry_run)
    ↓
Return results
```

## Key Tools Available to Agents

### Context Tools (understand current receipt)
- `get_my_metadata`: Current metadata from DynamoDB
- `get_my_lines`: All text lines on receipt
- `get_my_words`: Labeled words (MERCHANT_NAME, ADDRESS, PHONE, etc.)
- `get_receipt_text`: Formatted receipt text (human-readable)

### Similarity Search Tools (find matching receipts)
- `find_similar_to_my_line`: Use receipt's line embeddings to find similar lines
- `find_similar_to_my_word`: Use receipt's word embeddings to find similar words
- `search_lines`: Search by arbitrary text (address, phone, merchant name)
- `search_words`: Search for specific labeled words

### Aggregation Tools (understand consensus)
- `get_merchant_consensus`: Get canonical data for a merchant based on all receipts
- `get_place_id_info`: Get all receipts using a specific Place ID

### Google Places Tools (source of truth)
- `verify_with_google_places`: Search Google Places API (phone → address → name priority)
- `find_businesses_at_address`: Find businesses at a specific address (when Google returns address as name)

### Decision Tools
- `submit_metadata`: Submit found metadata (metadata finder)
- `submit_harmonization`: Submit canonical values (harmonizer)

## Data Sources Priority

For each metadata field, agents use this priority:

1. **Receipt content** (labels, lines) - Most reliable, comes from receipt itself
2. **Google Places** - Official source, use for validation and missing fields
3. **Similar receipts** (ChromaDB) - Verification only, may be wrong

## Integration Points

### DynamoDB
- **Reads**: Receipt metadata, receipt details (lines, words), receipt entities
- **Writes**: Receipt metadata updates (merchant_name, address, phone_number, place_id)

### ChromaDB
- **Reads**: Line embeddings, word embeddings for similarity search
- **Collections**: "lines", "words"
- **Lazy Loading**: Can download from S3 bucket on-demand (for Lambda)

### Google Places API
- **Reads**: Place details, search by phone/address/name
- **Caching**: Uses DynamoDB PlacesCache (30-day TTL) to minimize costs
- **Priority**: Phone → Address → Name (phone is most reliable)

## Configuration

All configuration via environment variables (prefix: `RECEIPT_AGENT_`):

```bash
# LLM (Ollama Cloud)
RECEIPT_AGENT_OLLAMA_BASE_URL=https://ollama.com
RECEIPT_AGENT_OLLAMA_API_KEY=...
RECEIPT_AGENT_OLLAMA_MODEL=gpt-oss:120b-cloud

# Embeddings (OpenAI)
RECEIPT_AGENT_OPENAI_API_KEY=...
RECEIPT_AGENT_EMBEDDING_MODEL=text-embedding-3-small

# ChromaDB
RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY=/path/to/chroma
CHROMADB_BUCKET=s3://bucket/chromadb  # For lazy loading

# DynamoDB
RECEIPT_AGENT_DYNAMO_TABLE_NAME=receipts

# Google Places (optional)
GOOGLE_PLACES_API_KEY=...
```

## Key Design Decisions

1. **Agent-based reasoning**: Uses LLM agents instead of simple rules to handle edge cases
2. **Sub-agents**: Harmonizer can spin up Metadata Finder and CoVe as sub-agents
3. **Lazy loading**: ChromaDB can be downloaded from S3 on-demand (for Lambda)
4. **Google Places as source of truth**: Always validates against Google Places
5. **Address verification**: Critical check to catch wrong place_id assignments
6. **Text consistency**: CoVe verifies receipts actually belong to same place

## Example Usage

### Finding Missing Metadata

```python
from receipt_agent.graph.receipt_metadata_finder_workflow import (
    create_receipt_metadata_finder_graph,
    run_receipt_metadata_finder,
)

# Create graph
graph, state_holder = create_receipt_metadata_finder_graph(
    dynamo_client=dynamo,
    chroma_client=chroma,
    embed_fn=embed_fn,
    places_api=places,
)

# Run agent
result = await run_receipt_metadata_finder(
    graph=graph,
    state_holder=state_holder,
    image_id="abc-123",
    receipt_id=1,
)

# Result contains:
# - place_id: "ChIJ..."
# - merchant_name: "Starbucks"
# - address: "123 Main St..."
# - phone_number: "805-555-1234"
# - confidence: 0.95
# - fields_found: ["place_id", "merchant_name", "address", "phone_number"]
```

### Harmonizing Metadata

```python
from receipt_agent.graph.harmonizer_workflow import (
    create_harmonizer_graph,
    run_harmonizer_agent,
)

# Create graph
graph, state_holder = create_harmonizer_graph(
    dynamo_client=dynamo,
    places_api=places,
)

# Run agent
result = await run_harmonizer_agent(
    graph=graph,
    state_holder=state_holder,
    place_id="ChIJ...",
    receipts=[
        {"image_id": "abc-123", "receipt_id": 1, "merchant_name": "Starbucks", ...},
        {"image_id": "def-456", "receipt_id": 1, "merchant_name": "STARBUCKS", ...},
    ],
)

# Result contains:
# - canonical_merchant_name: "Starbucks"
# - canonical_address: "123 Main St..."
# - canonical_phone: "805-555-1234"
# - receipts_needing_update: 2
# - updates: [{"image_id": "def-456", "receipt_id": 1, "changes": [...]}]
```

## Summary

The `receipt_agent` package uses three main LLM agents to produce metadata:

1. **Metadata Finder**: Finds missing metadata by examining receipts, searching Google Places, and using similarity search
2. **Harmonizer**: Ensures consistency across receipts sharing the same place_id by validating with Google Places and fixing inconsistencies
3. **CoVe**: Verifies text consistency to catch receipts that don't belong to the same place

All agents use LangGraph workflows with tools that provide access to DynamoDB, ChromaDB, and Google Places API. The agents reason about edge cases (OCR errors, address-like names, wrong place_ids) and produce metadata with confidence scores.
