# ChromaDB Evidence Gaps in Label Evaluator Traces

## Status

Evidence lookup is **broken** in the current label evaluator step function
traces.  All 353 `phase3_llm_review` spans in the February 2026 batch
(session `d142b135`) produced empty `{}` outputs, and the LLM prompts
show no ChromaDB evidence blocks.  This document describes the bug, the
expected data model, and the changes needed to fix evidence collection
and make it traceable in the viz-cache pipeline.

---

## 1. The numpy array comparison bug

### Symptom

When `query_similar_words()` (or `query_label_evidence()`) returns
results from ChromaDB, the embeddings come back as **numpy arrays**.
Code that later does a truthiness check like:

```python
if embeddings:          # ValueError!
```

...triggers:

> `ValueError: The truth value of an array is ambiguous.
> Use a.any() or a.all()`

because `bool(np.array([...]))` is undefined for multi-element arrays.

### Root cause

`chroma_client.get(collection_name="words", ids=[...], include=["embeddings"])`
returns embeddings as `numpy.ndarray` objects when the underlying
ChromaDB PersistentClient is backed by an `onnxruntime`-based embedding
function.  Several call sites in `chroma_helpers.py` (lines 421-430,
866-868) guard with `if embeddings:` or `len(embeddings) == 0`.  The
`if embeddings:` pattern works for Python lists but **not** for numpy
arrays with more than one element.

The unified receipt evaluator Lambda (`unified_receipt_evaluator.py`)
calls `query_similar_words()` inside `phase3_llm_review`, which
silently catches the `ValueError` and returns an empty evidence list.
The LLM therefore never sees ChromaDB evidence and makes decisions
purely from receipt text and line-item patterns.

### Fix

Replace bare truthiness checks with explicit length/None checks:

```python
# Before (broken for numpy)
if embeddings:
    ...

# After (works for both list and ndarray)
if embeddings is not None and len(embeddings) > 0:
    ...
```

Affected locations in `receipt_agent/receipt_agent/utils/chroma_helpers.py`:
- Line 421: `if not embeddings:`
- Line 430: `if not isinstance(embedding, list) or not embedding:`
- Line 866: `if embeddings is None or len(embeddings) == 0:`
- Line 884-885: `if metadatas is None or len(metadatas) == 0 or len(metadatas[0]) == 0:`

The `hasattr(embedding, "tolist")` guard already exists (line 427, 459,
504, 872) but the truthiness check before it swallows the exception.

---

## 2. What ChromaDB evidence SHOULD provide

When working correctly, each flagged word gets a `LabelEvidence` record
(defined at `chroma_helpers.py:330`) from `query_label_evidence()`:

| Field | Type | Description |
|---|---|---|
| `word_text` | `str` | Text of the similar word from ChromaDB |
| `similarity_score` | `float` | Cosine similarity (0-1), converted from L2 distance |
| `chroma_id` | `str` | Canonical `IMAGE#...#WORD#...` identifier |
| `label_valid` | `bool` | `True` = validated AS this label; `False` = validated as NOT this label |
| `merchant_name` | `str` | Merchant the similar word belongs to |
| `is_same_merchant` | `bool` | Whether the similar word is from the same merchant |
| `position_description` | `str` | Human-readable position like "top-left" |
| `left_neighbor` | `str` | Text of word to the left (or `<EDGE>`) |
| `right_neighbor` | `str` | Text of word to the right (or `<EDGE>`) |
| `evidence_source` | `str` | `"words"` or `"lines"` collection |

### Consensus score

`compute_label_consensus()` (line 705) produces a weighted score
`[-1.0, +1.0]` where positive = evidence supports VALID and negative
= evidence supports INVALID.  Same-merchant matches get a configurable
boost (default `+0.1`).

### Formatted prompt output

`format_label_evidence_for_prompt()` (line 752) renders evidence into
the LLM prompt as:

```
Evidence validated AS PRODUCT_NAME:
  - [WORD] "Avocado" [middle-left] 92% similar (same merchant)
  - [LINE] "Bananas 1.29" [middle-left] 87% similar
Evidence validated as NOT PRODUCT_NAME:
  - [WORD] "SUBTOTAL" [bottom-right] 78% similar

Consensus: Evidence suggests VALID (3 for, 1 against, score: +0.42)
```

---

## 3. What needs to change in the step function

### 3a. Fix the numpy truthiness bug

In `chroma_helpers.py`, replace all `if embeddings:` and
`if not embeddings:` patterns with explicit checks (see Section 1).
This single fix will restore evidence to the LLM prompt.

### 3b. Trace the evidence in span outputs

Currently `phase3_llm_review` spans have empty `{}` outputs.  After
fixing the evidence bug, the handler should record per-issue evidence
summaries in the span output so the viz-cache pipeline can extract them:

```python
trace_ctx.set_outputs({
    "issues_reviewed": len(reviewed_issues),
    "decisions": dict(decisions),
    "evidence_summary": {
        "total_evidence_items": total_evidence_count,
        "words_with_evidence": words_with_evidence,
        "avg_similarity": avg_similarity,
        "avg_consensus": avg_consensus,
    },
})
```

### 3c. Record per-issue evidence in the reviewed issues JSON

Each reviewed issue uploaded to S3 should include the evidence that was
used (currently only `similar_word_count` is stored):

```python
reviewed_issues.append({
    "image_id": image_id,
    "receipt_id": receipt_id,
    "issue": issue,
    "llm_review": review_result,
    "similar_word_count": len(similar_evidence),
    # NEW: store evidence details for viz
    "evidence": [
        {
            "word_text": e["word_text"],
            "similarity_score": e["similarity_score"],
            "label_valid": any(v["label"] == target_label for v in e["validated_as"]),
            "evidence_source": "words",
            "is_same_merchant": e["is_same_merchant"],
        }
        for e in similar_evidence[:10]
    ],
    "consensus_score": consensus_score,
})
```

---

## 4. Proposed viz-cache structure for evidence

Once evidence is flowing and traced, a new viz-cache helper
(`evaluator_evidence_viz_cache.py`) can extract per-receipt evidence
summaries.  Proposed output format per receipt:

```json
{
    "image_id": "abc-123",
    "receipt_id": 1,
    "merchant_name": "Sprouts Farmers Market",
    "trace_id": "...",
    "issues": [
        {
            "word_text": "Avocado",
            "line_id": 5,
            "word_id": 2,
            "issue_type": "missing_label_cluster",
            "current_label": null,
            "suggested_label": "PRODUCT_NAME",
            "llm_decision": "VALID",
            "evidence": [
                {
                    "word_text": "Avocado",
                    "similarity_score": 0.92,
                    "label_valid": true,
                    "evidence_source": "words",
                    "is_same_merchant": true,
                    "consensus_score": 0.65
                }
            ]
        }
    ],
    "evidence_stats": {
        "total_evidence_items": 45,
        "avg_similarity": 0.84,
        "avg_consensus": 0.42,
        "words_with_evidence": 12,
        "words_without_evidence": 3
    }
}
```

### Aggregation for the dashboard

The index file would include:

```json
{
    "total_receipts": 588,
    "receipts_with_evidence": 0,
    "evidence_status": "broken",
    "evidence_error": "numpy array truthiness bug",
    "avg_evidence_per_issue": 0.0,
    "fix_pr": null
}
```

Once the fix is deployed and a new batch is run, these fields would
populate with real data and `evidence_status` would change to
`"available"`.

---

## 5. Action items

1. **Fix numpy truthiness bug** in `chroma_helpers.py` (4 locations)
2. **Add evidence to span outputs** in `llm_review.py` handler
3. **Store per-issue evidence** in reviewed issues JSON on S3
4. **Re-run a batch** to generate traces with real evidence
5. **Build evidence viz-cache helper** once traces contain evidence data
