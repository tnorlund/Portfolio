# Spatial Anomaly Detection Using N Closest Neighbors

## Overview

Anomaly detection using spatial relationships identifies labels that have unusual spatial contexts compared to historical patterns. This helps catch mislabeled words, OCR errors, and unusual receipt layouts.

## How It Works

### 1. **Building Normal Profiles**

For each label type (e.g., `GRAND_TOTAL`, `TAX`, `SUBTOTAL`), we analyze historical spatial data to build a "normal" profile:

```
GRAND_TOTAL normal profile:
- Nearby labels (N=25 closest):
  - TAX: appears in 95% of cases, avg distance: 0.15
  - SUBTOTAL: appears in 90% of cases, avg distance: 0.20
  - MERCHANT_NAME: appears in 5% of cases, avg distance: 0.80
  - PRODUCT_NAME: appears in 2% of cases, avg distance: 0.85
```

### 2. **Comparing New Labels**

When checking a label, we look at its N closest neighbors and compare against the normal profile:

```
New GRAND_TOTAL label:
- Nearby labels:
  - PRODUCT_NAME: distance 0.10 (unusual - should be far)
  - MERCHANT_NAME: distance 0.12 (unusual - should be far)
  - TAX: NOT FOUND (unusual - should be nearby)
  - SUBTOTAL: NOT FOUND (unusual - should be nearby)
```

### 3. **Anomaly Scoring**

Calculate an anomaly score based on:

#### **Missing Expected Neighbors**
- If `GRAND_TOTAL` typically has `TAX` nearby (95% of cases), but this one doesn't → **high anomaly score**

#### **Unexpected Close Neighbors**
- If `GRAND_TOTAL` is very close to `PRODUCT_NAME` (which is usually far away) → **high anomaly score**

#### **Distance Deviations**
- If `TAX` is typically 0.15 units away, but this one is 0.50 units away → **medium anomaly score**

#### **Directional Anomalies**
- If `TAX` is typically to the left of `GRAND_TOTAL`, but this one is above → **medium anomaly score**

## Anomaly Score Calculation

```python
anomaly_score = (
    missing_neighbor_penalty * 0.4 +      # Missing expected neighbors
    unexpected_neighbor_penalty * 0.3 +   # Unexpected close neighbors
    distance_deviation_penalty * 0.2 +    # Distance deviations
    directional_deviation_penalty * 0.1   # Directional anomalies
)
```

### Score Interpretation

- **0.0 - 0.3**: Normal (matches expected patterns)
- **0.3 - 0.5**: Slightly unusual (may need review)
- **0.5 - 0.7**: Anomalous (likely needs review)
- **0.7 - 1.0**: Highly anomalous (likely mislabeled or OCR error)

## Use Cases

### 1. **Label Validation**

Before marking a label as `VALID`, check its spatial context:

```python
if anomaly_score > 0.5:
    # Mark as NEEDS_REVIEW instead of VALID
    label.validation_status = "NEEDS_REVIEW"
    label.anomaly_score = anomaly_score
```

### 2. **Batch Validation**

Run anomaly detection on all `PENDING` labels to prioritize review:

```python
# Find labels with high anomaly scores
anomalous_labels = [
    label for label in pending_labels
    if calculate_anomaly_score(label) > 0.5
]

# Prioritize these for manual review
```

### 3. **OCR Error Detection**

Unusual spatial patterns can indicate OCR errors:

```python
# If GRAND_TOTAL is in the middle of product names
# → likely OCR misread a product name as a price
```

### 4. **Receipt Layout Validation**

Detect receipts with unusual layouts:

```python
# If all labels have high anomaly scores
# → receipt may have unusual layout or be corrupted
```

## Implementation Strategy

### Phase 1: Profile Building

1. Query all spatial analyses for a label type
2. Aggregate nearby label frequencies and distances
3. Build statistical profiles (mean, std dev, percentiles)

### Phase 2: Anomaly Detection

1. For each label, fetch its spatial analysis
2. Compare against the normal profile
3. Calculate anomaly score
4. Flag labels above threshold

### Phase 3: Integration

1. Add anomaly score to label validation workflow
2. Use anomaly score to adjust confidence scores
3. Prioritize anomalous labels for review

## Example: GRAND_TOTAL Anomaly Detection

### Normal Profile (from 1000 receipts)

```
Expected nearby labels (within 0.3 units):
- TAX: 95% frequency, avg distance: 0.15, std dev: 0.05
- SUBTOTAL: 90% frequency, avg distance: 0.20, std dev: 0.08
- DATE: 10% frequency, avg distance: 0.25, std dev: 0.10

Expected directions:
- TAX: typically LEFT (60%) or ABOVE (30%)
- SUBTOTAL: typically ABOVE (70%) or LEFT (20%)
```

### Anomalous Case

```
Label: GRAND_TOTAL at position (0.5, 0.8)
Nearby labels:
- PRODUCT_NAME: distance 0.08 (unexpectedly close)
- PRODUCT_NAME: distance 0.12 (unexpectedly close)
- TAX: NOT FOUND (expected but missing)
- SUBTOTAL: NOT FOUND (expected but missing)

Anomaly Score: 0.75
→ Highly anomalous: likely mislabeled or OCR error
```

## Benefits

1. **Early Detection**: Catch mislabeled words before they're marked as VALID
2. **Prioritization**: Focus manual review on most suspicious labels
3. **Confidence Adjustment**: Lower confidence for anomalous labels
4. **Pattern Learning**: Improve over time as more data is collected
5. **Merchant-Specific**: Can build profiles per merchant for better accuracy

## Limitations

1. **New Patterns**: Legitimate new receipt layouts may be flagged as anomalies
2. **Sparse Data**: Labels with few examples may have unreliable profiles
3. **Context Missing**: Doesn't consider receipt type or merchant-specific layouts
4. **False Positives**: Unusual but correct labels may be flagged

## Future Enhancements

1. **Merchant-Specific Profiles**: Build separate profiles per merchant
2. **Receipt Type Profiles**: Different profiles for different receipt types
3. **Temporal Patterns**: Consider how patterns change over time
4. **Machine Learning**: Use ML models for more sophisticated anomaly detection
5. **Feedback Loop**: Learn from manual review corrections

