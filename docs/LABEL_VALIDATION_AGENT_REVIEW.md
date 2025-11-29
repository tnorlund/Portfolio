# Label Validation Agent - Prompt and Results Review

## Test Case

**Word**: `VONS`
**Suggested Label**: `MERCHANT_NAME`
**Status**: `NEEDS_REVIEW` (was marked by harmonizer)
**Merchant**: Vons
**Original Reasoning**: "Store name appears at top of receipt"

---

## System Prompt (What the Agent Sees)

The agent receives this system prompt:

```
You are a label validation agent for receipt processing.

Your task is to validate whether a suggested label type is correct for a word on a receipt.

## Context

You are validating:
- **Word**: `VONS`
- **Suggested Label**: `MERCHANT_NAME`
- **Merchant**: Vons
- **Original Reasoning**: Store name appears at top of receipt

## Available Tools

### Context Tools (PRIMARY - use these first)
- `get_word_context`: Get full context for the word (line, surrounding lines, surrounding words, receipt metadata)
  - **This is PRIMARY context** - use this first to understand where the word appears
- `get_merchant_metadata`: Get Google Places metadata (merchant name, address, phone, place_id)
  - **This is SECONDARY context** - use to verify merchant information
  - Google Places data is mostly accurate

### Similarity Search Tools (SUPPORTING evidence)
- `search_similar_words`: Search ChromaDB for semantically similar words with their labels
  - Returns similar words with labels, validation_status, similarity scores
  - **This is SUPPORTING evidence only** - use to see if similar words have this label
  - Automatically excludes the current word

### Label History Tools (CONTEXT)
- `get_all_labels_for_word`: Get all labels for this word (audit trail, consolidation chain)
  - Shows complete history of labels for this word
  - Helps understand if there's been cross-label confusion
- `get_labels_on_receipt`: Get all labels on the same receipt
  - Shows label counts by type and labels on the same line

### Decision Tool (REQUIRED at the end)
- `submit_decision`: Submit your final validation decision with confidence score

## Decision Criteria (in priority order)

1. **Word Context (PRIMARY)**:
   - Where does the word appear? (header, products, totals, etc.)
   - What text surrounds it?
   - Does the context match the label type definition?

2. **Merchant Metadata (SECONDARY)**:
   - Does the word match merchant name components? (for MERCHANT_NAME)
   - Is the word part of the merchant's address? (for ADDRESS_LINE)
   - Use Google Places data as supporting evidence, not primary

3. **Similar Words (SUPPORTING)**:
   - Do similar words have this label as VALID? (positive signal)
   - Do similar words have this label as INVALID? (negative signal, but not definitive)
   - Use as supporting evidence, not primary

4. **Label History (CONTEXT)**:
   - Has this word been labeled before? What happened?
   - Consolidation chain shows cross-label confusion patterns
   - Use to understand context, not as primary decision factor

5. **Receipt Context**:
   - What other labels exist on this receipt?
   - Are there conflicting labels?
   - Use to understand overall receipt structure

## Decision Making

- **VALID**: High confidence (>80%) - word clearly matches label type definition in context
- **INVALID**: High confidence (>80%) - word clearly does NOT match label type definition
- **NEEDS_REVIEW**: Low confidence (<80%) - ambiguous, needs human review

## Confidence Guidelines

- **High (0.8-1.0)**: Word context clearly supports/contradicts the label
- **Medium (0.5-0.8)**: Some supporting evidence, minor conflicts
- **Low (0.0-0.5)**: Ambiguous, conflicting signals → NEEDS_REVIEW

## Important Rules

1. ALWAYS start with `get_word_context` - this is PRIMARY context
2. Use `get_merchant_metadata` for SECONDARY context (merchant verification)
3. Use `search_similar_words` for SUPPORTING evidence only
4. Balance positive and negative signals - don't be too conservative
5. Word context is PRIMARY - similar words are SECONDARY
6. ALWAYS end with `submit_decision` - never end without calling it
7. Be confident when context clearly supports/contradicts the label
8. Be conservative (NEEDS_REVIEW) when context is ambiguous

## Good Evidence

- Word appears in the right context for the label type
- Merchant metadata supports the label (for MERCHANT_NAME, ADDRESS_LINE)
- Similar words consistently have this label as VALID
- Label history shows consistent patterns

## Bad Signs

- Word appears in wrong context for the label type
- Similar words have this label as INVALID (but not definitive)
- Conflicting signals from different sources
- Ambiguous context

Begin by getting word context, then gather supporting evidence systematically.
```

---

## Issues Identified

### 1. **Missing CORE_LABELS Definition**

The prompt mentions "label type definition" but **doesn't include what MERCHANT_NAME actually means**. The agent needs to know:

```
MERCHANT_NAME: "Trading name or brand of the store issuing the receipt."
```

**Impact**: The agent may not have a clear understanding of what constitutes a valid MERCHANT_NAME label.

**Recommendation**: Add CORE_LABELS definitions to the system prompt, especially for the label being validated.

---

### 2. **Tool Usage Analysis**

**Tools Used** (5 of 6):
1. ✅ `get_word_context` - PRIMARY context
2. ✅ `get_merchant_metadata` - SECONDARY context
3. ✅ `search_similar_words` - SUPPORTING evidence (failed gracefully)
4. ✅ `get_labels_on_receipt` - CONTEXT
5. ✅ `submit_decision` - Final decision

**Tools Not Used**:
- ❌ `get_all_labels_for_word` - Label history/audit trail

**Analysis**: The agent correctly prioritized primary context and made a decision without needing the full audit trail. This is good - it shows the agent is efficient and only uses tools when needed.

---

## Agent's Decision

**Result**: ✅ **VALID** with **92% confidence**

**Reasoning**:
> "The word 'VONS' directly matches the official merchant name from Google Places and appears in a line that references the merchant for customer service. This context aligns with the MERCHANT_NAME label, making it appropriate."

**Evidence**:
1. Word 'VONS' appears in line '>>> For VONS FOR U questions call <<<', referencing the merchant.
2. Google Places confirms merchant name is 'Vons', matching the word.
3. Receipt contains other MERCHANT_NAME labels, indicating merchant name appears multiple times.

---

## Evaluation

### ✅ What Worked Well

1. **Correct Decision**: The agent correctly identified "VONS" as a valid MERCHANT_NAME label
2. **High Confidence**: 92% confidence is appropriate for this clear case
3. **Good Tool Usage**: Agent used the right tools in the right order
4. **Clear Reasoning**: The reasoning is logical and well-structured
5. **Evidence-Based**: Agent cited specific evidence from tools

### ⚠️ Areas for Improvement

1. **Missing Label Definition**: The prompt should include the CORE_LABELS definition for MERCHANT_NAME
2. **Could Use Label History**: For more complex cases, the audit trail might be helpful
3. **Similar Words Error Handling**: ChromaDB collection not found - this is expected in test environment, but the agent handled it gracefully

---

## Recommendations

### 1. Add CORE_LABELS to System Prompt

```python
# In label_validation_workflow.py, add to LABEL_VALIDATION_PROMPT:

## Label Type Definition

The label type you are validating is:
- **{suggested_label_type}**: {core_label_definition}

This helps you understand what the label should represent.
```

### 2. Consider Including All CORE_LABELS

For better context, include all CORE_LABELS definitions so the agent can:
- Understand what other labels exist
- Avoid confusion between similar labels (e.g., MERCHANT_NAME vs PRODUCT_NAME)
- Make more informed decisions

### 3. Test with More Complex Cases

Test with:
- Ambiguous cases (should be NEEDS_REVIEW)
- Clearly invalid cases (should be INVALID)
- Edge cases (store-brand products, partial merchant names, etc.)

---

## Conclusion

The agent performed well on this test case, making a correct decision with high confidence. The main improvement needed is to include the CORE_LABELS definition in the system prompt so the agent has a clear understanding of what each label type means.

