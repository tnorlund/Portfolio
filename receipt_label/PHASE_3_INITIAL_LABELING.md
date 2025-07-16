# Phase 3: Initial Label Assignment Strategy

## Overview

The first pass of label assignment uses CORE_LABELS to identify and tag obvious matches with PENDING validation status. This document defines when to stop assigning labels and transition to validation.

## CORE_LABELS Definition

```python
CORE_LABELS = {
    # Essential Receipt Fields
    "MERCHANT_NAME": {
        "priority": 1,
        "required": True,
        "max_per_receipt": 1,
        "typical_location": "top",
        "patterns": ["store", "restaurant", "shop", "market"]
    },
    
    "DATE": {
        "priority": 1,
        "required": True,
        "max_per_receipt": 1,
        "typical_location": "top_or_bottom",
        "patterns": [r"\d{1,2}/\d{1,2}/\d{2,4}", r"\d{4}-\d{2}-\d{2}"]
    },
    
    "TIME": {
        "priority": 2,
        "required": False,
        "max_per_receipt": 1,
        "typical_location": "near_date",
        "patterns": [r"\d{1,2}:\d{2}\s*[APap][Mm]", r"\d{2}:\d{2}:\d{2}"]
    },
    
    "GRAND_TOTAL": {
        "priority": 1,
        "required": True,
        "max_per_receipt": 1,
        "typical_location": "bottom",
        "patterns": ["total", "amount due", "balance"]
    },
    
    # Line Item Fields
    "PRODUCT_NAME": {
        "priority": 1,
        "required": True,  # At least one
        "max_per_receipt": None,  # Multiple allowed
        "typical_location": "middle",
        "patterns": []  # Context-dependent
    },
    
    "PRODUCT_PRICE": {
        "priority": 1,
        "required": False,
        "max_per_receipt": None,
        "typical_location": "right_aligned",
        "patterns": [r"\$?\d+\.\d{2}"]
    },
    
    "PRODUCT_QUANTITY": {
        "priority": 2,
        "required": False,
        "max_per_receipt": None,
        "typical_location": "before_price",
        "patterns": [r"\d+\s*@", r"qty:?\s*\d+"]
    },
    
    # Financial Fields
    "SUBTOTAL": {
        "priority": 2,
        "required": False,
        "max_per_receipt": 1,
        "typical_location": "before_tax",
        "patterns": ["subtotal", "sub total", "merchandise"]
    },
    
    "TAX": {
        "priority": 2,
        "required": False,
        "max_per_receipt": 3,  # Could have multiple tax types
        "typical_location": "after_subtotal",
        "patterns": ["tax", "sales tax", "gst", "vat"]
    },
    
    "DISCOUNT": {
        "priority": 3,
        "required": False,
        "max_per_receipt": None,
        "typical_location": "anywhere",
        "patterns": ["discount", "coupon", "savings", "-$"]
    },
    
    # Payment Fields
    "PAYMENT_METHOD": {
        "priority": 3,
        "required": False,
        "max_per_receipt": 2,  # Could have split payment
        "typical_location": "bottom",
        "patterns": ["cash", "credit", "debit", "visa", "mastercard"]
    },
    
    "CHANGE": {
        "priority": 3,
        "required": False,
        "max_per_receipt": 1,
        "typical_location": "bottom",
        "patterns": ["change", "change due"]
    }
}
```

## Initial Assignment Algorithm

```python
class InitialLabelAssigner:
    """First pass label assignment using patterns and positions"""
    
    def __init__(self, core_labels: Dict = CORE_LABELS):
        self.core_labels = core_labels
        self.assignment_stats = defaultdict(int)
        
    async def assign_initial_labels(
        self,
        receipt_id: str,
        words: List[ReceiptWord],
        pattern_results: Dict[str, Any],
        currency_columns: List[PriceColumn]
    ) -> LabelAssignmentResult:
        """
        Assign CORE_LABELS to receipt words with PENDING status.
        
        Returns:
            LabelAssignmentResult with assigned labels and stopping reason
        """
        
        # Initialize tracking
        assigned_labels = {}
        label_counts = defaultdict(int)
        essential_found = set()
        
        # Step 1: Assign from pattern detection results
        assigned_labels.update(
            self._assign_from_patterns(pattern_results, label_counts)
        )
        
        # Step 2: Assign currency values based on position
        assigned_labels.update(
            self._assign_currency_labels(
                currency_columns, 
                words, 
                label_counts
            )
        )
        
        # Step 3: Look for keyword-based labels
        assigned_labels.update(
            self._assign_keyword_labels(
                words,
                assigned_labels,
                label_counts
            )
        )
        
        # Step 4: Check stopping criteria
        stop_reason = self._check_stopping_criteria(
            label_counts,
            essential_found,
            len(words),
            len(assigned_labels)
        )
        
        # Step 5: Set all labels to PENDING status
        for word_id, label_info in assigned_labels.items():
            label_info["validation_status"] = "pending"
            label_info["assignment_method"] = "initial_phase3"
            label_info["assigned_at"] = datetime.utcnow().isoformat()
        
        return LabelAssignmentResult(
            receipt_id=receipt_id,
            assigned_labels=assigned_labels,
            label_counts=dict(label_counts),
            stop_reason=stop_reason,
            should_continue_to_validation=stop_reason.should_validate,
            confidence_score=self._calculate_confidence(
                label_counts,
                essential_found
            )
        )
    
    def _assign_from_patterns(
        self,
        pattern_results: Dict,
        label_counts: Dict
    ) -> Dict[int, LabelInfo]:
        """Assign labels from Phase 2 pattern detection"""
        
        assigned = {}
        
        # Date patterns
        if "date_patterns" in pattern_results:
            for match in pattern_results["date_patterns"]:
                if label_counts["DATE"] < self.core_labels["DATE"]["max_per_receipt"]:
                    assigned[match.word.word_id] = {
                        "label_type": "DATE",
                        "confidence": match.confidence,
                        "source": "pattern"
                    }
                    label_counts["DATE"] += 1
        
        # Merchant patterns
        if "merchant_patterns" in pattern_results:
            for match in pattern_results["merchant_patterns"]:
                if label_counts["MERCHANT_NAME"] < 1:
                    assigned[match.word.word_id] = {
                        "label_type": "MERCHANT_NAME",
                        "confidence": match.confidence,
                        "source": "pattern"
                    }
                    label_counts["MERCHANT_NAME"] += 1
        
        return assigned
    
    def _assign_currency_labels(
        self,
        currency_columns: List[PriceColumn],
        words: List[ReceiptWord],
        label_counts: Dict
    ) -> Dict[int, LabelInfo]:
        """Assign labels to currency values based on position"""
        
        assigned = {}
        
        if not currency_columns:
            return assigned
        
        # Sort currency values by Y position (top to bottom)
        all_prices = []
        for column in currency_columns:
            all_prices.extend(column.prices)
        all_prices.sort(key=lambda p: p.word.y)
        
        # Find the largest value (likely grand total)
        if all_prices:
            largest_price = max(all_prices, key=lambda p: p.value)
            
            # Is it at the bottom?
            if largest_price.word.y > 0.8 * max(w.y for w in words):
                assigned[largest_price.word.word_id] = {
                    "label_type": "GRAND_TOTAL",
                    "confidence": 0.8,
                    "source": "position_heuristic"
                }
                label_counts["GRAND_TOTAL"] += 1
        
        # Assign line item prices (middle section)
        receipt_height = max(w.y for w in words) - min(w.y for w in words)
        middle_prices = [
            p for p in all_prices
            if 0.2 < (p.word.y - min(w.y for w in words)) / receipt_height < 0.7
        ]
        
        for price in middle_prices:
            if label_counts["PRODUCT_PRICE"] < 20:  # Reasonable limit
                assigned[price.word.word_id] = {
                    "label_type": "PRODUCT_PRICE",
                    "confidence": 0.6,
                    "source": "position_heuristic"
                }
                label_counts["PRODUCT_PRICE"] += 1
        
        return assigned
    
    def _check_stopping_criteria(
        self,
        label_counts: Dict[str, int],
        essential_found: Set[str],
        total_words: int,
        labeled_words: int
    ) -> StoppingReason:
        """Determine when to stop initial assignment"""
        
        # Check if we have all required labels
        required_labels = [
            label for label, config in self.core_labels.items()
            if config["required"]
        ]
        
        missing_required = []
        for label in required_labels:
            if label == "PRODUCT_NAME" and label_counts[label] == 0:
                missing_required.append(label)
            elif label != "PRODUCT_NAME" and label_counts[label] < 1:
                missing_required.append(label)
        
        # Stopping criteria
        if not missing_required:
            return StoppingReason(
                code="ALL_REQUIRED_FOUND",
                message="All required labels found",
                should_validate=True
            )
        
        if labeled_words / total_words > 0.5:
            return StoppingReason(
                code="MAJORITY_LABELED",
                message="Over 50% of words labeled",
                should_validate=True
            )
        
        if label_counts["GRAND_TOTAL"] > 0 and label_counts["MERCHANT_NAME"] > 0:
            return StoppingReason(
                code="ESSENTIAL_MINIMUMS_MET",
                message="Have merchant and total",
                should_validate=True
            )
        
        # Need more sophisticated analysis
        return StoppingReason(
            code="NEED_CONTEXT_ANALYSIS",
            message=f"Missing required: {missing_required}",
            should_validate=False,
            next_action="gpt_analysis"
        )
```

## Transition to Validation

```python
class LabelValidationOrchestrator:
    """Orchestrate validation after initial assignment"""
    
    async def should_proceed_to_validation(
        self,
        assignment_result: LabelAssignmentResult
    ) -> ValidationDecision:
        """Decide if we should validate or need more analysis"""
        
        if assignment_result.should_continue_to_validation:
            # We have enough labels to validate
            return ValidationDecision(
                action="VALIDATE",
                reason=assignment_result.stop_reason.message,
                validation_priorities=self._get_validation_priorities(
                    assignment_result
                )
            )
        
        # Need more sophisticated analysis
        if assignment_result.stop_reason.next_action == "gpt_analysis":
            return ValidationDecision(
                action="GPT_ANALYSIS",
                reason="Missing critical labels",
                missing_labels=self._identify_missing_critical(
                    assignment_result
                ),
                context_needed=True
            )
        
        # Edge case: very few labels found
        if assignment_result.confidence_score < 0.3:
            return ValidationDecision(
                action="FULL_GPT_LABELING",
                reason="Low confidence in pattern matching",
                requires_spatial_context=True
            )
```

## Validation Stage Entry Criteria

```python
@dataclass
class ValidationEntryChecklist:
    """Criteria for entering validation stage"""
    
    has_merchant: bool
    has_date: bool
    has_total: bool
    has_line_items: bool
    coverage_percentage: float
    
    @property
    def ready_for_validation(self) -> bool:
        """Check if ready for validation stage"""
        
        # Must have at least merchant OR total
        if not (self.has_merchant or self.has_total):
            return False
        
        # If we have good coverage, proceed
        if self.coverage_percentage > 0.4:
            return True
        
        # If we have all essentials, proceed
        if self.has_merchant and self.has_total:
            return True
        
        return False
    
    @property
    def validation_strategy(self) -> str:
        """Determine validation approach"""
        
        if self.ready_for_validation:
            if self.coverage_percentage > 0.6:
                return "comprehensive_validation"
            elif self.has_merchant and self.has_total:
                return "essential_validation"
            else:
                return "partial_validation"
        else:
            return "need_more_labels"
```

## Workflow Integration

```python
# In LangGraph workflow
async def initial_labeling_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Initial label assignment node"""
    
    # Create assigner
    assigner = InitialLabelAssigner()
    
    # Assign labels
    result = await assigner.assign_initial_labels(
        receipt_id=state["receipt_id"],
        words=state["receipt_words"],
        pattern_results=state["pattern_results"],
        currency_columns=state["currency_columns"]
    )
    
    # Update state
    state["initial_labels"] = result.assigned_labels
    state["label_assignment_result"] = result
    state["validation_entry_checklist"] = ValidationEntryChecklist(
        has_merchant=result.label_counts.get("MERCHANT_NAME", 0) > 0,
        has_date=result.label_counts.get("DATE", 0) > 0,
        has_total=result.label_counts.get("GRAND_TOTAL", 0) > 0,
        has_line_items=result.label_counts.get("PRODUCT_NAME", 0) > 0,
        coverage_percentage=len(result.assigned_labels) / len(state["receipt_words"])
    )
    
    return state

# Conditional edge to validation
def should_validate(state: ReceiptProcessingState) -> str:
    """Decide next node based on labeling results"""
    
    checklist = state["validation_entry_checklist"]
    
    if checklist.ready_for_validation:
        return "validation_node"
    elif checklist.coverage_percentage < 0.2:
        return "gpt_full_labeling_node"
    else:
        return "gpt_gap_filling_node"
```

## Key Principles

1. **All initial labels are PENDING** - Nothing is trusted until validated
2. **Stop when diminishing returns** - Don't over-analyze obvious patterns
3. **Essential fields first** - Merchant, Date, Total are critical
4. **Coverage matters** - If we've labeled 50%+ words, move to validation
5. **Context determines next step** - Low coverage needs GPT, high coverage needs validation

## Summary

The initial labeling phase:
1. Applies CORE_LABELS using patterns and heuristics
2. Assigns everything with PENDING status
3. Stops when essential fields are found OR coverage is sufficient
4. Transitions to validation when ready, or to GPT when more analysis needed

This approach ensures we don't waste time on obvious labels while quickly identifying when we need more sophisticated analysis.