# Self-Correcting Validation System Orchestration

## Overview

The self-correcting validation system runs **per receipt**, processing all PENDING/NEEDS_REVIEW labels for a single receipt together. This allows for:
- **Contradiction detection** across all labels in a receipt
- **Batch processing** of ChromaDB queries
- **Context-aware CoVe** that considers all labels together
- **Efficient resource usage** (single ChromaDB snapshot download per receipt)

---

## Execution Model: Per Receipt

### Why Per Receipt?

1. **Contradiction Detection Requires Full Context**
   - Step 2 needs all labels to detect logical inconsistencies (e.g., multiple GRAND_TOTALs, math mismatches)
   - Can't detect contradictions if processing labels individually

2. **CoVe Benefits from Receipt Context**
   - Step 3 can generate better questions when it sees all labels
   - Can detect missing labels (e.g., if "TOTAL" is invalid, find the actual currency value)

3. **Efficient Resource Usage**
   - Single ChromaDB snapshot download per receipt
   - Batch ChromaDB queries for all labels
   - Single LLM context window for CoVe

4. **Consistent with Existing Architecture**
   - Current `validate-pending-labels-dev-sf` processes per receipt
   - LangGraph workflows operate on full receipts
   - DynamoDB queries are receipt-scoped

---

## Orchestration Architecture

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────┐
│         AWS Step Function: self-correcting-validation-sf    │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
        ┌───────────────────────────────────────┐
        │  ListReceiptsWithPendingLabels        │
        │  (Lambda)                             │
        │  - Query DynamoDB for receipts with   │
        │    PENDING/NEEDS_REVIEW labels        │
        │  - Create manifest with receipt IDs   │
        │  - Upload manifest to S3              │
        └───────────────────────────────────────┘
                            │
                            ▼
        ┌───────────────────────────────────────┐
        │  CheckReceipts (Choice)               │
        │  - total_receipts > 0?                │
        └───────────────────────────────────────┘
                            │
                ┌───────────┴───────────┐
                │                       │
                ▼                       ▼
    ┌───────────────────┐    ┌──────────────────┐
    │  ProcessReceipts  │    │  NoReceipts      │
    │  (Map State)      │    │  (End)           │
    │  MaxConcurrency: 3│    └──────────────────┘
    └───────────────────┘
                │
                ▼
    ┌───────────────────────────────────────────┐
    │  ValidateReceipt                          │
    │  (Lambda - Container)                     │
    │  - Runs Steps 1-5 for ALL labels          │
    │    in a single receipt                    │
    └───────────────────────────────────────────┘
```

---

## Step Function Definition

### State Machine Structure

```json
{
  "Comment": "Self-Correcting Validation System - Processes receipts with PENDING/NEEDS_REVIEW labels",
  "StartAt": "ListReceiptsWithPendingLabels",
  "States": {
    "ListReceiptsWithPendingLabels": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:...:function:list-receipts-with-pending-labels",
      "Next": "CheckReceipts",
      "ResultPath": "$.list_result"
    },
    "CheckReceipts": {
      "Type": "Choice",
      "Choices": [
        {
          "Variable": "$.list_result.total_receipts",
          "NumericGreaterThan": 0,
          "Next": "ProcessReceipts"
        }
      ],
      "Default": "NoReceipts"
    },
    "ProcessReceipts": {
      "Type": "Map",
      "ItemsPath": "$.list_result.receipts",
      "MaxConcurrency": 3,
      "Iterator": {
        "StartAt": "ValidateReceipt",
        "States": {
          "ValidateReceipt": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:...:function:validate-receipt-self-correcting",
            "End": true,
            "Retry": [
              {
                "ErrorEquals": ["States.TaskFailed", "Lambda.ServiceException"],
                "IntervalSeconds": 2,
                "MaxAttempts": 3,
                "BackoffRate": 2.0
              }
            ],
            "Catch": [
              {
                "ErrorEquals": ["States.ALL"],
                "Next": "ValidationFailed",
                "ResultPath": "$.error"
              }
            ]
          },
          "ValidationFailed": {
            "Type": "Fail",
            "Error": "ValidationFailed",
            "Cause": "Receipt validation failed"
          }
        }
      },
      "End": true
    },
    "NoReceipts": {
      "Type": "Succeed"
    }
  }
}
```

---

## Lambda Handler: ValidateReceipt

### Input

```json
{
  "image_id": "uuid-here",
  "receipt_id": 1,
  "pending_label_count": 5
}
```

### Processing Flow (Per Receipt)

```python
async def validate_receipt_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Validate ALL PENDING/NEEDS_REVIEW labels for a single receipt.

    This runs Steps 1-5 of the self-correcting validation system:
    1. Enhanced ChromaDB (query valid + invalid examples)
    2. Contradiction Detection (logical inconsistencies)
    3. Iterative CoVe Refinement (LLM verification)
    4. Cross-Validation Conflict Resolution (weighted voting)
    5. Store Invalid Examples (for future learning)
    """

    image_id = event["image_id"]
    receipt_id = event["receipt_id"]

    # Initialize clients
    dynamo_client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])
    chroma_client = await _init_chromadb_client()  # Download snapshot
    llm = ChatOllama(...)

    # Fetch receipt data (once per receipt)
    receipt_lines = dynamo_client.list_receipt_lines_from_receipt(image_id, receipt_id)
    receipt_words = dynamo_client.list_receipt_words_from_receipt(image_id, receipt_id)
    receipt_metadata = dynamo_client.get_receipt_metadata(image_id, receipt_id)

    # Build receipt text and word lookup (once per receipt)
    receipt_text = _build_receipt_text(receipt_lines, receipt_words)
    word_text_lookup = {
        (word.line_id, word.word_id): word.text
        for word in receipt_words
    }

    # Get ALL PENDING/NEEDS_REVIEW labels for this receipt
    pending_labels = dynamo_client.list_receipt_word_labels_with_status(
        image_id=image_id,
        receipt_id=receipt_id,
        statuses=["PENDING", "NEEDS_REVIEW"]
    )

    if not pending_labels:
        return {
            "success": True,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "labels_processed": 0,
            "message": "No pending labels found"
        }

    logger.info(f"Processing {len(pending_labels)} labels for receipt {image_id}/{receipt_id}")

    # ============================================================
    # STEP 1: Enhanced ChromaDB Validation (All Labels Together)
    # ============================================================
    logger.info("Step 1: Enhanced ChromaDB validation (learning from mistakes)...")

    chromadb_results = {}
    chromadb_examples = {}

    # Process all labels together (batch ChromaDB queries)
    for label in pending_labels:
        result, examples = await validate_with_invalid_examples_hybrid(
            label=label,
            chroma_client=chroma_client,
            dynamo_client=dynamo_client,
            word_embedding=_get_word_embedding(label, receipt_words),
            receipt_text=receipt_text,
            word_text_lookup=word_text_lookup,
            llm=llm,  # Optional - only used if ambiguous
        )

        label_id = f"{label.image_id}#{label.receipt_id}#{label.line_id}#{label.word_id}"
        chromadb_results[label_id] = result
        chromadb_examples[label_id] = examples

    # Count Step 1 results
    step1_validated = sum(1 for r in chromadb_results.values() if r.get("status") == "VALID")
    step1_invalidated = sum(1 for r in chromadb_results.values() if r.get("status") == "INVALID")
    step1_pending = len(pending_labels) - step1_validated - step1_invalidated

    logger.info(f"Step 1 Results: {step1_validated} VALID, {step1_invalidated} INVALID, {step1_pending} PENDING")

    # ============================================================
    # STEP 2: Contradiction Detection (All Labels Together)
    # ============================================================
    logger.info("Step 2: Contradiction detection...")

    contradictions = detect_contradictions(
        labels=pending_labels,
        receipt_text=receipt_text,
        word_text_lookup=word_text_lookup,
        chromadb_validation_results=chromadb_results,  # Feed from Step 1
    )

    logger.info(f"Step 2 Results: Found {len(contradictions)} contradictions")

    # ============================================================
    # STEP 3: Iterative CoVe Refinement (All Labels Together)
    # ============================================================
    logger.info("Step 3: Iterative CoVe refinement...")

    # Prioritize labels for CoVe based on Step 1 results
    labels_for_cove = _prioritize_labels_for_cove(
        labels=pending_labels,
        chromadb_validation_results=chromadb_results,
    )

    if labels_for_cove:
        cove_results = await iterative_cove_validation(
            labels=labels_for_cove,
            receipt_text=receipt_text,
            llm=llm,
            max_iterations=3,
            chromadb_validation_results=chromadb_results,  # Feed from Step 1
            chromadb_examples=chromadb_examples,  # Feed from Step 1
        )
    else:
        cove_results = {}
        logger.info("Step 3: No labels need CoVe (all validated by Step 1)")

    # ============================================================
    # STEP 4: Cross-Validation Conflict Resolution (All Labels Together)
    # ============================================================
    logger.info("Step 4: Cross-validation conflict resolution...")

    final_labels = resolve_validation_conflicts(
        labels=pending_labels,
        chromadb_results=chromadb_results,  # From Step 1
        cove_results=cove_results,  # From Step 3
        contradiction_results=contradictions,  # From Step 2
    )

    # ============================================================
    # STEP 5: Store Invalid Examples (All Invalid Labels Together)
    # ============================================================
    logger.info("Step 5: Storing invalid examples for future learning...")

    invalid_labels = [l for l in final_labels if l.validation_status == "INVALID"]
    if invalid_labels:
        await store_invalid_examples(
            invalid_labels=invalid_labels,
            chroma_client=chroma_client,
            dynamo_client=dynamo_client,
            word_text_lookup=word_text_lookup,
        )
        logger.info(f"Step 5: Stored {len(invalid_labels)} invalid examples")

    # ============================================================
    # Update DynamoDB (Batch Update All Labels)
    # ============================================================
    logger.info("Updating DynamoDB with final validation results...")

    labels_to_update = [l for l in final_labels if l.validation_status != "PENDING"]

    if labels_to_update:
        dynamo_client.update_receipt_word_labels(labels_to_update)
        logger.info(f"Updated {len(labels_to_update)} labels in DynamoDB")

    # ============================================================
    # Return Results
    # ============================================================
    return {
        "success": True,
        "image_id": image_id,
        "receipt_id": receipt_id,
        "labels_processed": len(pending_labels),
        "labels_validated": sum(1 for l in final_labels if l.validation_status == "VALID"),
        "labels_invalidated": sum(1 for l in final_labels if l.validation_status == "INVALID"),
        "labels_still_pending": sum(1 for l in final_labels if l.validation_status == "PENDING"),
        "step1_results": {
            "validated": step1_validated,
            "invalidated": step1_invalidated,
            "pending": step1_pending,
        },
        "step2_results": {
            "contradictions_found": len(contradictions),
        },
        "step3_results": {
            "labels_processed": len(labels_for_cove),
            "iterations": cove_results.get("iterations", []),
        },
        "step5_results": {
            "invalid_examples_stored": len(invalid_labels),
        },
    }
```

---

## Data Flow: Per Receipt

### Input Data (Per Receipt)

```
Receipt: image_id="abc123", receipt_id=1
├── ReceiptLines: [Line 1, Line 2, ..., Line N]
├── ReceiptWords: [Word 1, Word 2, ..., Word M]
├── ReceiptMetadata: {merchant_name: "Walmart", ...}
└── PendingLabels: [
    Label 1: GRAND_TOTAL (line_id=5, word_id=12, status=PENDING)
    Label 2: TAX (line_id=4, word_id=8, status=PENDING)
    Label 3: SUBTOTAL (line_id=3, word_id=6, status=PENDING)
    ...
]
```

### Step 1: Enhanced ChromaDB (Per Label, Batched)

```
For each label in pending_labels:
    ├── Query ChromaDB for VALID examples
    ├── Query ChromaDB for INVALID examples
    ├── Merge with DynamoDB data
    └── Return: {status, confidence, valid_similarity, invalid_similarity, ...}

Result: chromadb_results = {
    "label_1": {status: "INVALID", confidence: 0.88, ...},
    "label_2": {status: "VALID", confidence: 0.82, ...},
    "label_3": {status: "PENDING", confidence: 0.65, ...},
}
```

### Step 2: Contradiction Detection (All Labels Together)

```
Input: All labels + Step 1 results
├── Check: Multiple GRAND_TOTALs? → Found: Label 1
├── Check: Math mismatch? → Found: SUBTOTAL + TAX ≠ GRAND_TOTAL
└── Check: "TOTAL" word? → Found: Label 1 (triggered by Step 1)

Result: contradictions = [
    {type: "label_word_not_value", label: Label 1, priority: "high"},
    {type: "math_mismatch", labels: [Label 2, Label 3, Label 1], priority: "high"},
]
```

### Step 3: Iterative CoVe (All Labels Together)

```
Input: Labels that need CoVe (prioritized from Step 1)
├── Iteration 1:
│   ├── Generate questions with Step 1 context
│   ├── Answer questions
│   └── Revise labels
├── Iteration 2:
│   └── Refine based on Iteration 1 results
└── Iteration 3:
    └── Final refinement

Result: cove_results = {
    "label_1": {status: "INVALID", confidence: 0.92, ...},
    "label_2": {status: "VALID", confidence: 0.88, ...},
}
```

### Step 4: Conflict Resolution (All Labels Together)

```
Input: All labels + Step 1 + Step 2 + Step 3 results
├── For each label:
│   ├── Collect votes from Step 1, Step 2, Step 3
│   ├── Apply weighted voting
│   └── Make final decision
└── Return: Final labels with validation_status

Result: final_labels = [
    Label 1: {validation_status: "INVALID"},  # All methods agree
    Label 2: {validation_status: "VALID"},    # CoVe overrides ChromaDB
    Label 3: {validation_status: "PENDING"},  # Tie - needs manual review
]
```

### Step 5: Store Invalid Examples (All Invalid Labels Together)

```
Input: All labels with validation_status="INVALID"
├── For each invalid label:
│   ├── Get ChromaDB ID
│   ├── Update ChromaDB metadata: invalid_labels = [label_type]
│   └── Log for metrics
└── Result: Invalid examples stored for future learning
```

---

## Parallelization Strategy

### Step Function Level

- **MaxConcurrency: 3** (processes 3 receipts in parallel)
- Limited by:
  - Ollama API rate limits
  - ChromaDB snapshot download bandwidth
  - Lambda concurrency limits

### Lambda Level (Per Receipt)

- **Sequential processing** of Steps 1-5 within a single receipt
- **Batch operations** where possible:
  - ChromaDB queries can be batched (but currently done sequentially for clarity)
  - DynamoDB updates are batched (25 labels per batch)

### Future Optimization Opportunities

1. **Parallel ChromaDB Queries**: Query all labels in parallel (Step 1)
2. **Parallel CoVe**: Process multiple labels in parallel (Step 3)
3. **Streaming Updates**: Update DynamoDB as each step completes

---

## Error Handling

### Per Receipt Errors

- **ChromaDB download failure**: Retry with exponential backoff
- **LLM API failure**: Retry with exponential backoff, fall back to Step 1 only
- **DynamoDB update failure**: Retry with exponential backoff
- **Individual label failure**: Log error, continue with other labels

### Step Function Retry Policy

```json
{
  "Retry": [
    {
      "ErrorEquals": ["States.TaskFailed", "Lambda.ServiceException"],
      "IntervalSeconds": 2,
      "MaxAttempts": 3,
      "BackoffRate": 2.0
    }
  ]
}
```

---

## Metrics and Monitoring

### Per Receipt Metrics

- **Processing time**: Total time for Steps 1-5
- **Labels processed**: Total number of labels
- **Step 1 results**: Validated, invalidated, pending counts
- **Step 2 results**: Contradictions found
- **Step 3 results**: CoVe iterations, labels processed
- **Step 4 results**: Conflicts resolved
- **Step 5 results**: Invalid examples stored

### Aggregated Metrics (CloudWatch)

- **Receipts processed**: Total receipts processed
- **Labels validated**: Total labels marked VALID
- **Labels invalidated**: Total labels marked INVALID
- **Accuracy improvement**: Track learning over time
- **Error rates**: Per-step error rates

---

## Comparison: Per Label vs Per Receipt

| Aspect | Per Label | Per Receipt (Current) |
|--------|-----------|----------------------|
| **Contradiction Detection** | ❌ Can't detect (needs all labels) | ✅ Can detect (has all labels) |
| **CoVe Context** | ❌ Limited context | ✅ Full receipt context |
| **Resource Efficiency** | ❌ Multiple ChromaDB downloads | ✅ Single ChromaDB download |
| **Parallelization** | ✅ More parallelization | ⚠️ Limited by receipt scope |
| **Error Isolation** | ✅ One label failure doesn't affect others | ⚠️ One receipt failure affects all labels |
| **Complexity** | ✅ Simpler (one label at a time) | ⚠️ More complex (batch processing) |

**Conclusion**: Per receipt is the correct approach because:
1. Contradiction detection requires all labels
2. CoVe benefits from full context
3. More efficient resource usage
4. Consistent with existing architecture

---

## Next Steps

1. **Implement Lambda Handler**: Create `validate_receipt_self_correcting` Lambda
2. **Create Step Function**: Define state machine in Pulumi
3. **Add Metrics**: CloudWatch metrics for monitoring
4. **Test with Sample Receipts**: Validate end-to-end flow
5. **Deploy to Dev**: Test with real data
6. **Monitor and Optimize**: Track performance and accuracy improvements

