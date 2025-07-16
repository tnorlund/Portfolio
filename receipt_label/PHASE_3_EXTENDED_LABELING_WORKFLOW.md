# Phase 3: Extended Labeling Workflow

## Overview

After securing the Essential 4 labels (Merchant, Date, Time, Total), we need a systematic approach to identify the remaining CORE_LABELS. This uses a sub-workflow within LangGraph to intelligently fill in product details, tax information, and other receipt components.

## Label Hierarchy

```python
LABEL_TIERS = {
    "TIER_1_ESSENTIAL": {
        # Must have these
        "labels": ["MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL"],
        "min_required": 4,
        "strategy": "aggressive"
    },
    
    "TIER_2_LINE_ITEMS": {
        # Product information
        "labels": ["PRODUCT_NAME", "PRODUCT_PRICE", "PRODUCT_QUANTITY"],
        "min_required": 1,  # At least one product
        "strategy": "spatial_grouping"
    },
    
    "TIER_3_FINANCIAL": {
        # Money flow details
        "labels": ["SUBTOTAL", "TAX", "DISCOUNT"],
        "min_required": 0,  # Nice to have
        "strategy": "mathematical_validation"
    },
    
    "TIER_4_PAYMENT": {
        # Payment details
        "labels": ["PAYMENT_METHOD", "CHANGE", "CARD_LAST_FOUR"],
        "min_required": 0,  # Optional
        "strategy": "keyword_matching"
    }
}
```

## Extended Labeling Sub-Workflow

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Dict, Optional

class ExtendedLabelingState(TypedDict):
    """State for extended labeling sub-workflow"""
    # Input from main workflow
    receipt_words: List[ReceiptWord]
    essential_labels: Dict[int, LabelInfo]  # Already found Merchant, Date, Time, Total
    currency_columns: List[PriceColumn]
    math_solutions: List[Dict]
    
    # Processing state
    current_tier: str
    tier_results: Dict[str, Dict]
    spatial_groups: List[SpatialGroup]
    line_assignments: Dict[int, List[int]]  # line_number -> word_ids
    
    # Results
    extended_labels: Dict[int, LabelInfo]
    confidence_scores: Dict[str, float]
    validation_notes: List[str]

def create_extended_labeling_workflow():
    """Sub-workflow for finding Tier 2-4 labels"""
    
    workflow = StateGraph(ExtendedLabelingState)
    
    # Add nodes for each tier
    workflow.add_node("spatial_analysis", spatial_grouping_node)
    workflow.add_node("line_item_detection", line_item_detection_node)
    workflow.add_node("financial_analysis", financial_analysis_node)
    workflow.add_node("payment_detection", payment_detection_node)
    workflow.add_node("validate_assignments", validate_extended_labels_node)
    
    # Define flow
    workflow.set_entry_point("spatial_analysis")
    workflow.add_edge("spatial_analysis", "line_item_detection")
    workflow.add_edge("line_item_detection", "financial_analysis")
    workflow.add_edge("financial_analysis", "payment_detection")
    workflow.add_edge("payment_detection", "validate_assignments")
    workflow.add_edge("validate_assignments", END)
    
    return workflow.compile()
```

### Node 1: Spatial Analysis

```python
async def spatial_grouping_node(state: ExtendedLabelingState) -> ExtendedLabelingState:
    """Group words by spatial proximity for line item detection"""
    
    # Group words by line number
    lines = defaultdict(list)
    for word in state["receipt_words"]:
        # Skip already labeled essential fields
        if word.word_id not in state["essential_labels"]:
            lines[word.line_number].append(word)
    
    # Identify line patterns
    spatial_groups = []
    for line_num, words in lines.items():
        # Sort words left to right
        words.sort(key=lambda w: w.x)
        
        # Check if line has currency value
        has_price = any(
            price.word.line_number == line_num 
            for col in state["currency_columns"] 
            for price in col.prices
        )
        
        if has_price:
            # This could be a product line
            spatial_groups.append(
                SpatialGroup(
                    line_number=line_num,
                    words=words,
                    group_type="potential_line_item",
                    has_price=True
                )
            )
        else:
            # Check for other patterns
            text = " ".join(w.text for w in words).lower()
            if any(keyword in text for keyword in ["subtotal", "tax", "total"]):
                spatial_groups.append(
                    SpatialGroup(
                        line_number=line_num,
                        words=words,
                        group_type="financial_summary",
                        has_price=has_price
                    )
                )
    
    state["spatial_groups"] = spatial_groups
    state["line_assignments"] = {
        line_num: [w.word_id for w in words]
        for line_num, words in lines.items()
    }
    
    return state
```

### Node 2: Line Item Detection

```python
async def line_item_detection_node(state: ExtendedLabelingState) -> ExtendedLabelingState:
    """Identify product names, prices, and quantities"""
    
    extended_labels = state.get("extended_labels", {})
    
    # Process each potential line item
    for group in state["spatial_groups"]:
        if group.group_type != "potential_line_item":
            continue
        
        # Find the price on this line
        line_prices = [
            price for col in state["currency_columns"]
            for price in col.prices
            if price.word.line_number == group.line_number
        ]
        
        if not line_prices:
            continue
        
        # Usually rightmost price is the line total
        line_price = max(line_prices, key=lambda p: p.word.x)
        
        # Label the price
        extended_labels[line_price.word.word_id] = {
            "label_type": "PRODUCT_PRICE",
            "confidence": 0.8,
            "source": "spatial_line_analysis",
            "validation_status": "pending"
        }
        
        # Find quantity indicators (e.g., "2 @", "QTY: 3")
        quantity_word = None
        for word in group.words:
            if "@" in word.text or "qty" in word.text.lower():
                quantity_word = word
                break
        
        if quantity_word:
            extended_labels[quantity_word.word_id] = {
                "label_type": "PRODUCT_QUANTITY",
                "confidence": 0.9,
                "source": "pattern_match",
                "validation_status": "pending"
            }
        
        # Everything else on the line before the price is likely product name
        product_words = [
            w for w in group.words
            if w.x < line_price.word.x - 20  # Buffer
            and w.word_id != (quantity_word.word_id if quantity_word else None)
        ]
        
        if product_words:
            # Group consecutive words as product name
            for word in product_words:
                extended_labels[word.word_id] = {
                    "label_type": "PRODUCT_NAME",
                    "confidence": 0.7,
                    "source": "spatial_inference",
                    "validation_status": "pending",
                    "group_id": f"line_{group.line_number}"  # Track which words go together
                }
    
    state["extended_labels"] = extended_labels
    state["tier_results"]["line_items"] = {
        "found_products": len([l for l in extended_labels.values() if l["label_type"] == "PRODUCT_NAME"]),
        "found_prices": len([l for l in extended_labels.values() if l["label_type"] == "PRODUCT_PRICE"]),
        "found_quantities": len([l for l in extended_labels.values() if l["label_type"] == "PRODUCT_QUANTITY"])
    }
    
    return state
```

### Node 3: Financial Analysis

```python
async def financial_analysis_node(state: ExtendedLabelingState) -> ExtendedLabelingState:
    """Find subtotal, tax, and discounts using math validation"""
    
    extended_labels = state.get("extended_labels", {})
    
    # We already have GRAND_TOTAL from essential labels
    grand_total = None
    for word_id, label in state["essential_labels"].items():
        if label["label_type"] == "GRAND_TOTAL":
            grand_total = next(
                w for w in state["receipt_words"] 
                if w.word_id == word_id
            )
            break
    
    if not grand_total:
        return state
    
    # Use math solutions to find financial relationships
    for solution in state["math_solutions"]:
        if solution["type"] == "items_tax_total":
            # Pattern: items + tax = total
            # We know the total, find tax
            tax_amount = solution["tax"]
            
            # Find currency value matching tax amount
            for col in state["currency_columns"]:
                for price in col.prices:
                    if abs(price.value - tax_amount) < 0.01:
                        # Check nearby text for "tax"
                        nearby_words = [
                            w for w in state["receipt_words"]
                            if abs(w.line_number - price.word.line_number) <= 1
                        ]
                        
                        if any("tax" in w.text.lower() for w in nearby_words):
                            extended_labels[price.word.word_id] = {
                                "label_type": "TAX",
                                "confidence": 0.9,
                                "source": "math_validation",
                                "validation_status": "pending"
                            }
        
        elif solution["type"] == "subtotal_tax_total":
            # Pattern: subtotal + tax = total
            subtotal_amount = solution["subtotal"]
            
            # Find currency value matching subtotal
            for col in state["currency_columns"]:
                for price in col.prices:
                    if abs(price.value - subtotal_amount) < 0.01:
                        # Should be before tax and total
                        if price.word.y < grand_total.y:
                            extended_labels[price.word.word_id] = {
                                "label_type": "SUBTOTAL",
                                "confidence": 0.85,
                                "source": "math_validation",
                                "validation_status": "pending"
                            }
    
    # Look for discounts (negative values or specific keywords)
    for group in state["spatial_groups"]:
        text = " ".join(w.text for w in group.words).lower()
        if any(disc in text for disc in ["discount", "coupon", "savings", "- $"]):
            for word in group.words:
                if word.word_id not in extended_labels:
                    extended_labels[word.word_id] = {
                        "label_type": "DISCOUNT",
                        "confidence": 0.7,
                        "source": "keyword_match",
                        "validation_status": "pending"
                    }
    
    state["extended_labels"] = extended_labels
    return state
```

### Node 4: Payment Detection

```python
async def payment_detection_node(state: ExtendedLabelingState) -> ExtendedLabelingState:
    """Find payment method and change information"""
    
    extended_labels = state.get("extended_labels", {})
    
    # Payment keywords
    payment_patterns = {
        "PAYMENT_METHOD": [
            "cash", "credit", "debit", "visa", "mastercard", 
            "amex", "discover", "card", "payment"
        ],
        "CHANGE": ["change", "change due", "your change"],
        "CARD_LAST_FOUR": [r"\*+\d{4}", r"ending \d{4}"]
    }
    
    # Look in bottom portion of receipt
    bottom_words = [
        w for w in state["receipt_words"]
        if w.y > 0.7 * max(w2.y for w2 in state["receipt_words"])
    ]
    
    for word in bottom_words:
        if word.word_id in extended_labels or word.word_id in state["essential_labels"]:
            continue
        
        text_lower = word.text.lower()
        
        # Check payment method
        if any(pm in text_lower for pm in payment_patterns["PAYMENT_METHOD"]):
            extended_labels[word.word_id] = {
                "label_type": "PAYMENT_METHOD",
                "confidence": 0.8,
                "source": "keyword_match",
                "validation_status": "pending"
            }
        
        # Check for change
        elif any(ch in text_lower for ch in payment_patterns["CHANGE"]):
            extended_labels[word.word_id] = {
                "label_type": "CHANGE",
                "confidence": 0.8,
                "source": "keyword_match",
                "validation_status": "pending"
            }
    
    state["extended_labels"] = extended_labels
    return state
```

### Node 5: Validate Extended Labels

```python
async def validate_extended_labels_node(state: ExtendedLabelingState) -> ExtendedLabelingState:
    """Validate the extended labels for consistency"""
    
    validation_notes = []
    
    # Check: Do product prices sum to subtotal (if found)?
    product_prices = [
        get_price_value(word_id, state)
        for word_id, label in state["extended_labels"].items()
        if label["label_type"] == "PRODUCT_PRICE"
    ]
    
    subtotal = next(
        (get_price_value(word_id, state)
         for word_id, label in state["extended_labels"].items()
         if label["label_type"] == "SUBTOTAL"),
        None
    )
    
    if subtotal and product_prices:
        if abs(sum(product_prices) - subtotal) < 0.01:
            validation_notes.append("✓ Product prices sum to subtotal")
        else:
            validation_notes.append("⚠ Product prices don't sum to subtotal")
    
    # Check: Is tax reasonable (typically 5-10% of subtotal)?
    tax = next(
        (get_price_value(word_id, state)
         for word_id, label in state["extended_labels"].items()
         if label["label_type"] == "TAX"),
        None
    )
    
    if tax and subtotal:
        tax_rate = tax / subtotal
        if 0.03 <= tax_rate <= 0.15:  # 3-15% is reasonable
            validation_notes.append(f"✓ Tax rate {tax_rate:.1%} is reasonable")
        else:
            validation_notes.append(f"⚠ Tax rate {tax_rate:.1%} seems unusual")
    
    # Calculate confidence scores
    state["confidence_scores"] = {
        "line_items": calculate_tier_confidence(state, "TIER_2_LINE_ITEMS"),
        "financial": calculate_tier_confidence(state, "TIER_3_FINANCIAL"),
        "payment": calculate_tier_confidence(state, "TIER_4_PAYMENT")
    }
    
    state["validation_notes"] = validation_notes
    
    return state
```

## Integration with Main Workflow

```python
# In main LangGraph workflow
async def extended_labeling_decision_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Decide if we need extended labeling"""
    
    # Check if we have Essential 4
    essential_labels = extract_essential_labels(state["initial_labels"])
    
    has_all_essential = all(
        label in essential_labels 
        for label in ["MERCHANT_NAME", "DATE", "TIME", "GRAND_TOTAL"]
    )
    
    if has_all_essential and not state.get("extended_labels"):
        # Run extended labeling sub-workflow
        extended_workflow = create_extended_labeling_workflow()
        
        extended_state = {
            "receipt_words": state["receipt_words"],
            "essential_labels": essential_labels,
            "currency_columns": state["currency_columns"],
            "math_solutions": state["math_solutions"]
        }
        
        extended_result = await extended_workflow.arun(extended_state)
        
        # Merge results
        state["extended_labels"] = extended_result["extended_labels"]
        state["all_labels"] = {
            **essential_labels,
            **extended_result["extended_labels"]
        }
        
        # Decide next step based on coverage
        if extended_result["confidence_scores"]["line_items"] < 0.5:
            state["next_action"] = "gpt_line_item_analysis"
        else:
            state["next_action"] = "final_validation"
    
    return state
```

## Benefits of Sub-Workflow Approach

1. **Modular**: Extended labeling is self-contained
2. **Conditional**: Only runs after Essential 4 are found
3. **Intelligent**: Uses spatial and mathematical relationships
4. **Validating**: Checks its own work
5. **Targeted**: Can identify exactly what's missing for GPT

## When to Use GPT in Extended Workflow

The extended workflow identifies gaps:
- **No line items found** → GPT with spatial context
- **Financial math doesn't add up** → GPT to find missing values
- **No payment info** → May not exist, optional GPT

This approach ensures you maximize pattern-based extraction before calling GPT, and when you do call GPT, you know exactly what you're looking for.