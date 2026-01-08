# Constellation Pattern Detection Analysis

## Overview

Constellation pattern detection identifies groups of labels that frequently appear together on receipts and validates their spatial relationships. When a label appears in an unexpected position relative to its constellation members, it's flagged for review.

## How It Works

1. **Find Constellation**: Identify label groups that co-occur frequently (e.g., ADDRESS_LINE + DATE + TIME)
2. **Calculate Centroid**: Compute the center point of the label group
3. **Learn Expected Offsets**: Each label type has a learned offset from the centroid based on historical data
4. **Detect Anomalies**: Flag labels whose actual position deviates significantly from expected

## Pairwise Drill-Down Enhancement

The constellation detection flags an entire label type (e.g., "DATE is displaced"). The pairwise drill-down then analyzes each word with that label to identify the specific culprit(s).

For each word of the flagged label:
- Calculate distance from expected position (centroid + expected offset)
- Words with deviation > 0.4 are marked as likely culprits
- Results sorted by deviation descending

## Example Analysis

### Example 1: DATE Anomaly

**Constellation**: ADDRESS_LINE + DATE + TIME

**Detection**: 'DATE' flagged as displaced from expected position

**Drill-Down Results**:
| Word | Position (y) | Deviation | Status |
|------|--------------|-----------|--------|
| "07/08/2024" | 0.992 | 0.67 | CULPRIT |
| "Monday," | 0.144 | 0.28 | OK |
| "2024" | 0.144 | 0.24 | OK |
| "July" | 0.144 | 0.20 | OK |
| "8," | 0.144 | 0.20 | OK |

**Analysis**: The actual transaction date ("Monday, July 8, 2024") is at the top of the receipt where expected. The word "07/08/2024" near the bottom is likely mislabeled - it's probably a transaction ID, order number, or reference code that happens to look like a date.

### Example 2: PAYMENT_METHOD Anomaly

**Constellation**: ADDRESS_LINE + PAYMENT_METHOD + WEBSITE

**Detection**: 'PAYMENT_METHOD' flagged as displaced

**Drill-Down Results**:
| Word | Position (y) | Deviation | Status |
|------|--------------|-----------|--------|
| "cash" | 0.025 | 0.56 | CULPRIT |
| "XXXXXXXХXXXX5061" | 0.844 | 0.42 | CULPRIT |
| "CARD" | 0.845 | 0.40 | OK |
| "MASTERCARD" | 0.858 | 0.36 | OK |
| "Method:Cntctless" | 0.855 | 0.36 | OK |
| "#:" | 0.845 | 0.34 | OK |
| "Entry" | 0.856 | 0.29 | OK |
| "CREDIT" | 0.538 | 0.20 | OK |

**Analysis**: This reveals significant data quality issues:
- The masked card number "XXXXXXXХXXXX5061" is the **only valid payment method** - it's the actual card used
- "cash" at the bottom is mislabeled (likely "cash back" or policy text)
- Words like "MASTERCARD", "CARD", "CREDIT", "Entry", "Method:Cntctless", "#:" are payment-related **metadata**, not the payment method itself
- The labeling conflates "payment method" (the actual card/cash used) with "payment information" (card type, entry method, etc.)

## Data Quality Insights

These examples highlight labeling inconsistencies in the training data:

1. **Overly broad labels**: PAYMENT_METHOD captures both the actual payment instrument and descriptive text about payment processing
2. **Context-insensitive labeling**: Words that look like dates/payment methods are labeled as such regardless of their semantic role on the receipt
3. **Position as signal**: The constellation detection successfully identifies these issues because mislabeled words appear in unexpected positions

## Architecture Decision: Constellation Replaces Pairwise

The constellation approach with drill-down replaces the separate pairwise geometric check. The pairwise patterns (like SUBTOTAL→GRAND_TOTAL positioning) emerge naturally within constellation relationships.

**Why this works:**
- Constellations capture co-occurring label groups
- Positional relationships are encoded in the expected offsets from centroid
- Drill-down pinpoints specific culprit words within a flagged label type
- No need for separate pairwise validation logic

## Step Function Integration

### Current Flow
```
LayoutLM Inference → Geometric Validation → LLM Review → Output
```

### Updated Flow with Drill-Down
```
LayoutLM Inference → Constellation Check → Drill-Down → LLM Review → Output
                                              ↓
                                    Culprit word context
```

### Drill-Down Output for LLM Reviewer

When a constellation anomaly is detected, the drill-down results should be passed to the LLM reviewer:

```json
{
  "flagged_label": "PAYMENT_METHOD",
  "constellation": ["ADDRESS_LINE", "PAYMENT_METHOD", "WEBSITE"],
  "reasoning": "PAYMENT_METHOD displaced from expected position",
  "drill_down": [
    {
      "word": "cash",
      "position": { "x": 0.42, "y": 0.025 },
      "deviation": 0.56,
      "is_culprit": true
    },
    {
      "word": "XXXXXXXХXXXX5061",
      "position": { "x": 0.75, "y": 0.844 },
      "deviation": 0.42,
      "is_culprit": true
    },
    {
      "word": "MASTERCARD",
      "position": { "x": 0.25, "y": 0.858 },
      "deviation": 0.36,
      "is_culprit": false
    }
  ]
}
```

This gives the LLM reviewer:
1. **Which words to focus on** - the culprit(s) identified by position analysis
2. **Context for decision** - deviation scores and positions
3. **Comparison baseline** - non-culprit words show where the label "should" be

### Agent Implementation

The agent already has the constellation detection logic. To add drill-down:

1. When constellation flags a label, calculate deviation for each word with that label
2. Include drill-down results in the evaluation output
3. Pass drill-down context to the LLM subagent for review

This keeps the step function's separation of concerns:
- **Geometric step**: Constellation detection + drill-down calculation
- **LLM step**: Review flagged words with drill-down context

## Next Steps

1. Add drill-down calculation to the geometric evaluation step in the step function
2. Update the LLM reviewer prompt to use drill-down context
3. Evaluate whether drill-down improves LLM reviewer accuracy on flagged labels
