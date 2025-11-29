# Label Validation Agent - Inconsistency Analysis

## Test Case (Same Both Runs)

**Word**: `VONS`
**Suggested Label**: `MERCHANT_NAME`
**Status**: `NEEDS_REVIEW`
**Merchant**: Vons
**Original Reasoning**: "Store name appears at top of receipt"

---

## Run 1 Results

**Decision**: ✅ **VALID**
**Confidence**: **92%**

**Tools Used**:
1. `get_word_context`
2. `get_merchant_metadata`
3. `search_similar_words`
4. `get_labels_on_receipt`
5. `submit_decision`

**Reasoning**:
> "The word 'VONS' directly matches the official merchant name from Google Places and appears in a line that references the merchant for customer service. This context aligns with the MERCHANT_NAME label, making it appropriate."

**Evidence**:
1. Word 'VONS' appears in line '>>> For VONS FOR U questions call <<<', referencing the merchant.
2. Google Places confirms merchant name is 'Vons', matching the word.
3. Receipt contains other MERCHANT_NAME labels, indicating merchant name appears multiple times.

---

## Run 2 Results

**Decision**: ❌ **INVALID**
**Confidence**: **85%**

**Tools Used**:
1. `get_word_context`
2. `get_merchant_metadata`
3. `search_similar_words`
4. `submit_decision`

**Reasoning**:
> "The word 'VONS' is used in a service instruction line rather than as the standalone store name at the top of the receipt. Although it matches the merchant name, the primary context indicates it is not functioning as the merchant name field. Therefore, labeling it as MERCHANT_NAME is incorrect."

**Evidence**:
1. Primary context line: '>>> For VONS FOR U questions call <<<', where VONS appears within a customer service phrase, not as the store header.
2. Merchant metadata confirms merchant name is 'Vons', but the word's placement in the receipt is not the typical merchant name location.

---

## Analysis

### Key Differences

1. **Opposite Decisions**: VALID vs INVALID
2. **Different Tool Usage**: Run 1 used `get_labels_on_receipt`, Run 2 did not
3. **Different Reasoning Focus**:
   - Run 1: Focused on word matching merchant name and appearing in merchant reference
   - Run 2: Focused on word appearing in service instruction vs. standalone store name
4. **Different Evidence**: Run 1 mentioned "other MERCHANT_NAME labels", Run 2 focused on placement

### Root Cause

The agent is making **subjective interpretations** of what constitutes a valid MERCHANT_NAME:

- **Run 1 interpretation**: "If the word matches the merchant name and appears in a merchant reference, it's valid"
- **Run 2 interpretation**: "MERCHANT_NAME must be the standalone store name at the top, not in service instructions"

### The Problem

**MERCHANT_NAME definition**: "Trading name or brand of the store issuing the receipt."

This definition doesn't specify:
- ❌ Where on the receipt it must appear
- ❌ Whether it must be standalone or can be part of a phrase
- ❌ Whether it can appear in service instructions

Both interpretations are **plausible** given the vague definition, leading to inconsistent decisions.

---

## Impact

### Critical Issues

1. **Inconsistent Results**: Same input → different decisions
2. **Unreliable Validation**: Can't trust the agent's decisions
3. **Confidence Scores Don't Help**: Both runs had high confidence (>80%) but opposite decisions

### Why This Matters

- If this agent is used in production, labels will be validated inconsistently
- The same word could be marked VALID one time and INVALID the next
- This defeats the purpose of automated validation

---

## Recommendations

### 1. **Clarify CORE_LABELS Definitions**

Add more specific guidance to MERCHANT_NAME definition:

```
MERCHANT_NAME: "Trading name or brand of the store issuing the receipt.
Can appear anywhere on the receipt (header, footer, service lines, etc.).
If the word matches the merchant name and appears in merchant-related context,
it is a valid MERCHANT_NAME label, even if it's part of a phrase or instruction."
```

### 2. **Add Examples to System Prompt**

Include positive and negative examples:

```
## Examples

VALID MERCHANT_NAME:
- "VONS" in line "For VONS FOR U questions call" (merchant name in service instruction)
- "Vons" at top of receipt (standalone merchant name)
- "VONS" in footer (merchant name in footer)

INVALID MERCHANT_NAME:
- "Vons" as part of product name "Vons Brand Milk" (this is PRODUCT_NAME)
- "Vons" as part of address "123 Vons Street" (this is ADDRESS_LINE)
```

### 3. **Add Decision Rules**

Make the decision criteria more explicit:

```
## Decision Rules for MERCHANT_NAME

1. Does the word match the merchant name? (from Google Places)
   - YES → Continue to step 2
   - NO → INVALID

2. Is the word part of a product name or address?
   - YES → INVALID (likely PRODUCT_NAME or ADDRESS_LINE)
   - NO → Continue to step 3

3. Is the word in merchant-related context?
   - YES → VALID (even if in service instruction, footer, etc.)
   - NO → NEEDS_REVIEW
```

### 4. **Lower Temperature or Add Consistency Checks**

- Use `temperature=0.0` (already set) but consider adding deterministic examples
- Add a consistency check: if same word/label combination was validated before, use previous decision

### 5. **Test with More Cases**

Test with:
- Clear VALID cases (standalone merchant name at top)
- Clear INVALID cases (merchant name in product name)
- Edge cases (merchant name in service instructions, footer, etc.)

---

## Conclusion

The agent shows **significant inconsistency** in decision-making. The same input produces opposite decisions with high confidence. This is a **critical issue** that must be addressed before using this agent in production.

**Primary fix**: Clarify the CORE_LABELS definition and add explicit decision rules with examples to guide the agent's interpretation.

