# LangGraph Structure in receipt_label

## Directory Organization

```
receipt_label/receipt_label/langchain/
├── currency_validation.py       # Main entry point & graph definition
├── models/
│   └── currency_validation.py   # Data models (CurrencyLabel, LineItemLabel, etc.)
├── nodes/
│   ├── load_data.py             # Load receipt data from DynamoDB
│   ├── phase1.py                # Currency analysis (GRAND_TOTAL, TAX, etc.)
│   └── phase2.py                # Line item analysis (PRODUCT_NAME, QUANTITY, UNIT_PRICE)
├── state/
│   └── currency_validation.py   # State model (CurrencyAnalysisState)
└── services/
    └── label_mapping.py         # Map labels to ReceiptWordLabel entities
```

## Current Components

### Entry Point Function
**File**: `receipt_label/langchain/currency_validation.py`

```python
async def analyze_receipt_simple(
    client: DynamoClient,
    image_id: str,
    receipt_id: int,
    ollama_api_key: str,
    langsmith_api_key: Optional[str] = None,
    save_labels: bool = False,
    dry_run: bool = False,
    save_dev_state: bool = False,
) -> ReceiptAnalysis
```

**What it does:**
1. Runs the full LangGraph workflow
2. Returns a `ReceiptAnalysis` object with discovered labels
3. Optionally saves labels to DynamoDB
4. Supports dry-run mode for testing
5. Can save dev state to `./dev.states/` directory

### Graph Structure

**Workflow**: `create_unified_analysis_graph()`

```
START
  ↓
load_data (load receipt lines/words from DynamoDB)
  ↓
phase1_currency (analyze currency: GRAND_TOTAL, TAX, SUBTOTAL, LINE_TOTAL)
  ↓
[dispatch_to_parallel_phase2] → spawns N parallel nodes
  ↓
phase2_line_analysis (analyze each LINE_TOTAL for PRODUCT_NAME, QUANTITY, UNIT_PRICE)
  ↓
combine_results (merge all results, create ReceiptWordLabel entities)
  ↓
END
```

### Nodes Breakdown

#### 1. `load_data` (load_data.py)
- **Purpose**: Load receipt lines and words from DynamoDB
- **Input**: Receipt ID from state
- **Output**: Lines, words, formatted text for analysis
- **No LLM calls**

#### 2. `phase1_currency` (phase1.py)
- **Purpose**: Identify currency amounts
- **LLM**: Ollama `gpt-oss:120b`
- **Labels**: GRAND_TOTAL, TAX, SUBTOTAL, LINE_TOTAL
- **Input**: Full receipt text
- **Output**: List of `CurrencyLabel` objects

#### 3. `phase2_line_analysis` (phase2.py)
- **Purpose**: Analyze individual line items
- **LLM**: Ollama `gpt-oss:20b`
- **Labels**: PRODUCT_NAME, QUANTITY, UNIT_PRICE
- **Input**: Specific line text for each LINE_TOTAL
- **Output**: List of `LineItemLabel` objects
- **Runs in parallel** - one instance per LINE_TOTAL found

#### 4. `combine_results` (currency_validation.py)
- **Purpose**: Merge all results and create database entities
- **No LLM calls**
- **Actions**:
  - Combines currency + line item labels
  - Filters duplicates
  - Creates `ReceiptWordLabel` entities
  - Loads existing labels from DynamoDB
  - Computes adds vs updates
  - Calculates confidence score
  - Can save dev state to disk

### State Model

**File**: `receipt_label/langchain/state/currency_validation.py`

```python
class CurrencyAnalysisState(BaseModel):
    # Inputs
    receipt_id: str
    image_id: str
    lines: List[ReceiptLine]
    words: List[ReceiptWord]
    formatted_text: str
    dynamo_client: DynamoClient
    
    # Phase results
    currency_labels: List[CurrencyLabel]
    line_item_labels: List[LineItemLabel]  # Auto-merged from parallel nodes
    
    # Final outputs
    discovered_labels: List[Any]
    confidence_score: float
    processing_time: float
```

**Key feature**: `line_item_labels` uses `operator.add` for automatic merging of parallel node results.

### Service Layer

**File**: `receipt_label/langchain/services/label_mapping.py`

```python
def create_receipt_word_labels_from_currency_labels(
    discovered_labels: List[CurrencyLabel | LineItemLabel],
    lines: List[ReceiptLine],
    words: List[ReceiptWord],
    image_id: str,
    receipt_id: str,
    client: DynamoClient,
) -> List[ReceiptWordLabel]
```

**Purpose**: Convert LangGraph label objects into DynamoDB `ReceiptWordLabel` entities by:
1. Mapping label text to actual words in the receipt
2. Handling different label types
3. Creating proper database entities

### Data Models

**File**: `receipt_label/langchain/models/currency_validation.py`

#### Current Labels Supported

**Currency Labels** (Phase 1):
- `CurrencyLabelType.GRAND_TOTAL`
- `CurrencyLabelType.TAX`
- `CurrencyLabelType.SUBTOTAL`
- `CurrencyLabelType.LINE_TOTAL`

**Line Item Labels** (Phase 2):
- `LineItemLabelType.PRODUCT_NAME`
- `LineItemLabelType.QUANTITY`
- `LineItemLabelType.UNIT_PRICE`

**Response Models**:
- `Phase1Response` - Currency analysis results
- `Phase2Response` - Line item analysis results
- `ReceiptAnalysis` - Complete analysis with all labels

## Usage Example

```python
from receipt_label.langchain.currency_validation import analyze_receipt_simple
from receipt_dynamo import DynamoClient

# Initialize
client = DynamoClient("my-table")

# Run analysis
result = await analyze_receipt_simple(
    client=client,
    image_id="abc-123",
    receipt_id=1,
    ollama_api_key="sk-...",
    langsmith_api_key="ls-...",  # Optional
    save_labels=True,   # Persist to DynamoDB
    dry_run=False,     # Actually save
    save_dev_state=True  # Debug mode
)

# Access results
print(f"Confidence: {result.confidence_score}")
print(f"Total labels: {len(result.discovered_labels)}")

# Labels are organized by type
for label in result.discovered_labels:
    if hasattr(label, 'label_type'):
        print(f"{label.label_type}: {label.amount if hasattr(label, 'amount') else label.word_text}")
```

## What `analyze_receipt_simple` Does

### The Right Function?

**YES** - `analyze_receipt_simple` is the main entry point for LangGraph currency validation.

It:
- ✅ Runs the complete 2-phase LangGraph workflow
- ✅ Handles secure API key injection
- ✅ Supports LangSmith tracing
- ✅ Can save/persist labels to DynamoDB
- ✅ Returns structured results
- ✅ Has dry-run mode for testing

### Current Limitations

Only 7 labels supported:
- ✅ GRAND_TOTAL, TAX, SUBTOTAL, LINE_TOTAL
- ✅ PRODUCT_NAME, QUANTITY, UNIT_PRICE

**Missing** (from `CORE_LABELS` in `constants.py`):
- ❌ DATE, TIME
- ❌ MERCHANT_NAME, PHONE_NUMBER, ADDRESS_LINE
- ❌ PAYMENT_METHOD, COUPON, DISCOUNT
- ❌ Others...

## Is This The Complete LangGraph Structure?

**Current Status**: This is the ONLY LangGraph implementation in the codebase.

**Other Systems** (NOT LangGraph):
- `receipt_label/merchant_validation/` - Uses OpenAI Agents (not LangGraph)
- `receipt_label/decision_engine/` - Pattern matching logic (not LLM-based)
- `receipt_label/pattern_detection/` - Regex/fuzzy matching (not LangGraph)

## Summary

**Single Entry Point**: `analyze_receipt_simple()`

**Directory Structure**:
- **currency_validation.py** - Main graph & entry point
- **models/** - Data models
- **nodes/** - Graph nodes (phase implementations)
- **state/** - State model
- **services/** - Helper functions

**Current Capabilities**:
- 2-phase analysis (currency + line items)
- Parallel processing (multiple Phase 2 nodes)
- Database integration (save to DynamoDB)
- LangSmith tracing
- Dev state debugging

**Next Steps** (from LANGGRAPH_EXTENSION_PLAN.md):
- Add Phase 3 for DATE/TIME (header analysis)
- Extend to more CORE_LABELS

