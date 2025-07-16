# Phase 3 Technical Specification

## Immediate Implementation Plan

### Week 1: Foundation Setup

#### 1. Install LangGraph Dependencies
```bash
pip install langgraph langchain-core langchain-openai
```

Update `pyproject.toml`:
```toml
[tool.poetry.dependencies]
langgraph = "^0.0.20"
langchain-core = "^0.1.0"
langchain-openai = "^0.0.5"
```

#### 2. Create Module Structure
```
receipt_label/
├── langgraph_integration/
│   ├── __init__.py
│   ├── state.py              # State type definitions
│   ├── workflow.py           # Main workflow definition
│   ├── nodes/
│   │   ├── __init__.py
│   │   ├── merchant_lookup.py
│   │   ├── gap_analysis.py
│   │   ├── prompt_builder.py
│   │   ├── gpt_caller.py
│   │   └── validator.py
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── spatial_context.py   # Extract line-level context
│   │   ├── label_merger.py      # Merge pattern + GPT labels
│   │   └── validation_rules.py  # Math & semantic validation
│   └── prompts/
│       ├── __init__.py
│       └── templates.py          # GPT prompt templates
```

#### 3. DynamoDB Merchant Data Schema
```python
# receipt_dynamo/entities/merchant_metadata.py
from dataclasses import dataclass
from typing import List, Dict, Optional

@dataclass
class MerchantMetadata:
    """Merchant-specific patterns and metadata"""
    pk: str  # "MERCHANT#{name}"
    sk: str  # "METADATA"
    merchant_name: str
    normalized_name: str  # For fuzzy matching
    common_patterns: List[str]  # ["TC#", "ST#", "OP#"]
    common_products: List[str]  # ["Big Mac", "Quarter Pounder"]
    typical_layout: Dict[str, str]  # {"price_column": "right", "tax_position": "before_total"}
    validation_rules: Dict[str, Any]  # Custom validation for this merchant
    last_updated: str
    confidence_score: float  # How reliable is this data
```

### Week 2: Core Implementation

#### 1. Spatial Context Extractor
```python
# receipt_label/langgraph_integration/utils/spatial_context.py

def extract_line_context(
    words: List[ReceiptWord], 
    currency_columns: List[PriceColumn]
) -> Dict[int, Dict]:
    """
    Extract text context for each line with currency values.
    
    Returns:
    {
        5: {
            "price": "$12.99",
            "price_x": 450,
            "left_text": "CHICKEN SANDWICH",
            "all_text": "CHICKEN SANDWICH     12.99",
            "confidence": 0.95
        },
        6: {
            "price": "$8.50",
            "price_x": 448,
            "left_text": "FRENCH FRIES LG",
            "all_text": "FRENCH FRIES LG      8.50",
            "confidence": 0.90
        }
    }
    """
    line_context = {}
    
    # Group words by line number
    words_by_line = defaultdict(list)
    for word in words:
        words_by_line[word.line_number].append(word)
    
    # For each price in columns, extract same-line text
    for column in currency_columns:
        for price_match in column.prices:
            line_num = price_match.word.line_number
            price_x = price_match.word.x_center
            
            # Get all words on same line
            line_words = words_by_line[line_num]
            
            # Find text to the left of price
            left_words = [
                w for w in line_words 
                if w.x_center < price_x - 10  # Small buffer
            ]
            left_words.sort(key=lambda w: w.x_center)
            
            line_context[line_num] = {
                "price": price_match.matched_text,
                "price_x": price_x,
                "left_text": " ".join(w.text for w in left_words),
                "all_text": " ".join(w.text for w in sorted(line_words, key=lambda w: w.x_center)),
                "confidence": column.confidence
            }
    
    return line_context
```

#### 2. Smart GPT Prompt Builder
```python
# receipt_label/langgraph_integration/nodes/prompt_builder.py

async def build_gpt_prompt_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Build context-aware prompt focusing on what we're missing"""
    
    # Extract line context for prices
    line_context = extract_line_context(
        state["receipt_words"], 
        state["currency_columns"]
    )
    
    # Build focused prompt
    if "PRODUCT_DESCRIPTIONS_FOR_PRICES" in state["missing_labels"]:
        prompt = build_line_item_prompt(
            line_context,
            state["merchant_data"],
            state["math_solutions"]
        )
    else:
        prompt = build_general_prompt(
            state["missing_labels"],
            state["pattern_results"],
            state["merchant_data"]
        )
    
    state["gpt_prompt"] = prompt
    return state

def build_line_item_prompt(line_context, merchant_data, math_solutions):
    """Specialized prompt for matching products to prices"""
    
    prompt = f"""
    Task: Identify product names for the following prices found on a receipt.
    
    Merchant: {merchant_data.get('merchant_name', 'Unknown')}
    {f"Common products at this merchant: {', '.join(merchant_data.get('common_products', []))}" if merchant_data else ""}
    
    PRICES WITH LINE CONTEXT:
    """
    
    for line_num, context in sorted(line_context.items()):
        prompt += f"\n    Line {line_num}: {context['left_text']} → {context['price']}"
    
    prompt += f"""
    
    MATHEMATICAL VALIDATION:
    The following mathematical relationships were detected:
    """
    
    for solution in math_solutions:
        if solution['type'] == 'items_to_total':
            prompt += f"\n    Items {solution['items']} sum to {solution['sum']}"
    
    prompt += """
    
    OUTPUT FORMAT:
    For each price, provide:
    - line_number
    - price
    - product_name
    - label_type (PRODUCT_NAME, TAX, SUBTOTAL, or GRAND_TOTAL)
    
    IMPORTANT:
    - Product names are typically printed LEFT of the price
    - The largest price at the bottom is usually GRAND_TOTAL
    - TAX appears near the total, often as a percentage of subtotal
    """
    
    return prompt
```

#### 3. Validation Engine
```python
# receipt_label/langgraph_integration/utils/validation_rules.py

def validate_math_consistency(labels: Dict, math_solutions: List[Dict]) -> bool:
    """Validate that labeled items match mathematical relationships"""
    
    # Extract all product prices
    product_prices = [
        float(label['value']) 
        for label in labels.values() 
        if label['type'] == 'PRODUCT_NAME'
    ]
    
    # Check against math solutions
    for solution in math_solutions:
        if solution['type'] == 'items_to_total':
            expected_sum = solution['sum']
            actual_sum = sum(product_prices)
            
            # Allow small floating point differences
            if abs(actual_sum - expected_sum) > 0.01:
                logger.warning(
                    f"Math validation failed: {actual_sum} != {expected_sum}"
                )
                return False
    
    return True

def validate_against_merchant_data(labels: Dict, merchant_data: Dict) -> bool:
    """Validate labels against known merchant patterns"""
    
    if not merchant_data:
        return True  # Can't validate without data
    
    # Check if product names match known products
    if 'common_products' in merchant_data:
        known_products = merchant_data['common_products']
        found_products = [
            label['value'] 
            for label in labels.values() 
            if label['type'] == 'PRODUCT_NAME'
        ]
        
        # Fuzzy match against known products
        matches = 0
        for found in found_products:
            if any(fuzz.ratio(found, known) > 80 for known in known_products):
                matches += 1
        
        # If we found products, at least some should match
        if found_products and matches == 0:
            logger.warning("No products match merchant's known products")
            return False
    
    return True
```

### Week 3: Integration & Testing

#### 1. Main Orchestrator Integration
```python
# receipt_label/core/labeler_v3.py

class ReceiptLabelerV3:
    """Phase 3 labeler with LangGraph integration"""
    
    def __init__(
        self,
        decision_engine_config: Optional[DecisionEngineConfig] = None,
        use_langgraph: bool = True
    ):
        # Phase 2 components
        self.decision_orchestrator = DecisionEngineOrchestrator(
            config=decision_engine_config
        )
        
        # Phase 3 LangGraph workflow
        self.use_langgraph = use_langgraph
        if use_langgraph:
            self.workflow = create_phase3_workflow()
    
    async def label_receipt(
        self, 
        receipt_id: str,
        words: List[ReceiptWord]
    ) -> Dict[str, Any]:
        """Main entry point for receipt labeling"""
        
        # Step 1: Get receipt metadata from DynamoDB
        receipt_metadata = await get_receipt_metadata(receipt_id)
        
        # Step 2: Run Phase 2 pattern detection
        integration_result = await self.decision_orchestrator.process_receipt(
            words, 
            receipt_context={"receipt_id": receipt_id}
        )
        
        # Step 3: Check if we need Phase 3 (LangGraph)
        if integration_result.decision.outcome == DecisionOutcome.SKIP:
            # 81.7% of receipts - patterns were sufficient!
            return {
                "labels": integration_result.pattern_results,
                "method": "patterns_only",
                "cost": 0.0
            }
        
        # Step 4: Use LangGraph for the complex 18.3%
        if self.use_langgraph:
            state = {
                "receipt_words": words,
                "receipt_metadata": receipt_metadata,
                "pattern_results": integration_result.pattern_results,
                "currency_columns": integration_result.pattern_results.get("currency_columns", []),
                "math_solutions": integration_result.pattern_results.get("math_solutions", []),
                "decision_outcome": integration_result.decision.outcome.value,
                "retry_count": 0
            }
            
            # Run the workflow
            final_state = await self.workflow.arun(state)
            
            return {
                "labels": final_state["final_labels"],
                "method": "langgraph_gpt",
                "cost": calculate_gpt_cost(final_state),
                "validations": final_state["validation_results"]
            }
        else:
            # Fallback to simple GPT call without LangGraph
            return await self._simple_gpt_labeling(
                words, 
                integration_result.pattern_results
            )
```

#### 2. Cost Tracking
```python
# receipt_label/langgraph_integration/utils/cost_tracker.py

def calculate_gpt_cost(final_state: Dict) -> float:
    """Calculate total GPT API cost for this receipt"""
    
    # Track all GPT calls made
    prompt_tokens = final_state.get("total_prompt_tokens", 0)
    completion_tokens = final_state.get("total_completion_tokens", 0)
    
    # OpenAI pricing (as of 2024)
    PRICE_PER_1K_PROMPT = 0.01  # $0.01 per 1K prompt tokens
    PRICE_PER_1K_COMPLETION = 0.03  # $0.03 per 1K completion tokens
    
    cost = (
        (prompt_tokens / 1000) * PRICE_PER_1K_PROMPT +
        (completion_tokens / 1000) * PRICE_PER_1K_COMPLETION
    )
    
    # If using batch API (50% discount)
    if final_state.get("used_batch_api", False):
        cost *= 0.5
    
    return cost
```

## Testing Strategy

### 1. Unit Tests for Each Node
```python
# tests/langgraph_integration/test_nodes.py

async def test_spatial_context_extraction():
    """Test that we correctly extract line-level context"""
    
    # Mock currency columns and words
    words = create_mock_receipt_words()
    columns = create_mock_currency_columns()
    
    # Extract context
    context = extract_line_context(words, columns)
    
    # Verify we found product names for prices
    assert context[5]["left_text"] == "CHICKEN SANDWICH"
    assert context[5]["price"] == "$12.99"
```

### 2. Integration Tests
```python
# tests/langgraph_integration/test_workflow.py

async def test_full_workflow_with_missing_products():
    """Test complete workflow when products are missing"""
    
    # Create a receipt with prices but no product labels
    state = {
        "receipt_words": load_test_receipt("receipt_with_prices_only.json"),
        "pattern_results": {
            "currency_columns": [...],  # Has prices
            "labels": {}  # No product names
        },
        "missing_labels": ["PRODUCT_DESCRIPTIONS_FOR_PRICES"]
    }
    
    # Run workflow
    workflow = create_phase3_workflow()
    final_state = await workflow.arun(state)
    
    # Verify products were found and validated
    assert "PRODUCT_NAME" in final_state["final_labels"]
    assert final_state["validation_results"]["mathematical"] == True
```

## Success Criteria

1. **Line Item Matching**: Successfully match >90% of prices to product names
2. **Cost Efficiency**: Maintain <20% GPT usage rate across all receipts
3. **Validation Success**: >95% of GPT responses pass mathematical validation
4. **Performance**: Total processing time <3s for complex receipts

## Monitoring & Optimization

1. **Track Key Metrics**:
   - GPT call rate by merchant
   - Validation failure reasons
   - Cost per receipt
   - Line item matching accuracy

2. **Continuous Improvement**:
   - Add successful patterns to merchant data
   - Tune prompts based on failure analysis
   - Optimize retry strategies

This specification provides a clear path forward for implementing Phase 3 with LangGraph, building on the solid foundation of Phase 2's pattern detection while adding intelligent orchestration for the complex cases.