# LangGraph Extension Plan: Adding Additional CORE_LABELS

## Current State

### What's Working Now
- **Phase 1**: Currency analysis (GRAND_TOTAL, TAX, SUBTOTAL, LINE_TOTAL)
- **Phase 2**: Line item analysis (PRODUCT_NAME, QUANTITY, UNIT_PRICE)
- **Infrastructure**: Deployed via Lambda + Step Functions
- **Testing**: `dev.test_simple_currency_validation.py`

### Architecture
```
START → load_data → phase1_currency → phase2_line_analysis → combine_results → END
```

---

## Target Labels to Add

### Priority 1: Transaction Header Labels
- **`DATE`** - Calendar date of the transaction
- **`TIME`** - Time of the transaction

### Priority 2: Merchant Context Labels  
- **`PHONE_NUMBER`** - Telephone number printed on receipt
- **`ADDRESS_LINE`** - Full address line

### Priority 3: Transaction Details
- **`PAYMENT_METHOD`** - Payment instrument summary
- **`COUPON`** - Coupon code or description
- **`DISCOUNT`** - Non-coupon discount line item

### Skip (Already Have or Low Value)
- `MERCHANT_NAME` - Already in ReceiptMetadata
- `STORE_HOURS`, `WEBSITE`, `LOYALTY_ID` - Low value

---

## Implementation Strategy

### Option A: Add New Phase (Recommended)
**Pattern**: Phase 3 for header analysis

```
START → load_data → phase1_currency → phase3_header → phase2_line_analysis → combine_results → END
```

**Advantages:**
- Clean separation of concerns
- Header analysis is different from currency analysis
- Can run in parallel with Phase 2
- Easy to test independently

### Option B: Extend Phase 1
Add DATE/TIME to existing Phase 1 currency analysis

**Advantages:**
- Fewer nodes in graph
- Single LLM call for header + currency

**Disadvantages:**
- Less modular
- Mixed concerns (currency + date/time are semantically different)
- Harder to optimize independently

---

## Recommended Implementation: Option A

### Phase 3 Architecture

**New Node**: `phase3_header_analysis`
- **Input**: First 10-15 lines of receipt (header region)
- **Output**: HeaderLabel objects (DATE, TIME)
- **LLM**: Same Ollama model (gpt-oss:120b)
- **Location**: Between Phase 1 and Phase 2

**Rationale**:
- Header fields (DATE, TIME) are top of receipt
- Different from currency amounts (throughout receipt)
- Different from line items (bottom of receipt)
- Natural extension of current 2-phase design

---

## Detailed Implementation Steps

### Step 1: Create Phase 3 Models

**File**: `receipt_label/receipt_label/langchain/models/currency_validation.py`

Add to existing models:

```python
class HeaderLabelType(str, Enum):
    """Header information labels."""
    DATE = "DATE"
    TIME = "TIME"


class HeaderLabel(BaseModel):
    """A discovered header label with LLM reasoning."""
    
    word_text: str = Field(
        description="The exact text of the word being labeled"
    )
    label_type: HeaderLabelType = Field(
        description="The classified label type (DATE or TIME)"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in this classification"
    )
    reasoning: str = Field(
        description="Explanation for why this classification was chosen"
    )


class Phase3Response(BaseModel):
    """Response model for phase 3 (header analysis)."""
    
    header_labels: List[HeaderLabel] = Field(
        description="Header information classifications"
    )
    reasoning: str = Field(description="Overall analysis reasoning")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Overall confidence score"
    )
```

### Step 2: Update State Model

**File**: `receipt_label/receipt_label/langchain/state/currency_validation.py`

Add to `CurrencyAnalysisState`:

```python
class CurrencyAnalysisState(BaseModel):
    # ... existing fields ...
    
    # Phase results
    currency_labels: List[CurrencyLabel] = Field(default_factory=list)
    header_labels: List[HeaderLabel] = Field(default_factory=list)  # NEW
    line_item_labels: Annotated[List[LineItemLabel], operator.add] = Field(
        default_factory=list
    )
    
    # ... rest unchanged ...
```

Update `to_serializable` method to include header_labels.

### Step 3: Create Phase 3 Node

**File**: `receipt_label/receipt_label/langchain/nodes/phase3.py`

```python
from __future__ import annotations

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_ollama import ChatOllama

from receipt_label.constants import CORE_LABELS
from receipt_label.langchain.models import (
    HeaderLabel,
    HeaderLabelType,
    Phase3Response,
)
from receipt_label.langchain.state.currency_validation import (
    CurrencyAnalysisState,
)


async def phase3_header_analysis(
    state: CurrencyAnalysisState, ollama_api_key: str
):
    """Analyze header information (DATE and TIME)."""
    
    llm = ChatOllama(
        model="gpt-oss:120b",
        base_url="https://ollama.com",
        client_kwargs={
            "headers": {"Authorization": f"Bearer {ollama_api_key}"}
        },
    )
    
    # Extract header region (first 15 lines typically contain header info)
    header_lines = state.lines[:15] if len(state.lines) > 15 else state.lines
    header_text = "\n".join([line.text for line in header_lines])
    
    # Focus on header-specific labels
    subset = [
        HeaderLabelType.DATE.value,
        HeaderLabelType.TIME.value,
    ]
    subset_definitions = "\n".join(
        f"- {l}: {CORE_LABELS[l]}" for l in subset if l in CORE_LABELS
    )
    
    template = """You are analyzing a receipt header to identify transaction date and time.

RECEIPT HEADER (First 15 lines):
{header_text}

Find all date and time information and classify them as:
{subset_definitions}

Focus on:
1. Transaction date (usually near the top)
2. Transaction time (usually next to or near the date)

Output only DATE and TIME labels. Ignore other information.

{format_instructions}"""
    
    output_parser = PydanticOutputParser(pydantic_object=Phase3Response)
    prompt = PromptTemplate(
        template=template,
        input_variables=["header_text", "subset_definitions"],
        partial_variables={
            "format_instructions": output_parser.get_format_instructions()
        },
    )
    
    chain = prompt | llm | output_parser
    try:
        response = await chain.ainvoke(
            {
                "header_text": header_text,
                "subset_definitions": subset_definitions,
            },
            config={
                "metadata": {
                    "receipt_id": state.receipt_id,
                    "phase": "header_analysis",
                    "model": "120b",
                },
                "tags": ["phase3", "header", "receipt-analysis"],
            },
        )
        
        header_labels = [
            HeaderLabel(
                word_text=item.word_text,
                label_type=getattr(HeaderLabelType, item.label_type),
                confidence=item.confidence,
                reasoning=item.reasoning,
            )
            for item in response.header_labels
        ]
        return {"header_labels": header_labels}
    except Exception as e:
        print(f"Phase 3 failed: {e}")
        return {"header_labels": []}
```

### Step 4: Update Graph Structure

**File**: `receipt_label/receipt_label/langchain/currency_validation.py`

Update `create_unified_analysis_graph`:

```python
def create_unified_analysis_graph(
    ollama_api_key: str, save_dev_state: bool = False
) -> CompiledStateGraph:
    """Create unified graph with Phase 3 (header analysis)."""

    workflow = StateGraph(CurrencyAnalysisState)

    # Create nodes with API key injection via closures
    async def phase1_with_key(state):
        return await phase1_currency_analysis(state, ollama_api_key)
    
    async def phase3_with_key(state):
        return await phase3_header_analysis(state, ollama_api_key)

    def dispatch_with_key(state):
        return dispatch_to_parallel_phase2(state, ollama_api_key)

    async def phase2_with_key(send_data):
        return await phase2_line_analysis(send_data)

    async def combine_with_dev_save(state):
        return await combine_results(state, save_dev_state)

    # Add nodes
    workflow.add_node("load_data", load_receipt_data)
    workflow.add_node("phase1_currency", phase1_with_key)
    workflow.add_node("phase3_header", phase3_with_key)  # NEW
    workflow.add_node("phase2_line_analysis", phase2_with_key)
    workflow.add_node("combine_results", combine_with_dev_save)

    # Define the flow
    workflow.add_edge(START, "load_data")
    workflow.add_edge("load_data", "phase1_currency")
    
    # Phase 1 → Phase 3 (header analysis)
    workflow.add_edge("phase1_currency", "phase3_header")  # NEW
    
    # Phase 3 → Phase 2 (parallel line item analysis)
    workflow.add_conditional_edges(
        "phase3_header",
        dispatch_with_key,
        ["phase2_line_analysis"],
    )
    
    # Phase 2 → Combine results
    workflow.add_edge("phase2_line_analysis", "combine_results")
    workflow.add_edge("combine_results", END)

    return workflow.compile()
```

### Step 5: Update Combine Results

**File**: `receipt_label/receipt_label/langchain/currency_validation.py`

Update `combine_results` function to include header_labels:

```python
async def combine_results(
    state: CurrencyAnalysisState, save_dev_state: bool = False
) -> CurrencyAnalysisState:
    """Final node: Combine all results from all phases."""

    print(f"🔄 Combining results")

    if save_dev_state:
        print(f"   💾 Saving state for development...")
        save_json(
            state=dict(state),
            stage="combine_results_start",
            receipt_id=state.receipt_id,
            output_dir="./dev.states",
        )

    # Combine all discovered labels
    discovered_labels = []

    # Add currency labels from Phase 1
    currency_labels = state.currency_labels
    discovered_labels.extend(currency_labels)
    
    # Add header labels from Phase 3  # NEW
    header_labels = state.header_labels
    discovered_labels.extend(header_labels)

    # Add line item labels from Phase 2 (already reduced by graph reducer)
    line_item_labels = state.line_item_labels
    discovered_labels.extend(filtered_line_labels)

    print(f"   ✅ Phase 1: {len(currency_labels)} currency labels")
    print(f"   ✅ Phase 3: {len(header_labels)} header labels")  # NEW
    print(f"   ✅ Phase 2: {len(filtered_line_labels)} line item labels")
    
    # ... rest of function unchanged ...
```

### Step 6: Update Label Mapping Service

**File**: `receipt_label/receipt_label/langchain/services/label_mapping.py`

Update `create_receipt_word_labels_from_currency_labels` to handle header labels:

```python
def create_receipt_word_labels_from_currency_labels(
    discovered_labels: List[CurrencyLabel | HeaderLabel | LineItemLabel],  # Updated
    lines: List[ReceiptLine],
    words: List[ReceiptWord] | None,
    image_id: str,
    receipt_id: str,
    client: DynamoClient,
) -> List[ReceiptWordLabel]:
    """Create ReceiptWordLabel entities from all label types."""
    
    # ... existing code for currency and line item labels ...
    
    # Handle header labels (DATE, TIME)
    for label in discovered_labels:
        if hasattr(label, 'word_text'):  # HeaderLabel has word_text
            if label.label_type in ['DATE', 'TIME']:
                # Map word_text to actual words in receipt
                # ... similar logic to line item labels ...
```

### Step 7: Add Import Statements

**File**: `receipt_label/receipt_label/langchain/currency_validation.py`

```python
from receipt_label.langchain.nodes.phase3 import phase3_header_analysis  # NEW
```

**File**: `receipt_label/receipt_label/langchain/models/__init__.py`

```python
from .currency_validation import (
    # ... existing imports ...
    HeaderLabel,         # NEW
    HeaderLabelType,    # NEW
    Phase3Response,     # NEW
)

__all__ = [
    # ... existing exports ...
    "HeaderLabel",      # NEW
    "HeaderLabelType",  # NEW
    "Phase3Response",   # NEW
]
```

---

## Testing Plan

### Step 1: Unit Test Phase 3 Node

Create `test_phase3_header.py`:

```python
import asyncio
from receipt_label.langchain.state.currency_validation import CurrencyAnalysisState
from receipt_label.langchain.nodes.phase3 import phase3_header_analysis

async def test_phase3():
    # Create test state with sample receipt lines
    state = CurrencyAnalysisState(
        receipt_id="test/1",
        image_id="test",
        lines=[...],  # Sample receipt lines
        formatted_text="...",  # Sample formatted text
    )
    
    result = await phase3_header_analysis(state, "test-key")
    print(f"Header labels: {result['header_labels']}")
    
asyncio.run(test_phase3())
```

### Step 2: Integration Test

Update `dev.test_simple_currency_validation.py`:

```python
# No changes needed - will automatically use Phase 3
# Check output for header_labels in result
```

### Step 3: Check Dev State Files

After running tests, check `./dev.states/`:

```bash
ls -la ./dev.states/
cat ./dev.states/state_combine_results_*_metadata.json
# Should show: header_labels_count > 0
```

### Step 4: Validate Output

Check that date/time labels are created correctly:

```python
# In dev state JSON files, look for:
{
  "header_labels_count": 2,
  "header_labels": [
    {
      "word_text": "12/25/2024",
      "label_type": "DATE",
      "confidence": 0.95
    },
    {
      "word_text": "14:30",
      "label_type": "TIME",
      "confidence": 0.92
    }
  ]
}
```

---

## Deployment

### No Infrastructure Changes Needed

The existing infrastructure will automatically use Phase 3:

- **Lambda**: `infra/currency_validation_step_functions/handlers/process_receipt.py`
- **Step Functions**: Already configured for this pattern
- **No code changes** to infrastructure - just deploy updated Lambda code

### Deployment Steps

1. Update code in `receipt_label/`
2. Run tests locally with `dev.test_simple_currency_validation.py`
3. Build Lambda layer (if needed)
4. Deploy via Pulumi: `pulumi up`
5. Lambda automatically gets new code from the layer

---

## Next Steps After DATE/TIME

### Phase 4: Merchant Context (Optional)

If you need PHONE_NUMBER and ADDRESS_LINE:

**Option 1**: Extend Phase 3 to include them
```python
# In phase3.py, expand subset:
subset = [
    HeaderLabelType.DATE.value,
    HeaderLabelType.TIME.value,
    HeaderLabelType.PHONE_NUMBER.value,  # Add
    HeaderLabelType.ADDRESS_LINE.value,   # Add
]
```

**Option 2**: Create Phase 4 separately
- Keep header analysis focused on DATE/TIME
- Create new phase for merchant context

### Phase 5: Transaction Details (Optional)

For PAYMENT_METHOD, COUPON, DISCOUNT:

**Recommended**: Add to Phase 2 (analyze footer region)
- These are usually at the bottom of receipt
- Can be detected when analyzing line items

---

## Success Criteria

### Phase 3 Complete When:
- [ ] Date and time extracted from receipts
- [ ] Header labels saved to ReceiptWordLabel entities in DynamoDB
- [ ] Confidence scores > 0.8 for date/time in tests
- [ ] LangSmith traces show Phase 3 execution
- [ ] No degradation in existing currency/line item accuracy

### Metrics to Track
- Date extraction accuracy (% of receipts with valid date)
- Time extraction accuracy (% of receipts with valid time)
- Overall confidence score (should be > 0.7)
- Processing time impact (should add < 1 second per receipt)

---

## Files to Modify Summary

1. **Models**: `receipt_label/langchain/models/currency_validation.py`
   - Add `HeaderLabelType`, `HeaderLabel`, `Phase3Response`

2. **State**: `receipt_label/langchain/state/currency_validation.py`
   - Add `header_labels` to `CurrencyAnalysisState`

3. **Node**: `receipt_label/langchain/nodes/phase3.py` (NEW FILE)
   - Create `phase3_header_analysis` function

4. **Graph**: `receipt_label/langchain/currency_validation.py`
   - Add Phase 3 node to graph
   - Update edges

5. **Combine**: `receipt_label/langchain/currency_validation.py`
   - Update `combine_results` to include header_labels

6. **Mapping**: `receipt_label/langchain/services/label_mapping.py`
   - Handle header labels in label creation

7. **Exports**: `receipt_label/langchain/models/__init__.py`
   - Export new models

---

## Timeline Estimate

- **Step 1-2** (Models & State): 30 minutes
- **Step 3** (Phase 3 Node): 1 hour
- **Step 4-5** (Graph & Combine): 30 minutes
- **Step 6** (Label Mapping): 45 minutes
- **Step 7** (Imports): 15 minutes
- **Testing**: 1 hour
- **Total**: ~4-5 hours

---

## Rollback Plan

If issues arise:

1. Graph still works without Phase 3 (phase3_with_key returns empty)
2. Can disable Phase 3 node by setting edge to skip
3. Existing Phase 1 + Phase 2 unchanged
4. No database schema changes needed

