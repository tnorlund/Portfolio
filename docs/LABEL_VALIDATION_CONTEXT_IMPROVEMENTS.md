# Label Validation Agent - Context Improvements

## Current Issue

The agent is making inconsistent decisions on the same input:
- **Run 1**: VALID (92% confidence) - "Word matches merchant name and appears in merchant reference"
- **Run 2**: INVALID (85% confidence) - "Word appears in service instruction, not as standalone store name"

The root cause: **Missing context** about what constitutes a valid MERCHANT_NAME label.

---

## What Context Should We Provide?

### 1. **CORE_LABELS Definition (CRITICAL)**

**Current**: The prompt mentions "label type definition" but doesn't include it.

**What to add**:
```
MERCHANT_NAME: "Trading name or brand of the store issuing the receipt."
```

**Why**: The agent needs to know what MERCHANT_NAME means to validate it correctly.

---

### 2. **Label Placement Rules**

**Current**: No guidance on where MERCHANT_NAME can appear.

**What to add**:
```
MERCHANT_NAME can appear anywhere on the receipt:
- Header (top of receipt) - most common
- Footer (bottom of receipt)
- Service instructions ("For VONS FOR U questions call")
- Store information sections
- Receipt body (less common but valid)

MERCHANT_NAME is INVALID if:
- It's part of a product name (e.g., "Vons Brand Milk" → PRODUCT_NAME)
- It's part of an address (e.g., "123 Vons Street" → ADDRESS_LINE)
- It's part of a website URL (e.g., "vons.com" → WEBSITE)
```

**Why**: The agent needs to distinguish between valid merchant name references vs. invalid contexts.

---

### 3. **Examples (VALID vs INVALID)**

**Current**: No examples provided.

**What to add**:

```
## Examples

### VALID MERCHANT_NAME Examples:

1. **Standalone at top**: "VONS" appearing alone at the top of the receipt
   - Context: Header line, no other text
   - Decision: VALID

2. **In service instruction**: "VONS" in "For VONS FOR U questions call"
   - Context: Service/customer service line
   - Decision: VALID (merchant name reference)

3. **In footer**: "VONS" in receipt footer
   - Context: Footer section
   - Decision: VALID

4. **Partial match**: "Vons" when merchant is "Vons" (case-insensitive match)
   - Context: Matches Google Places merchant name
   - Decision: VALID

### INVALID MERCHANT_NAME Examples:

1. **In product name**: "Vons" in "Vons Brand Organic Milk"
   - Context: Product line item
   - Decision: INVALID (this is PRODUCT_NAME)

2. **In address**: "Vons" in "123 Vons Street"
   - Context: Address line
   - Decision: INVALID (this is ADDRESS_LINE)

3. **In website**: "vons" in "www.vons.com"
   - Context: Website/URL
   - Decision: INVALID (this is WEBSITE)

4. **No match**: "Walmart" when merchant is "Vons"
   - Context: Doesn't match merchant name
   - Decision: INVALID
```

**Why**: Examples help the agent understand edge cases and make consistent decisions.

---

### 4. **Decision Rules (Step-by-Step)**

**Current**: Vague decision criteria.

**What to add**:

```
## Decision Rules for MERCHANT_NAME

Follow these steps in order:

1. **Does the word match the merchant name?** (from Google Places)
   - Check: Case-insensitive match, partial match OK
   - If NO → INVALID (confidence: 90%)
   - If YES → Continue to step 2

2. **Is the word part of a product name?**
   - Check: Is it in a line with product-like text? (e.g., "Brand", "Organic", prices)
   - If YES → INVALID (confidence: 85%) - likely PRODUCT_NAME
   - If NO → Continue to step 3

3. **Is the word part of an address?**
   - Check: Is it in a line with address-like text? (e.g., street numbers, "Street", "Avenue")
   - If YES → INVALID (confidence: 85%) - likely ADDRESS_LINE
   - If NO → Continue to step 4

4. **Is the word part of a website/URL?**
   - Check: Is it in a line with URL-like text? (e.g., "www.", ".com", "http")
   - If YES → INVALID (confidence: 85%) - likely WEBSITE
   - If NO → Continue to step 5

5. **Is the word in merchant-related context?**
   - Check: Header, footer, service instructions, store info
   - If YES → VALID (confidence: 90%)
   - If NO → NEEDS_REVIEW (confidence: 50%)
```

**Why**: Step-by-step rules make the decision process explicit and consistent.

---

### 5. **Context from Similar Words**

**Current**: Similar words are used but not prioritized.

**What to add**:

```
## Using Similar Words

When checking similar words:
- **Strong positive signal**: Similar words with MERCHANT_NAME as VALID in same merchant
- **Strong negative signal**: Similar words with MERCHANT_NAME as INVALID in same merchant
- **Weak signal**: Similar words from different merchants (less relevant)

Priority:
1. Same merchant + VALID MERCHANT_NAME → Strong support for VALID
2. Same merchant + INVALID MERCHANT_NAME → Strong support for INVALID
3. Different merchant → Use as supporting evidence only
```

**Why**: Helps the agent weight similar word evidence correctly.

---

### 6. **Label History Context**

**Current**: Label history tool exists but isn't emphasized.

**What to add**:

```
## Using Label History

Check label history to understand:
- Has this word been labeled before?
- Was it previously MERCHANT_NAME? (if yes, likely still valid)
- Was it previously PRODUCT_NAME or ADDRESS_LINE? (if yes, likely invalid)
- Consolidation chain shows cross-label confusion patterns

Use label history as supporting evidence, not primary decision factor.
```

**Why**: Label history can reveal patterns of confusion (e.g., MERCHANT_NAME vs PRODUCT_NAME).

---

## Implementation Plan

### Phase 1: Add CORE_LABELS Definition
- [ ] Import CORE_LABELS from `receipt_label.constants`
- [ ] Add label definition to system prompt
- [ ] Format: `**{label_type}**: {definition}`

### Phase 2: Add Examples
- [ ] Add VALID examples (3-5 cases)
- [ ] Add INVALID examples (3-5 cases)
- [ ] Include context for each example

### Phase 3: Add Decision Rules
- [ ] Create step-by-step decision rules
- [ ] Add confidence thresholds for each step
- [ ] Make rules explicit and deterministic

### Phase 4: Enhance Tool Context
- [ ] Improve similar words tool to show merchant match
- [ ] Improve label history tool to show consolidation chain
- [ ] Add context about tool priority

### Phase 5: Test and Refine
- [ ] Run against all NEEDS_REVIEW MERCHANT_NAME labels
- [ ] Analyze patterns in correct vs incorrect decisions
- [ ] Refine rules based on results

---

## Expected Improvements

After adding this context:

1. **Consistency**: Same input → same decision (deterministic rules)
2. **Accuracy**: Correct decisions on edge cases (clear examples)
3. **Confidence**: Appropriate confidence scores (explicit thresholds)
4. **Efficiency**: Faster decisions (clear rules reduce LLM reasoning time)

---

## Next Steps

1. Wait for test results from `test_all_needs_review_merchant_names.py`
2. Analyze patterns in correct vs incorrect decisions
3. Identify which context was missing for incorrect decisions
4. Implement improvements based on analysis
5. Re-test to validate improvements

