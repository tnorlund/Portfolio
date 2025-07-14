# Smart Decision Engine Design Context

## Overview

This document provides context for designing a Smart Decision Engine that will determine when pattern detection is sufficient for labeling receipt words versus when GPT/LLM assistance is needed. The goal is to achieve 84% cost reduction by minimizing unnecessary AI API calls.

## Current State

### Pattern Detection Capabilities (Phase 2-3 Complete)

We have a robust pattern detection system with the following capabilities:

1. **Parallel Pattern Detection** (`ParallelPatternOrchestrator`)
   - Runs multiple detectors simultaneously using asyncio
   - Sub-millisecond performance (avg 0.49ms on 9,594 words)
   - 4 optimization levels: Legacy, Basic, Optimized, Advanced

2. **Available Pattern Detectors**
   - **Currency**: Detects prices, totals, tax amounts with contextual classification
   - **DateTime**: Finds dates and times in various formats
   - **Contact**: Identifies phone numbers, emails, addresses
   - **Quantity**: Detects item quantities ("2 @ $1.99", "1.5 lbs")
   - **Merchant-Specific**: Queries known patterns for specific stores (via Pinecone)

3. **Pattern Detection Output Format**
   ```python
   {
       "detected_patterns": [
           {
               "receipt_word_id": "uuid",
               "text": "$12.99",
               "label": "UNIT_PRICE",
               "confidence": 0.95,
               "pattern_type": "currency",
               "metadata": {
                   "currency_type": "price",
                   "position_percentile": 0.45
               }
           }
       ],
       "processing_time_ms": 0.49,
       "optimization_stats": {...}
   }
   ```

### Core Labels to Assign

From `receipt_label/constants.py`, we need to assign these labels:

```python
CORE_LABELS = {
    # Essential (must-have for valid receipt)
    "MERCHANT_NAME": "Trading name or brand of the store",
    "DATE": "Calendar date of the transaction",
    "GRAND_TOTAL": "Final amount due after all discounts, taxes and fees",
    
    # Important transaction info
    "TIME": "Time of the transaction",
    "PAYMENT_METHOD": "Payment instrument summary",
    "SUBTOTAL": "Sum of all line totals before tax",
    "TAX": "Any tax line (sales tax, VAT, bottle deposit)",
    
    # Line items (need at least some)
    "PRODUCT_NAME": "Descriptive text of a purchased product",
    "QUANTITY": "Numeric count or weight of the item",
    "UNIT_PRICE": "Price per single unit/weight before tax",
    "LINE_TOTAL": "Extended price for that line",
    
    # Store info
    "PHONE_NUMBER": "Telephone number printed on receipt",
    "WEBSITE": "Web or email address",
    "ADDRESS_LINE": "Full address line printed on receipt",
    
    # Other
    "LOYALTY_ID": "Customer loyalty/rewards identifier",
    "STORE_HOURS": "Printed business hours",
    "COUPON": "Coupon code or description",
    "DISCOUNT": "Any non-coupon discount line item"
}
```

### Noise Detection

We have `is_noise_word()` that identifies ~30% of OCR words as noise:
- Single punctuation marks
- Separators (|, /, \\, etc.)
- Non-alphanumeric artifacts
- Very short fragments

These should be stored in DynamoDB but skipped for labeling/embedding.

## Decision Engine Requirements

### Primary Goal
Determine when pattern detection provides sufficient labeling coverage to skip GPT entirely, achieving cost savings while maintaining labeling quality.

### Key Decision Factors

1. **Essential Label Coverage**
   - Must have: `MERCHANT_NAME`, `DATE`, `GRAND_TOTAL`
   - Without these, the receipt is incomplete regardless of other labels

2. **Meaningful Word Coverage**
   - Calculate: (labeled meaningful words) / (total meaningful words)
   - Meaningful = non-noise words
   - Target: >90% coverage to skip GPT

3. **Ambiguity Threshold**
   - If <5 meaningful words remain unlabeled → Skip GPT
   - The cost of calling GPT for so few words isn't justified

4. **Pattern Confidence**
   - High confidence patterns (>0.9): dates, phones, clear currency
   - Medium confidence (0.7-0.9): contextual currency classification
   - Low confidence (<0.7): may need GPT validation

5. **Merchant-Specific Knowledge**
   - If merchant patterns from Pinecone match well → Higher confidence
   - Unknown merchants → May need GPT for product names

### Decision Logic Flow

```
1. Run all pattern detectors in parallel
2. Filter out noise words
3. Check essential labels:
   - If missing any → Consider GPT
4. Calculate coverage percentage:
   - If >90% → Skip GPT
   - If 70-90% → Check unlabeled word count
   - If <70% → Use GPT
5. Evaluate remaining unlabeled words:
   - If <5 meaningful words → Skip GPT
   - If mostly ambiguous currency → Use GPT
   - If mostly product names with known merchant → Skip GPT
```

### Cost-Benefit Analysis

**Pattern Detection Costs:**
- Processing time: ~0.5ms per receipt
- Pinecone query: ~$0.0001 per receipt
- Total: ~$0.0001 per receipt

**GPT Costs (if used):**
- GPT-4: ~$0.05 per receipt (1K tokens in, 500 out)
- Processing time: ~2-3 seconds
- Total: ~$0.05 per receipt

**Target Metrics:**
- Skip GPT for 84% of receipts
- Maintain >95% labeling accuracy
- Reduce average cost from $0.05 to $0.008 per receipt

### Integration Points

1. **Input**: 
   - List of `ReceiptWord` objects
   - Pattern detection results
   - Optional merchant metadata

2. **Output**:
   - Decision: use GPT or not
   - If no GPT: Final labels from patterns
   - If GPT needed: Context for GPT prompt focusing on unlabeled words
   - Confidence score and reasoning

3. **Downstream Usage**:
   - Apply labels to DynamoDB
   - Generate embeddings for Pinecone
   - Update merchant pattern knowledge

## Examples to Consider

### Example 1: Well-Structured Grocery Receipt
- Merchant: "Whole Foods Market" (found by pattern)
- Date/Time: Found by datetime patterns
- Products: Clear pattern "ORGANIC BANANAS", "MILK 2%"
- Prices: All detected with high confidence
- **Decision**: Skip GPT (95% coverage, all essential labels)

### Example 2: Restaurant Receipt with Handwriting
- Merchant: Found in header
- Date: Found
- Items: Some unclear "SPEC ROLLS", "2 BEERS"
- Tip/Total: Handwritten, patterns uncertain
- **Decision**: Use GPT for tip/total clarification

### Example 3: Gas Station Receipt
- Simple format: "REGULAR", "GALLONS", "PRICE/GAL"
- All patterns match with high confidence
- Only 15 total words, 13 labeled
- **Decision**: Skip GPT (2 unlabeled words below threshold)

## Questions for Design Consideration

1. **Fallback Strategy**: If patterns find essential labels but low product coverage, should we:
   - Accept partial labeling?
   - Use GPT only for products?
   - Have a "minimal GPT" mode?

2. **Confidence Thresholds**: Should thresholds be:
   - Fixed for all merchants?
   - Learned per merchant over time?
   - Adjustable based on receipt quality?

3. **Hybrid Approach**: Can we design a "focused GPT" mode that:
   - Only sends unlabeled words to GPT?
   - Provides pattern labels as context?
   - Reduces token usage by 60-80%?

4. **Quality Assurance**: How do we:
   - Measure labeling quality without ground truth?
   - Detect when patterns are failing?
   - Trigger retraining or pattern updates?

5. **Special Cases**:
   - Receipts in multiple languages
   - Damaged/partial receipts
   - Non-standard formats (handwritten, etc.)

## Implementation Considerations

1. **Performance**: Decision must be fast (<10ms) to not negate pattern detection benefits

2. **Configurability**: Thresholds should be adjustable without code changes

3. **Monitoring**: Track decision metrics:
   - GPT skip rate
   - Coverage percentages
   - Confidence distributions
   - Cost savings achieved

4. **A/B Testing**: Ability to compare different decision strategies

5. **Graceful Degradation**: If pattern detection fails, fallback to GPT

## Success Metrics

1. **Primary**: Achieve 84% GPT skip rate
2. **Quality**: Maintain >95% labeling accuracy
3. **Cost**: Reduce from $0.05 to <$0.01 per receipt
4. **Speed**: Total processing <100ms for pattern-only path
5. **Coverage**: Label >90% of meaningful words via patterns