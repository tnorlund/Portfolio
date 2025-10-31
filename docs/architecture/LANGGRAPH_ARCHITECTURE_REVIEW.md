# LangGraph Architecture Review

## Overview

This document provides a comprehensive review of how LangGraph is used in this project for receipt labeling and currency validation.

## Architecture Summary

LangGraph is used specifically for **currency validation** in the `receipt_label` module. It does NOT appear to be used for merchant validation (which uses a different agent-based approach).

## LangGraph Structure in receipt_label

### Location
- **Main implementation**: `receipt_label/receipt_label/langchain/currency_validation.py`
- **State model**: `receipt_label/receipt_label/langchain/state/currency_validation.py`
- **Nodes**: `receipt_label/receipt_label/langchain/nodes/`

### Workflow Graph

The LangGraph workflow uses a **two-phase parallel analysis** pattern:

```
START → load_data → phase1_currency → [dispatch N parallel phase2 nodes] → combine_results → END
```

#### Graph Definition (`create_unified_analysis_graph`)

```python
# Nodes:
1. load_data: Loads receipt lines and words from DynamoDB
2. phase1_currency: Analyzes currency amounts (GRAND_TOTAL, TAX, SUBTOTAL, LINE_TOTAL)
3. phase2_line_analysis: Analyzes line item components (PRODUCT_NAME, QUANTITY, UNIT_PRICE)
4. combine_results: Merges results and creates ReceiptWordLabels

# Flow:
1. START → load_data
2. load_data → phase1_currency
3. phase1_currency → (conditional dispatch) → phase2_line_analysis (N parallel instances)
4. phase2_line_analysis → combine_results
5. combine_results → END
```

### State Model (CurrencyAnalysisState)

**Inputs:**
- `receipt_id`: Receipt identifier
- `image_id`: Image identifier  
- `lines`: List of ReceiptLine objects
- `words`: List of ReceiptWord objects
- `dynamo_client`: DynamoDB client instance

**Intermediate Results:**
- `currency_labels`: List of CurrencyLabel from Phase 1
- `line_item_labels`: List of LineItemLabel from Phase 2 (with reducer for parallel nodes)

**Outputs:**
- `discovered_labels`: Combined labels from both phases
- `receipt_word_labels_to_add`: New ReceiptWordLabel entities to save
- `receipt_word_labels_to_update`: Existing ReceiptWordLabel entities to update
- `confidence_score`: Overall confidence (average across all labels)

### Phase 1: Currency Analysis (`phase1_currency_analysis`)

**Purpose**: Identify currency amounts and classify them as GRAND_TOTAL, TAX, SUBTOTAL, or LINE_TOTAL

**LLM**: Uses Ollama `gpt-oss:120b` model
**Input**: Receipt formatted text
**Output**: List of `CurrencyLabel` objects with:
- `line_text`: The text containing the currency amount
- `amount`: The numeric currency value
- `label_type`: CurrencyLabelType (GRAND_TOTAL, TAX, etc.)
- `line_ids`: List of line IDs this amount spans
- `confidence`: Confidence score (0-1)
- `reasoning`: LLM explanation

### Phase 2: Line Item Analysis (`phase2_line_analysis`)

**Purpose**: For each LINE_TOTAL from Phase 1, identify line item components (PRODUCT_NAME, QUANTITY, UNIT_PRICE)

**LLM**: Uses Ollama `gpt-oss:20b` model (smaller for detailed analysis)
**Input**: Specific line text for each LINE_TOTAL
**Output**: List of `LineItemLabel` objects with:
- `word_text`: The specific word being labeled
- `label_type`: LineItemLabelType (PRODUCT_NAME, QUANTITY, UNIT_PRICE)
- `confidence`: Confidence score
- `reasoning`: LLM explanation

**Key Feature**: This phase runs **in parallel** for each LINE_TOTAL discovered in Phase 1, using LangGraph's Send API for dynamic dispatch.

### Combine Results (`combine_results`)

**Purpose**: Merge Phase 1 and Phase 2 results, create ReceiptWordLabel entities for DynamoDB

**Operations**:
1. Combine currency labels from Phase 1 with line item labels from Phase 2
2. Filter out duplicates (Phase 2 currency labels if already found in Phase 1)
3. Create ReceiptWordLabel entities mapping to actual words in the receipt
4. Load existing labels from DynamoDB
5. Compute adds (new labels) and updates (existing labels that need revision)
6. Calculate overall confidence score

**Precedence Rules**:
- If a word has a LINE_TOTAL label, drop competing PRODUCT_NAME/UNIT_PRICE labels
- Only update PENDING labels, skip already VALIDATED ones

## Input and Output

### Main Entry Point: `analyze_receipt_simple`

**Location**: `receipt_label/receipt_label/langchain/currency_validation.py`

**Input Parameters:**
```python
async def analyze_receipt_simple(
    client: DynamoClient,      # DynamoDB client
    image_id: str,              # Receipt image ID
    receipt_id: int,            # Receipt ID
    ollama_api_key: str,        # Ollama API key (injected via closure)
    langsmith_api_key: Optional[str] = None,  # Optional LangSmith tracing
    save_labels: bool = False,  # Whether to persist to DynamoDB
    dry_run: bool = False,      # Preview mode
    save_dev_state: bool = False,  # Save workflow state for debugging
) -> ReceiptAnalysis
```

**Output Type (`ReceiptAnalysis`):**
```python
{
    "receipt_id": str,
    "known_total": float,
    "discovered_labels": List[CurrencyLabel | LineItemLabel],
    "validation_results": Dict,
    "total_lines": int,
    "confidence_score": float,
    "formatted_text": str,
    "processing_time": float
}
```

## Infrastructure Deployment

### 1. Lambda Deployment

**Location**: `infra/currency_validation_step_functions/`

**Components**:
- `handlers/list_receipts.py`: Lists receipts requiring validation
- `handlers/process_receipt.py`: **Invokes LangGraph workflow**

**Key Lambda Configuration** (from `infrastructure.py`):
```python
currency_validation_process_lambda = aws.lambda_.Function(
    "currency_validation_process_lambda",
    runtime="python3.12",
    architectures=["arm64"],
    memory_size=1536,
    timeout=600,  # 10 minutes
    layers=[label_layer.arn],  # Contains LangGraph + LangChain
    environment={
        "DYNAMODB_TABLE_NAME": dynamodb_table.name,
        "OLLAMA_API_KEY": ollama_api_key,
        "LANGCHAIN_API_KEY": langsmith_api_key,
        "LANGCHAIN_TRACING_V2": "true",
        "LANGCHAIN_PROJECT": "currency-validation",
    }
)
```

### 2. Step Functions Orchestration

**State Machine**: `create_currency_validation_state_machine()`

**Workflow**:
```
ListReceipts → HasReceipts? → [ForEachReceipt (Map with max concurrency)] → Done
                                 ↓
                              ValidateReceipt (Lambda invokes LangGraph)
```

**Features**:
- Fan-out pattern: Process multiple receipts in parallel
- Max concurrency: 10 (configurable)
- Error handling: Retry + SNS notifications
- Timeout: 7200 seconds (2 hours) for entire workflow

### 3. Lambda Handler Implementation

**File**: `infra/currency_validation_step_functions/handlers/process_receipt.py`

```python
def handler(event: Dict[str, Any], _):
    """Run currency validation for a single receipt using LangGraph flow."""
    image_id = event["image_id"]
    receipt_id = event["receipt_id"]
    
    # Execute async analyzer from sync Lambda runtime
    result = asyncio.run(
        analyze_receipt_simple(
            client,
            image_id,
            receipt_id,
            ollama_api_key=OLLAMA_API_KEY,
            langsmith_api_key=LANGCHAIN_API_KEY,
            save_labels=save_labels,
            dry_run=dry_run,
            save_dev_state=save_dev_state,
        )
    )
    
    return {
        "statusCode": 200,
        "image_id": image_id,
        "receipt_id": receipt_id,
        "confidence": result.confidence_score,
        "labels": len(result.discovered_labels or []),
        "processing_time": result.processing_time,
    }
```

## LangSmith Tracing

The workflow integrates with **LangSmith** for observability:

```python
# Setup tracing
if langsmith_api_key:
    os.environ["LANGCHAIN_API_KEY"] = langsmith_api_key
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_PROJECT"] = f"receipt-analysis-unified-{image_id[:8]}"

# Attach tracer to graph invocation
tracer = LangChainTracer()
result = await unified_graph.ainvoke(
    initial_state,
    config={
        "callbacks": [tracer],
        "tags": ["unified", "parallel", "receipt-analysis"],
    },
)
```

This enables viewing the entire LangGraph execution in LangSmith dashboard.

## Key Design Decisions

### 1. API Key Security
- API keys are **injected via closures** in graph nodes, never stored in state
- This ensures keys don't get serialized to disk or logged

### 2. Parallel Processing
- Phase 2 uses LangGraph's `Send` API to dispatch multiple parallel nodes
- Each LINE_TOTAL gets its own Phase 2 analysis instance
- Results are automatically reduced using `operator.add` in the state model

### 3. State Reduction
- `line_item_labels` uses `Annotated[List[LineItemLabel], operator.add]`
- This automatically combines results from multiple parallel Phase 2 nodes
- Prevents overwriting between parallel executions

### 4. Database Integration
- DynamoDB client is passed through state (not stored, just referenced)
- Supports both adding new labels and updating existing ones
- Respects validation status (doesn't overwrite VALIDATED labels)

## Dependencies

**From `receipt_label/pyproject.toml`**:
```toml
dependencies = [
    "langchain>=0.3.0",
    "langchain-core>=0.3.0",
    "langchain-community>=0.3.0",
    "langgraph>=0.2.0",  # For graph-based workflows
    "langchain-openai>=0.2.0",
    "langchain-ollama>=0.2.0",  # Ollama integration
    "langsmith>=0.1.0",  # Observability and tracing
]
```

## Comparison with Merchant Validation

**IMPORTANT**: The merchant validation system (`receipt_label/merchant_validation/`) does **NOT** use LangGraph. It uses:
- A traditional agent-based approach with OpenAI's Agent API
- Different infrastructure: Container-based Lambda with EFS access
- Focus: Identifying merchant identity from receipt text
- See `infra/merchant_validation_container/` for deployment

The two systems are **independent** and serve different purposes:
- **LangGraph (Currency Validation)**: Labels words in a receipt (GRAND_TOTAL, PRODUCT_NAME, etc.)
- **Merchant Validation**: Identifies which merchant the receipt is from (business name, location, etc.)

## Usage Example

```python
from receipt_label.langchain.currency_validation import analyze_receipt_simple
from receipt_dynamo import DynamoClient

# Initialize
client = DynamoClient("my-table")

# Analyze receipt
result = await analyze_receipt_simple(
    client=client,
    image_id="abc123",
    receipt_id=1,
    ollama_api_key="sk-...",
    langsmith_api_key="ls-...",
    save_labels=True,  # Persist to DynamoDB
)

print(f"Confidence: {result.confidence_score}")
print(f"Labels discovered: {len(result.discovered_labels)}")
```

## Summary

LangGraph is used specifically for **currency and line item labeling** in receipt processing. It provides:
- **Structured workflow** with clear phases
- **Parallel processing** for efficiency
- **LangSmith integration** for observability
- **Secure API key handling** via closures
- **Database integration** with smart merging logic

The system is deployed as a Lambda function orchestrated by AWS Step Functions, enabling fan-out processing of multiple receipts in parallel.

