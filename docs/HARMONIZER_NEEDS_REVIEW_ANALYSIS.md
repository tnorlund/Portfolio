# Harmonizer NEEDS_REVIEW Analysis

## Problem Statement

The harmonizer is marking too many words as `NEEDS_REVIEW` instead of confidently marking them as `VALID` or `INVALID`. This creates a backlog of labels that need manual review.

## ⚠️ IMPORTANT: Verify with Actual Metrics First

**Before implementing solutions, we need to verify this is actually a problem in production.**

### Check Current Metrics

Run the metrics script to see actual production data:

```bash
./scripts/check_harmonizer_metrics.sh 168  # Last week
```

This will show:
- `LabelsNeedsReview`: Total labels marked as NEEDS_REVIEW
- `LabelsUpdated`: Total labels successfully updated
- `LabelsSkipped`: Total labels skipped
- Breakdown by label type

### Metrics Currently Tracked

✅ **Tracked Metrics**:
- `LabelsNeedsReview` - Total labels marked as NEEDS_REVIEW
- `LabelsUpdated` - Total labels successfully updated
- `LabelsSkipped` - Total labels skipped
- `LabelsFailed` - Total labels that failed to update
- `OutliersDetected` - Total outliers found
- `LLMCallsTotal`, `LLMCallsSuccessful`, `LLMCallsFailed` - LLM call metrics

❌ **NOT Tracked** (need to add):
- **Why validation failed**: Edge case match, ChromaDB missing, noise word, LLM rejection
- **Validation failure reasons**: Breakdown of why suggestions weren't validated
- **Outlier-to-NEEDS_REVIEW rate**: How many outliers end up as NEEDS_REVIEW vs INVALID

### Questions to Answer from Metrics

1. **What's the actual NEEDS_REVIEW rate?**
   - `LabelsNeedsReview / (LabelsUpdated + LabelsSkipped + LabelsNeedsReview)`
   - Is it > 50%? > 30%? This determines if it's actually a problem

2. **Which label types have the highest NEEDS_REVIEW rate?**
   - Some label types might be inherently more ambiguous
   - Focus improvements on high-rate label types

3. **What's the outlier detection rate?**
   - `OutliersDetected / LabelsProcessed`
   - High outlier rate might indicate outlier detection is too aggressive

4. **What's the LLM validation success rate?**
   - `LLMCallsSuccessful / LLMCallsTotal`
   - Low success rate might indicate validation is too strict

### Recommended Next Steps

1. **Run metrics script** to get baseline data
2. **Analyze LangSmith traces** for a sample of NEEDS_REVIEW labels to understand why they failed validation
3. **Add detailed metrics** for validation failure reasons (see "Solution 6" below)
4. **Then implement solutions** based on actual data, not assumptions

## Root Cause Analysis

### Is Validation the Problem?

**Yes, validation has several issues that cause it to fail too often:**

#### Issue 1: Technical Failures Cause Validation to Fail (Lines 347-388)

The validation fails immediately for technical reasons, not semantic reasons:

```python
# Line 347-349: ChromaDB not available → validation fails
if not self.chroma or not self.embed_fn:
    return False, None

# Line 353-358: Noise words → validation fails
if word.is_noise:
    return False, None

# Line 374-379: Word not in ChromaDB → validation fails
if not results or not results.get("ids"):
    return False, None

# Line 383-388: No embedding → validation fails
if embeddings is None or len(embeddings) == 0 or embeddings[0] is None:
    return False, None

# Line 481-483: Any exception → validation fails
except Exception as e:
    return False, None
```

**Problem**: These are technical failures, not semantic rejections. The word might be perfectly valid, but validation fails because:
- Word isn't in ChromaDB yet (new word, not embedded)
- Word is marked as "noise" (but might still be a valid label)
- ChromaDB is unavailable (temporary issue)

**Impact**: Valid suggestions get rejected for technical reasons → NEEDS_REVIEW

#### Issue 2: LLM Validation Prompt is Too Conservative (Lines 646-673)

The validation prompt emphasizes negative signals very strongly:

```python
# Line 661: Strong emphasis on invalid_labels
"**Invalid labels**: ⚠️ Words with `{suggested_label_type}` in their invalid_labels are a STRONG negative signal"

# Line 665: Critical warning
"**Critical**: If you see similar words where `{suggested_label_type}` is in the invalid_labels, this is strong evidence the suggestion is wrong!"
```

**Problem**:
- The prompt is biased toward rejection
- If even ONE similar word has the label as INVALID, it's treated as "strong evidence"
- The prompt doesn't balance positive signals (valid_labels) with negative signals

**Impact**: LLM rejects valid suggestions because it's too conservative → NEEDS_REVIEW

#### Issue 3: Validation Prompt Lacks Original Word Context (Lines 646-673)

The validation prompt shows:
- ✅ Similar words' context (surrounding lines)
- ✅ Similar words' labels (valid/invalid)
- ❌ **Original word's context** (line, surrounding lines, merchant name context)

**Problem**: The LLM validates based on similar words, but doesn't see the original word's full context. It might reject a valid suggestion because similar words don't have the label, even though the original word's context clearly supports it.

**Impact**: Valid suggestions get rejected because LLM doesn't see full context → NEEDS_REVIEW

#### Issue 4: No Similar Words = Conservative Fallback (Line 667)

```python
# Line 667: If no similar words found
"If no similar words were found, evaluate based on your reasoning and general knowledge about receipt label types."
```

**Problem**: When no similar words are found, the LLM falls back to "reasoning and general knowledge" which is very conservative. It's more likely to reject than accept.

**Impact**: New/unique words get rejected because no similar examples exist → NEEDS_REVIEW

### Flow Overview

1. **Outlier Detection** (`_identify_outliers`): LLM determines if a word is an outlier in the group
2. **Label Suggestion** (`_suggest_label_type_for_outlier`): If outlier, LLM suggests a new label type
3. **Validation** (`_validate_suggestion_with_similarity`): Validates the suggestion by:
   - Checking edge cases (fast rejection)
   - Finding similar words in ChromaDB
   - Asking LLM to validate based on similar words
4. **Apply Fixes** (`apply_fixes_from_results`):
   - If outlier has **validated suggestion** → creates new label
   - If outlier has **NO validated suggestion** → marks as `NEEDS_REVIEW` (line 2508-2511)

### Why Validation Fails (Leading to NEEDS_REVIEW)

The validation step can fail for several reasons, all of which lead to `NEEDS_REVIEW`:

1. **Edge case match** (line 329-345): Word matches known invalid pattern → rejected immediately
2. **Word not in ChromaDB** (line 374-379): Word doesn't have an embedding → validation fails
3. **No embedding** (line 383-388): Word exists but has no embedding → validation fails
4. **Word is noise** (line 353-358): Word marked as noise → validation skipped
5. **LLM validation returns False** (line 471-479): LLM doesn't validate the suggestion
6. **Exception during validation** (line 481-483): Any error → validation fails

### Current Logic (Too Conservative)

```python
# Line 2508-2511 in apply_fixes_from_results
if is_outlier and not has_validated_suggestion:
    # Outlier without validated suggestion -> NEEDS_REVIEW
    labels_for_review.append(r)
    continue
```

**Problem**: This is a binary decision - either the suggestion is validated or it's `NEEDS_REVIEW`. There's no middle ground for cases where we can confidently mark as `INVALID`.

## Proposed Solutions

### Solution 1: Add Confidence-Based Decision Making

Instead of binary validation, use confidence scores to make decisions:

```python
# In apply_fixes_from_results
if is_outlier:
    if has_validated_suggestion:
        # High confidence: Create new label
        labels_to_update.append(r)
    elif r.confidence < 30.0:
        # Low confidence: Mark as INVALID (not NEEDS_REVIEW)
        # This word is clearly wrong but we don't know what it should be
        labels_to_mark_invalid.append(r)
    else:
        # Medium confidence: NEEDS_REVIEW
        labels_for_review.append(r)
```

### Solution 2: Fix Validation Logic (HIGH PRIORITY)

The validation has multiple issues that need fixing:

#### 2a. Handle Technical Failures Gracefully

**Current**: Technical failures (ChromaDB missing, no embedding, noise word) → validation fails → NEEDS_REVIEW

**Proposed**: Don't fail validation for technical reasons. Instead:

```python
# In _validate_suggestion_with_similarity
# If word not in ChromaDB, still try LLM validation
if not results or not results.get("ids"):
    logger.info("Word '%s' not in ChromaDB, validating with LLM only", word.word_text)
    # Get original word's context for LLM
    word_context = await self._get_word_context(word)
    is_valid = await self._llm_validate_suggestion(
        word_text=word.word_text,
        suggested_label_type=suggested_label_type,
        merchant_name=merchant_name,
        llm_reason=llm_reason,
        similar_words=[],  # Empty - validate based on original word context
        word_context=word_context,  # NEW: Include original word's context
    )
    return is_valid, None

# If noise word, still try validation (might be valid despite being noise)
if word.is_noise:
    logger.debug("Word '%s' is noise, but attempting validation", word.word_text)
    # Continue with validation - don't skip
```

#### 2b. Improve LLM Validation Prompt

**Current Issues**:
1. Too conservative - emphasizes negative signals too strongly
2. Missing original word's context - only shows similar words' context
3. No balance between positive and negative signals

**Proposed**: Update prompt to:

```python
prompt = f"""# Validate Label Type Suggestion

**Word:** `"{word_text}"` | **Merchant:** {merchant_name} | **Suggested Label:** `{suggested_label_type}`

**Original Word Context:**
{word_context}  # NEW: Include original word's line, surrounding lines, etc.

**Your reasoning:** {llm_reason or "No reasoning provided"}

## Similar Words from Database
{similar_words_text}

## Instructions

**Balance positive and negative signals:**
- ✅ **Positive signals**: Similar words with `{suggested_label_type}` in valid_labels support the suggestion
- ⚠️ **Negative signals**: Similar words with `{suggested_label_type}` in invalid_labels suggest caution, but are NOT definitive proof
- 📍 **Context matters**: The original word's context (shown above) is PRIMARY - similar words are SECONDARY

**Decision criteria (in order of importance):**
1. **Original word's context**: Does the word appear in the right context for `{suggested_label_type}`?
2. **Reasoning quality**: Is the reasoning sound and specific?
3. **Similar words**: Do similar words support or contradict? (Use as supporting evidence, not primary)
4. **Merchant context**: Does the merchant name/context support the suggestion?

**Be lenient when:**
- No similar words found but reasoning is strong
- Similar words don't have the label but original word's context clearly supports it
- Merchant name context strongly supports the suggestion

**Be strict when:**
- Multiple similar words explicitly have this label as INVALID
- Original word's context clearly contradicts the suggestion
- Reasoning is weak or generic

Is the suggestion valid?
"""
```

#### 2c. Add Word Context to Validation

**Current**: Validation prompt doesn't include original word's context

**Proposed**: Add method to get original word's context and include it in validation prompt:

```python
async def _get_word_context(self, word: LabelRecord) -> Dict[str, Any]:
    """Get full context for the original word."""
    context = {
        "word_text": word.word_text,
        "line_text": None,
        "surrounding_lines": None,
        "merchant_name": word.merchant_name,
    }

    if self.dynamo and word.image_id and word.receipt_id and word.line_id:
        # Get line and surrounding lines (same logic as _llm_determine_outlier)
        # ... fetch and format context ...

    return context
```

### Solution 3: Add Fallback Decision Logic

When validation fails, use additional signals to make a decision:

```python
# In apply_fixes_from_results
if is_outlier and not has_validated_suggestion:
    # Try to make a decision based on other signals
    decision = self._make_fallback_decision(r, group)

    if decision == "INVALID":
        # Clearly wrong - mark as INVALID
        labels_to_mark_invalid.append(r)
    elif decision == "VALID":
        # Reasonable suggestion - create label with lower confidence
        labels_to_update.append(r)  # But mark as PENDING, not VALID
    else:
        # Truly ambiguous - NEEDS_REVIEW
        labels_for_review.append(r)

def _make_fallback_decision(self, result: HarmonizerResult, group: MerchantLabelGroup) -> str:
    """Make a decision when validation fails."""
    # Check edge cases
    if self._check_edge_cases(result.word_text, result.suggested_label_type, group.merchant_name):
        return "INVALID"

    # If suggestion matches merchant name components (for MERCHANT_NAME label)
    if result.suggested_label_type == "MERCHANT_NAME":
        if result.word_text.upper() in group.merchant_name.upper():
            return "VALID"  # Likely valid

    # If suggestion is clearly wrong based on context
    if result.suggested_label_type in ["GRAND_TOTAL", "SUBTOTAL"]:
        # Check if word looks like a number
        if not any(c.isdigit() for c in result.word_text):
            return "INVALID"

    return "NEEDS_REVIEW"  # Default to review
```

### Solution 4: Use Edge Cases to Mark as INVALID

**Current**: Edge cases reject suggestions but don't mark the original label as INVALID

**Proposed**: When an edge case matches, mark the original label as INVALID:

```python
# In apply_fixes_from_results
if is_outlier:
    # Check if current label matches edge case
    edge_case = self._check_edge_cases(
        word_text=result.word_text,
        label_type=result.current_label,
        merchant_name=group.merchant_name,
    )
    if edge_case:
        # Mark current label as INVALID (it's a known invalid pattern)
        labels_to_mark_invalid.append(r)
        continue
```

### Solution 5: Improve Outlier Detection

**Current**: Outlier detection might be too aggressive, marking valid words as outliers

**Proposed**:
- Increase similarity threshold for outlier detection
- Only mark as outlier if there's strong evidence (multiple similar words with different labels)
- Use merchant name context more effectively

## Solution 6: Add Detailed Metrics for Validation Failures

**Before implementing other solutions, add metrics to understand WHY validation fails:**

```python
# In _validate_suggestion_with_similarity
validation_failure_reason = None

if edge_case:
    validation_failure_reason = "edge_case_match"
elif word.is_noise:
    validation_failure_reason = "noise_word"
elif not results or not results.get("ids"):
    validation_failure_reason = "chromadb_missing"
elif not embeddings or embeddings[0] is None:
    validation_failure_reason = "no_embedding"
elif not is_valid:
    validation_failure_reason = "llm_rejected"
else:
    validation_failure_reason = "exception"

# Track in metrics
self._api_metrics.validation_failures_by_reason[validation_failure_reason] = \
    self._api_metrics.validation_failures_by_reason.get(validation_failure_reason, 0) + 1
```

This will show us:
- How many fail due to edge cases (can mark as INVALID immediately)
- How many fail due to missing ChromaDB (can improve handling)
- How many fail due to LLM rejection (can improve prompts)
- How many fail due to noise words (can handle differently)

## Recommended Implementation Order

### Phase 0 (First): Verify Problem Exists
1. **Run metrics script** to get baseline NEEDS_REVIEW rates
2. **Add Solution 6** - Detailed validation failure metrics
3. **Analyze LangSmith traces** for sample NEEDS_REVIEW labels
4. **Decide if problem exists** and which solutions to implement

### Phase 1 (Quick Win): Solution 4 - Use edge cases to mark as INVALID
   - Low risk, high impact
   - Reduces NEEDS_REVIEW for known invalid patterns
   - **Only implement if metrics show edge cases are a significant cause**

### Phase 2 (Medium Effort): Solution 2a - Handle missing ChromaDB entries
   - Allows validation even when word not in ChromaDB
   - Reduces false NEEDS_REVIEW
   - **Only implement if metrics show ChromaDB missing is a significant cause**

### Phase 3 (Higher Effort): Solution 3 - Add fallback decision logic
   - More sophisticated decision making
   - Handles edge cases better
   - **Only implement if metrics show LLM rejection is a significant cause**

### Phase 4 (Ongoing): Solution 5 - Improve outlier detection
   - Reduces false positives in outlier detection
   - Prevents valid words from being marked as outliers
   - **Only implement if metrics show outlier rate is too high**

## Metrics to Track

### Current Metrics (Already Tracked)
- ✅ **NEEDS_REVIEW count**: `LabelsNeedsReview` by label type
- ✅ **Update count**: `LabelsUpdated` by label type
- ✅ **Skip count**: `LabelsSkipped` by label type
- ✅ **LLM call metrics**: Total, successful, failed

### New Metrics to Add (Solution 6)
- ❌ **Validation failure reasons**: Edge case, ChromaDB missing, noise word, LLM rejection, exception
- ❌ **Outlier-to-NEEDS_REVIEW rate**: How many outliers end up as NEEDS_REVIEW
- ❌ **Validation success rate**: How often validation succeeds vs fails

### Success Metrics (After Improvements)
- **NEEDS_REVIEW rate**: Should decrease after improvements
- **INVALID rate**: Should increase (more confident rejections)
- **False positive rate**: Track if we're incorrectly marking valid labels as INVALID
- **Validation success rate**: Track how often validation succeeds vs fails

## Testing Strategy

1. Run harmonizer on a sample of receipts
2. Compare NEEDS_REVIEW counts before/after changes
3. Manually review a sample of INVALID labels to check for false positives
4. Monitor LangSmith traces to see validation failure reasons

