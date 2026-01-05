# Hybrid Receipt Extraction Pipeline

## Overview

This document describes a hybrid approach combining LayoutLM (spatial/format pattern recognition) with ChromaDB (semantic similarity) and rule-based post-processing for optimal receipt entity extraction.

## Architecture

```
Receipt Image + OCR
        │
        ▼
┌───────────────────┐
│  LayoutLM Model   │  Spatial + Format patterns
│  (8 labels)       │  ~70% average accuracy
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  Spatial Rules    │  Position-based inference
│  (line grouping)  │  Same-line detection
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  ChromaDB Lookup  │  Semantic similarity
│  (PRODUCT_NAME)   │  2,085 labeled examples
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  Context Rules    │  Keyword + position
│  (amount subtypes)│  "Total", "Tax", etc.
└───────────────────┘
        │
        ▼
   Final Labels
```

## LayoutLM Training Configuration

### Labels (8 total)

LayoutLM should be trained on labels it excels at (spatial/format patterns):

| Label | Type | Expected Accuracy | Notes |
|-------|------|-------------------|-------|
| MERCHANT_NAME | Spatial | ~70% | Top of receipt, centered |
| DATE | Format | ~85% | MM/DD/YY patterns |
| TIME | Format | ~92% | HH:MM patterns |
| AMOUNT | Format+Spatial | ~55% | Merged currency labels, $X.XX right-aligned |
| ADDRESS | Spatial | ~55% | Merged ADDRESS_LINE + PHONE_NUMBER |
| WEBSITE | Format | ~70% | Contains .com, www |
| STORE_HOURS | Format | ~71% | Time range patterns |
| PAYMENT_METHOD | Format+Spatial | ~55% | Card types, "VISA", "CASH" |

### Labels NOT trained (handled by post-processing)

| Label | Reason | Alternative |
|-------|--------|-------------|
| PRODUCT_NAME | 7% accuracy, no visual pattern | ChromaDB semantic lookup |
| QUANTITY | 2.5% accuracy, just numbers | Position rules |
| LINE_TOTAL | Collapsed into AMOUNT | Context rules |
| UNIT_PRICE | Collapsed into AMOUNT | Position rules |
| GRAND_TOTAL | Collapsed into AMOUNT | Context rules |
| TAX | Collapsed into AMOUNT | Context rules |
| SUBTOTAL | Collapsed into AMOUNT | Context rules |
| COUPON | 0% accuracy, low support | Context rules |
| DISCOUNT | 0% accuracy, low support | Context rules |
| LOYALTY_ID | 0% accuracy, low support | Context rules |

### Training Parameters

```python
# Merge preset for hybrid pipeline
HYBRID_MERGE_PRESET = {
    # Merge all currency amounts
    "AMOUNT": ["LINE_TOTAL", "UNIT_PRICE", "GRAND_TOTAL", "TAX", "SUBTOTAL"],
    # Merge address-related
    "ADDRESS": ["ADDRESS_LINE", "PHONE_NUMBER"],
    # Keep DATE and TIME separate (both have strong format patterns)
    # NO: "DATE": ["TIME"]  -- they perform better separately
}

# Labels to include (drop low-support/low-accuracy labels)
HYBRID_ALLOWED_LABELS = [
    "MERCHANT_NAME",
    "DATE",
    "TIME",
    "AMOUNT",      # merged
    "ADDRESS",     # merged
    "WEBSITE",
    "STORE_HOURS",
    "PAYMENT_METHOD",
]

# Training hyperparameters (slower learning for better convergence)
TRAINING_CONFIG = {
    "learning_rate": "2e-5",      # Slower than default 5e-5
    "epochs": "15",               # More epochs
    "warmup_ratio": "0.15",       # More warmup
    "early_stopping_patience": "5",
    "batch_size": "8",
}
```

### Lambda Invocation

```bash
aws lambda invoke \
  --function-name layoutlm-sagemaker-start-training-d28f7b4 \
  --cli-binary-format raw-in-base64-out \
  --payload '{
    "job_name": "layoutlm-hybrid-8-labels",
    "hyperparameters": {
      "learning_rate": "2e-5",
      "warmup_ratio": "0.15",
      "epochs": "15",
      "early_stopping_patience": "5",
      "label_merges": "{\"AMOUNT\": [\"LINE_TOTAL\", \"UNIT_PRICE\", \"GRAND_TOTAL\", \"TAX\", \"SUBTOTAL\"], \"ADDRESS\": [\"ADDRESS_LINE\", \"PHONE_NUMBER\"]}",
      "allowed_labels": "MERCHANT_NAME,DATE,TIME,AMOUNT,ADDRESS,WEBSITE,STORE_HOURS,PAYMENT_METHOD"
    }
  }' \
  /tmp/response.json
```

## Post-Processing Pipeline

### Step 1: Spatial Grouping

Group tokens into lines based on y-coordinate proximity:

```python
def group_tokens_by_line(tokens, y_threshold=10):
    """Group tokens that share similar y-coordinates into lines."""
    lines = []
    sorted_tokens = sorted(tokens, key=lambda t: (t.bbox.y, t.bbox.x))

    current_line = [sorted_tokens[0]]
    for token in sorted_tokens[1:]:
        if abs(token.bbox.y - current_line[0].bbox.y) < y_threshold:
            current_line.append(token)
        else:
            lines.append(sorted(current_line, key=lambda t: t.bbox.x))
            current_line = [token]
    lines.append(sorted(current_line, key=lambda t: t.bbox.x))

    return lines
```

### Step 2: ChromaDB PRODUCT_NAME Lookup

For tokens on same line as AMOUNT, query ChromaDB:

```python
def identify_product_names(lines, chroma_client, threshold=0.3):
    """Use ChromaDB to identify PRODUCT_NAME tokens."""
    for line in lines:
        amount_tokens = [t for t in line if t.label == "AMOUNT"]
        if not amount_tokens:
            continue

        # Get rightmost amount (likely LINE_TOTAL)
        rightmost_amount = max(amount_tokens, key=lambda t: t.bbox.x)

        # Candidate tokens: left of the amount, currently unlabeled
        candidates = [
            t for t in line
            if t.bbox.x < rightmost_amount.bbox.x
            and t.label in ["O", None]
            and not looks_like_number(t.text)
        ]

        for token in candidates:
            # Query ChromaDB for similar PRODUCT_NAME words
            results = chroma_client.query(
                collection_name="words",
                query_texts=[token.text],
                n_results=5,
                where={"label": "PRODUCT_NAME"}
            )

            # If close match found, assign label
            if results["distances"][0][0] < threshold:
                token.label = "PRODUCT_NAME"
                token.confidence = 1 - results["distances"][0][0]
```

### Step 3: Amount Subtype Classification

Use context keywords and position to classify AMOUNT subtypes:

```python
def classify_amount_subtypes(lines):
    """Classify AMOUNT tokens into specific subtypes."""

    # Keywords for each subtype
    KEYWORDS = {
        "GRAND_TOTAL": ["total", "due", "amount due", "grand total", "balance"],
        "SUBTOTAL": ["subtotal", "sub total", "sub-total"],
        "TAX": ["tax", "hst", "gst", "pst", "vat"],
        "DISCOUNT": ["discount", "savings", "save"],
    }

    for line in lines:
        amount_tokens = [t for t in line if t.label == "AMOUNT"]
        line_text = " ".join(t.text.lower() for t in line)

        for amount in amount_tokens:
            # Check for keyword matches
            for subtype, keywords in KEYWORDS.items():
                if any(kw in line_text for kw in keywords):
                    amount.subtype = subtype
                    break

            # Default: if in a line with PRODUCT_NAME, it's LINE_TOTAL
            if not hasattr(amount, 'subtype'):
                has_product = any(t.label == "PRODUCT_NAME" for t in line)
                if has_product:
                    amount.subtype = "LINE_TOTAL"

    # Position-based: bottom-most unlabeled amount is likely GRAND_TOTAL
    all_amounts = [t for line in lines for t in line if t.label == "AMOUNT"]
    unlabeled = [a for a in all_amounts if not hasattr(a, 'subtype')]
    if unlabeled:
        bottom_amount = max(unlabeled, key=lambda t: t.bbox.y)
        bottom_amount.subtype = "GRAND_TOTAL"
```

### Step 4: QUANTITY Detection

Position-based detection for quantities:

```python
def detect_quantities(lines):
    """Detect QUANTITY tokens based on position relative to PRODUCT_NAME and AMOUNT."""
    for line in lines:
        product_tokens = [t for t in line if t.label == "PRODUCT_NAME"]
        amount_tokens = [t for t in line if t.label == "AMOUNT"]

        if not product_tokens or not amount_tokens:
            continue

        rightmost_product = max(product_tokens, key=lambda t: t.bbox.x)
        leftmost_amount = min(amount_tokens, key=lambda t: t.bbox.x)

        # Look for small integers between product and amount
        for token in line:
            if (token.bbox.x > rightmost_product.bbox.x + rightmost_product.bbox.w
                and token.bbox.x < leftmost_amount.bbox.x
                and token.label in ["O", None]):

                # Check if it's a small integer
                if token.text.isdigit() and int(token.text) < 100:
                    token.label = "QUANTITY"
```

## Expected Output Labels

After full pipeline, each receipt will have:

### From LayoutLM (direct)
- MERCHANT_NAME
- DATE
- TIME
- ADDRESS (merged)
- WEBSITE
- STORE_HOURS
- PAYMENT_METHOD
- AMOUNT (coarse)

### From Post-Processing
- PRODUCT_NAME (ChromaDB)
- QUANTITY (position rules)
- GRAND_TOTAL (context rules)
- SUBTOTAL (context rules)
- TAX (context rules)
- LINE_TOTAL (context rules)
- UNIT_PRICE (position rules)

## Metrics to Track

### LayoutLM Model Metrics
- Per-label F1, precision, recall
- Confusion matrix
- Training time

### Post-Processing Metrics
- ChromaDB query latency
- PRODUCT_NAME match rate (% of candidates that match)
- Average similarity score for matches

### End-to-End Metrics
- Overall entity extraction accuracy
- Line-item extraction rate (% of line items with PRODUCT_NAME + LINE_TOTAL)
- Receipt completeness (% of expected fields extracted)

## Future Improvements

1. **Train ChromaDB on more PRODUCT_NAME examples** - Currently 2,085 examples
2. **Add confidence calibration** - Combine LayoutLM confidence with ChromaDB similarity
3. **Region-based processing** - Segment receipt into header/items/totals first
4. **Fine-tune embeddings** - Train custom embeddings for receipt-specific vocabulary
