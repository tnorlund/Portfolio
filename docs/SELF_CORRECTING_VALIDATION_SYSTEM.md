# Self-Correcting Validation System Design

## Problem Statement

The current validation system has several limitations that prevent it from learning and improving over time:

1. **ChromaDB only queries VALID examples** - Doesn't learn from mistakes
2. **CoVe runs once** - No iterative refinement when errors are found
3. **No contradiction detection** - Doesn't check for logical inconsistencies (multiple GRAND_TOTALs, math mismatches)
4. **No cross-validation** - Different validation methods don't resolve conflicts intelligently
5. **No feedback loop** - Invalid examples aren't stored for future learning

### Example Problem: "TOTAL" vs Currency Value

**Current Issue:**
- Many receipts have the word "TOTAL" labeled as `GRAND_TOTAL`
- The actual currency value (e.g., "$24.01") should be the `GRAND_TOTAL`
- The word "TOTAL" is just a label/header, not the actual total amount

**Why This Happens:**
- LLM sees "TOTAL" near the bottom of receipt
- Pattern matching suggests it's the grand total
- No learning from past mistakes where "TOTAL" was incorrectly labeled

**Impact:**
- False positives: "TOTAL" marked as VALID GRAND_TOTAL
- Missing true positives: Actual currency value not labeled correctly
- System doesn't improve over time

---

## Solution: Self-Correcting Validation System

### Core Principles

1. **Learn from Mistakes**: Store and query INVALID examples, not just VALID ones
2. **Iterative Refinement**: Run CoVe multiple times, using feedback from previous iterations
3. **Contradiction Detection**: Find logical inconsistencies and resolve them
4. **Cross-Validation**: Combine multiple methods with intelligent conflict resolution
5. **Feedback Loop**: Store invalid examples for continuous improvement

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│         Self-Correcting Validation Workflow                  │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
        ┌───────────────────────────────────┐
        │  Step 1: Enhanced ChromaDB        │
        │  - Query VALID examples           │
        │  - Query INVALID examples         │
        │  - Learn from past mistakes       │
        └───────────────────────────────────┘
                            │
                            ▼
        ┌───────────────────────────────────┐
        │  Step 2: Contradiction Detection  │
        │  - Multiple GRAND_TOTALs?         │
        │  - Math inconsistencies?          │
        │  - Multiple MERCHANT_NAMEs?       │
        └───────────────────────────────────┘
                            │
                            ▼
        ┌───────────────────────────────────┐
        │  Step 3: Iterative CoVe           │
        │  - Run CoVe on affected labels    │
        │  - Refine based on feedback       │
        │  - Self-correct up to 3 times     │
        └───────────────────────────────────┘
                            │
                            ▼
        ┌───────────────────────────────────┐
        │  Step 4: Conflict Resolution      │
        │  - Weighted voting                │
        │  - Contradictions override        │
        │  - Final validation decision      │
        └───────────────────────────────────┘
                            │
                            ▼
        ┌───────────────────────────────────┐
        │  Step 5: Store Invalid Examples   │
        │  - Save INVALID labels to DB      │
        │  - Update ChromaDB with mistakes  │
        │  - Improve future validations     │
        └───────────────────────────────────┘
```

---

## Detailed Design

### 1. Enhanced ChromaDB: Learning from Invalid Examples

**Current State:**
- ✅ **Infrastructure exists**: ChromaDB already stores `invalid_labels` in metadata when labels are marked INVALID
- ❌ **Not being used**: Validation only queries `valid_labels`, ignoring `invalid_labels`
- ❌ **No learning**: System doesn't query past mistakes to avoid repeating them

**What "Learning from Past Mistakes" Means:**

When a label is corrected to INVALID, it's stored in ChromaDB metadata:
```python
# In compaction/operations.py (already implemented!)
if status == "INVALID":
    inv_set.add(current_label)
updated_metadata["invalid_labels"] = f",{','.join(sorted(inv_set))},"
```

**The Learning Process:**
1. **Store mistakes**: When a label is marked INVALID, it's stored in ChromaDB
2. **Query both**: When validating, query BOTH `valid_labels` AND `invalid_labels`
3. **Compare similarities**:
   - If similar to VALID examples → likely valid
   - If similar to INVALID examples → likely invalid (learned!)
4. **Make decision**: Use both similarities to determine validity

**Proposed Enhancement: Hybrid Approach (Normalization + Optional LLM)**

**Problem with OCR Variations:**
- OCR might split "$20.00" into "$20" and "00"
- Similarity search might not find "$20" similar to "$20.00" examples
- LLM could understand semantic equivalence, but adds cost/latency

**Solution: Text Normalization Before Similarity Search**

Instead of using LLM for every validation, we can:
1. **Normalize text** before similarity search (handle OCR variations)
2. **Use similarity search** with normalized text (fast, cheap)
3. **Fall back to LLM** only when similarity is ambiguous

**Two Approaches:**

**Approach A: Normalization-Only (Recommended for Most Cases)**
- Normalize currency values: "$20" → "$20.00", "$20.00" → "$20.00"
- Normalize label words: "TOTAL" → "TOTAL", "Total" → "TOTAL"
- Use similarity search with normalized text
- **Pros**: Fast, cheap, handles most OCR variations
- **Cons**: Might miss complex semantic variations

**Approach B: LLM-Assisted (For Ambiguous Cases)**
- Use LLM when similarity scores are ambiguous (e.g., valid_similarity ≈ invalid_similarity)
- LLM understands semantic equivalence without predefined rules
- **Pros**: Handles complex cases, no predefined rules needed
- **Cons**: Slower, more expensive, requires API calls

**Implementation: Approach A (Normalization-Only)**

```python
def normalize_currency_text(text: str) -> str:
    """Normalize currency text to handle OCR variations.

    Examples:
        "$20" → "$20.00"
        "$20.00" → "$20.00"
        "20.00" → "$20.00"
        "$20" + "00" (adjacent) → "$20.00"
    """
    # Remove currency symbols and whitespace
    cleaned = text.replace("$", "").replace(",", "").strip()

    # Try to parse as number
    try:
        # If it's a whole number, assume it's currency with .00
        if "." not in cleaned:
            value = float(cleaned)
            return f"${value:.2f}"
        else:
            value = float(cleaned)
            return f"${value:.2f}"
    except ValueError:
        # Not a number, return as-is (e.g., "TOTAL")
        return text.upper().strip()


def normalize_for_similarity_search(
    word_text: str,
    label_type: str,
    adjacent_words: Optional[Tuple[str, str]] = None,  # (left, right)
) -> str:
    """Normalize word text for similarity search, handling OCR variations."""

    # For currency labels, try to reconstruct split values
    if label_type in ["GRAND_TOTAL", "SUBTOTAL", "TAX", "LINE_TOTAL", "UNIT_PRICE"]:
        # Check if current word + adjacent words form a complete currency value
        if adjacent_words:
            left, right = adjacent_words
            # Try combinations: "$20" + "00" → "$20.00"
            combined = f"{left}{word_text}{right}".replace(" ", "")
            if re.match(r"^\$?\d+\.?\d{0,2}$", combined):
                return normalize_currency_text(combined)

        # Normalize current word
        return normalize_currency_text(word_text)

    # For non-currency labels, just normalize case/whitespace
    return word_text.upper().strip()


async def validate_with_invalid_examples_normalized(
    label: ReceiptWordLabel,
    chroma_client: VectorStoreInterface,
    word_embedding: List[float],
    word_text_lookup: Dict[Tuple[int, int], str],
) -> Dict[str, Any]:
    """Query both VALID and INVALID examples using normalized text."""

    # Get current word and adjacent words
    current_text = word_text_lookup.get((label.line_id, label.word_id), "")
    left_text = word_text_lookup.get((label.line_id, label.word_id - 1), "")
    right_text = word_text_lookup.get((label.line_id, label.word_id + 1), "")

    # Normalize for similarity search
    normalized_text = normalize_for_similarity_search(
        word_text=current_text,
        label_type=label.label,
        adjacent_words=(left_text, right_text),
    )

    # Re-embed normalized text (or use original embedding if normalization didn't change much)
    # For now, use original embedding but filter results by normalized text

    # Query for VALID examples
    valid_results = chroma_client.query(
        collection_name="words",
        query_embeddings=[word_embedding],
        n_results=20,
        where={"valid_labels": {"$in": [label.label]}},
        include=["metadatas", "documents", "distances"],
    )

    # Query for INVALID examples
    invalid_results = chroma_client.query(
        collection_name="words",
        query_embeddings=[word_embedding],
        n_results=20,
        where={"invalid_labels": {"$in": [label.label]}},
        include=["metadatas", "documents", "distances"],
    )

    # Normalize and filter results
    valid_matches = _normalize_and_filter_results(valid_results, normalized_text, label.label)
    invalid_matches = _normalize_and_filter_results(invalid_results, normalized_text, label.label)

    # Calculate similarities
    valid_similarity = _calculate_avg_similarity(valid_matches)
    invalid_similarity = _calculate_avg_similarity(invalid_matches)

    # Decision logic (similarity-based, no LLM needed)
    if valid_similarity > 0.75 and invalid_similarity < 0.65:
        return {"status": "VALID", "confidence": valid_similarity, "method": "normalized_similarity"}
    elif invalid_similarity > 0.75 and valid_similarity < 0.65:
        return {"status": "INVALID", "confidence": invalid_similarity, "method": "normalized_similarity", "reason": "similar_to_invalid_examples"}
    else:
        # Ambiguous - might need LLM fallback
        return {"status": "PENDING", "confidence": 0.5, "method": "normalized_similarity", "needs_llm": True}
```

**Implementation: Approach B (LLM-Assisted - For Ambiguous Cases)**

```python
async def validate_with_invalid_examples_llm(
    label: ReceiptWordLabel,
    chroma_client: VectorStoreInterface,
    dynamo_client: DynamoClient,
    word_embedding: List[float],
    receipt_text: str,
    word_text_lookup: Dict[Tuple[int, int], str],
    llm: ChatOllama,
) -> Dict[str, Any]:
    """Query both VALID and INVALID examples, merge with DynamoDB, and use LLM to decide."""

    # Step 1: Query ChromaDB for VALID examples
    valid_results = chroma_client.query(
        collection_name="words",
        query_embeddings=[word_embedding],
        n_results=10,
        where={"valid_labels": {"$in": [label.label]}},
        include=["metadatas", "documents", "distances"],
    )

    # Step 2: Query ChromaDB for INVALID examples
    invalid_results = chroma_client.query(
        collection_name="words",
        query_embeddings=[word_embedding],
        n_results=10,
        where={"invalid_labels": {"$in": [label.label]}},  # NEW!
        include=["metadatas", "documents", "distances"],
    )

    # Step 3: Parse ChromaDB IDs and fetch full word data from DynamoDB
    valid_examples = await _merge_chromadb_with_dynamo(
        chroma_results=valid_results,
        dynamo_client=dynamo_client,
        word_text_lookup=word_text_lookup,
    )

    invalid_examples = await _merge_chromadb_with_dynamo(
        chroma_results=invalid_results,
        dynamo_client=dynamo_client,
        word_text_lookup=word_text_lookup,
    )

    # Step 4: Build LLM prompt with context
    prompt = _build_validation_prompt(
        label=label,
        receipt_text=receipt_text,
        valid_examples=valid_examples,
        invalid_examples=invalid_examples,
    )

    # Step 5: LLM makes decision
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    decision = _parse_llm_response(response.content)

    return decision


async def _merge_chromadb_with_dynamo(
    chroma_results: Dict[str, Any],
    dynamo_client: DynamoClient,
    word_text_lookup: Dict[Tuple[int, int], str],
) -> List[Dict[str, Any]]:
    """Merge ChromaDB metadata with full DynamoDB word data."""
    examples = []

    if not chroma_results or "ids" not in chroma_results:
        return examples

    for i, chroma_id in enumerate(chroma_results["ids"][0]):
        # Parse ChromaDB ID: IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}
        parts = chroma_id.split("#")
        if len(parts) < 8 or "WORD" not in parts:
            continue

        image_id = parts[1]
        receipt_id = int(parts[3])
        line_id = int(parts[5])
        word_id = int(parts[7])

        # Get metadata from ChromaDB
        metadata = chroma_results["metadatas"][0][i] if "metadatas" in chroma_results else {}
        distance = chroma_results["distances"][0][i] if "distances" in chroma_results else 1.0
        similarity = 1.0 - distance
        document = chroma_results["documents"][0][i] if "documents" in chroma_results else ""

        # Fetch full word data from DynamoDB
        try:
            word = dynamo_client.get_receipt_word(
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=line_id,
                word_id=word_id,
            )

            # Get surrounding context (left/right words)
            left_word = word_text_lookup.get((line_id, word_id - 1), "")
            right_word = word_text_lookup.get((line_id, word_id + 1), "")

            examples.append({
                "word_text": word.text,
                "line_id": line_id,
                "word_id": word_id,
                "similarity": similarity,
                "left_word": left_word,
                "right_word": right_word,
                "x": word.x,
                "y": word.y,
                "merchant_name": metadata.get("merchant_name", ""),
            })
        except Exception as e:
            # Fallback to ChromaDB metadata only
            examples.append({
                "word_text": document or metadata.get("text", ""),
                "line_id": line_id,
                "word_id": word_id,
                "similarity": similarity,
                "merchant_name": metadata.get("merchant_name", ""),
            })

    return examples


def _build_validation_prompt(
    label: ReceiptWordLabel,
    receipt_text: str,
    valid_examples: List[Dict[str, Any]],
    invalid_examples: List[Dict[str, Any]],
) -> str:
    """Build LLM prompt with ChromaDB examples as context."""

    # Format valid examples
    valid_examples_text = ""
    if valid_examples:
        valid_examples_text = "\n".join([
            f"  - Word: '{ex['word_text']}' (line {ex['line_id']}, word {ex['word_id']}, "
            f"similarity: {ex['similarity']:.2f}, context: '{ex.get('left_word', '')} {ex['word_text']} {ex.get('right_word', '')}')"
            for ex in valid_examples[:5]  # Top 5 examples
        ])
    else:
        valid_examples_text = "  (No similar VALID examples found)"

    # Format invalid examples
    invalid_examples_text = ""
    if invalid_examples:
        invalid_examples_text = "\n".join([
            f"  - Word: '{ex['word_text']}' (line {ex['line_id']}, word {ex['word_id']}, "
            f"similarity: {ex['similarity']:.2f}, context: '{ex.get('left_word', '')} {ex['word_text']} {ex.get('right_word', '')}')"
            for ex in invalid_examples[:5]  # Top 5 examples
        ])
    else:
        invalid_examples_text = "  (No similar INVALID examples found)"

    # Get current word context
    current_word_text = label.word_text or ""
    current_left = ""  # Would need to fetch from word_text_lookup
    current_right = ""  # Would need to fetch from word_text_lookup

    prompt = f"""You are validating a receipt word label. Your task is to determine if the label is VALID or INVALID based on similar examples from past validations.

CURRENT LABEL TO VALIDATE:
- Label Type: {label.label}
- Word Text: "{current_word_text}"
- Line ID: {label.line_id}, Word ID: {label.word_id}
- Context: "{current_left} {current_word_text} {current_right}"

RECEIPT TEXT (for reference):
{receipt_text[:1000]}

SIMILAR VALID EXAMPLES (words correctly labeled as {label.label}):
{valid_examples_text}

SIMILAR INVALID EXAMPLES (words incorrectly labeled as {label.label}):
{invalid_examples_text}

ANALYSIS:
Compare the current word to both the VALID and INVALID examples. Consider:
1. Does the current word match the pattern of VALID examples?
2. Does the current word match the pattern of INVALID examples (which would indicate it's wrong)?
3. What is the semantic similarity? (e.g., for GRAND_TOTAL, is it a currency value like "$24.01" or a label word like "TOTAL"?)

RESPONSE FORMAT (JSON):
{{
  "decision": "VALID" | "INVALID" | "PENDING",
  "confidence": 0.0-1.0,
  "reasoning": "Brief explanation of why this decision was made",
  "key_patterns": ["pattern1", "pattern2"]  // What patterns from examples influenced the decision
}}

Respond with ONLY valid JSON, no markdown formatting."""

    return prompt
```

**Example: Handling OCR Split "$20" + "00" → "$20.00"**

**Scenario:**
- Receipt has: `TOTAL $20.00`
- OCR splits it into: Word 1: "TOTAL", Word 2: "$20", Word 3: "00"
- Word "$20" is labeled as GRAND_TOTAL
- ChromaDB has valid examples like "$20.00", "$15.99", "$24.01"

**Without Normalization:**
```python
# Query ChromaDB for "$20"
valid_results = chroma_client.query(query_embeddings=[embedding_for_$20], ...)
# Similarity to "$20.00" examples: 0.45 (low - different embeddings!)
# Decision: PENDING (needs LLM or manual review)
```

**With Normalization:**
```python
# Step 1: Detect adjacent words
current_text = "$20"
left_text = "TOTAL"
right_text = "00"

# Step 2: Try to reconstruct complete currency value
combined = f"{left_text}{current_text}{right_text}".replace(" ", "")  # "TOTAL$2000"
# Check if "$20" + "00" forms a currency pattern
if re.match(r"^\$?\d+\.?\d{0,2}$", "$2000"):  # Matches!
    normalized = normalize_currency_text("$2000")  # "$2000.00"
# Or better: detect "$20" + "00" as split
if current_text.startswith("$") and right_text.isdigit() and len(right_text) <= 2:
    normalized = normalize_currency_text(f"{current_text}.{right_text}")  # "$20.00"

# Step 3: Query ChromaDB with normalized understanding
# Now "$20" is treated as "$20.00" for similarity search
# Similarity to "$20.00" examples: 0.85 (high - normalized match!)
# Decision: VALID (no LLM needed!)
```

**When to Use Each Approach:**

| Scenario | Approach | Why |
|----------|----------|-----|
| "$20" vs "$20.00" (OCR split) | **Normalization** | Adjacent word detection + currency normalization handles this |
| "TOTAL" vs "Total" (case variation) | **Normalization** | Case normalization handles this |
| "$20" vs "20.00" (format variation) | **Normalization** | Currency normalization handles this |
| Ambiguous similarity (0.70 vs 0.68) | **LLM** | Need semantic understanding |
| Complex semantic equivalence | **LLM** | Normalization rules can't cover everything |
| High confidence (0.85 vs 0.30) | **Normalization** | Clear pattern, no LLM needed |

**Recommended Workflow:**

```python
async def validate_with_hybrid_approach(
    label: ReceiptWordLabel,
    chroma_client: VectorStoreInterface,
    dynamo_client: DynamoClient,
    word_embedding: List[float],
    receipt_text: str,
    word_text_lookup: Dict[Tuple[int, int], str],
    llm: Optional[ChatOllama] = None,  # Optional - only used if needed
) -> Dict[str, Any]:
    """Hybrid approach: Normalization first, LLM fallback if ambiguous."""

    # Step 1: Try normalization-based similarity search
    result = await validate_with_invalid_examples_normalized(
        label=label,
        chroma_client=chroma_client,
        word_embedding=word_embedding,
        word_text_lookup=word_text_lookup,
    )

    # Step 2: If ambiguous and LLM available, use LLM
    if result.get("needs_llm") and llm:
        logger.info(f"   🤖 Similarity ambiguous, using LLM for {label.label}")
        llm_result = await validate_with_invalid_examples_llm(
            label=label,
            chroma_client=chroma_client,
            dynamo_client=dynamo_client,
            word_embedding=word_embedding,
            receipt_text=receipt_text,
            word_text_lookup=word_text_lookup,
            llm=llm,
        )
        return {**llm_result, "method": "llm_fallback"}

    # Step 3: Return normalization result
    return result
```

**Example Prompt with Real Data (LLM Approach):**

Here's what the prompt looks like when validating "TOTAL" as GRAND_TOTAL:

```
You are validating a receipt word label. Your task is to determine if the label is VALID or INVALID based on similar examples from past validations.

CURRENT LABEL TO VALIDATE:
- Label Type: GRAND_TOTAL
- Word Text: "TOTAL"
- Line ID: 5, Word ID: 12
- Context: "Subtotal $15.99 TOTAL $18.50"

RECEIPT TEXT (for reference):
Subtotal $15.99
Tax $2.51
TOTAL $18.50
Thank you for your visit!

SIMILAR VALID EXAMPLES (words correctly labeled as GRAND_TOTAL):
  - Word: '$18.50' (line 5, word 14, similarity: 0.35, context: 'TOTAL $18.50 Thank')
  - Word: '$24.01' (line 6, word 8, similarity: 0.32, context: 'Total $24.01 Cash')
  - Word: '$15.99' (line 4, word 6, similarity: 0.28, context: 'Subtotal $15.99 Tax')
  - Word: '$100.00' (line 7, word 10, similarity: 0.30, context: 'Amount $100.00 Paid')
  - Word: '$45.67' (line 5, word 12, similarity: 0.33, context: 'Grand $45.67 Total')

SIMILAR INVALID EXAMPLES (words incorrectly labeled as GRAND_TOTAL):
  - Word: 'TOTAL' (line 5, word 12, similarity: 0.88, context: 'Subtotal $15.99 TOTAL $18.50')
  - Word: 'AMOUNT' (line 6, word 2, similarity: 0.82, context: 'Total AMOUNT $24.01')
  - Word: 'Amount:' (line 4, word 1, similarity: 0.79, context: 'Amount: $15.99 Subtotal')
  - Word: 'Total' (line 5, word 11, similarity: 0.85, context: 'Grand Total $45.67')
  - Word: 'TOTAL:' (line 6, word 3, similarity: 0.87, context: 'Subtotal $15.99 TOTAL: $18.50')

ANALYSIS:
Compare the current word to both the VALID and INVALID examples. Consider:
1. Does the current word match the pattern of VALID examples?
2. Does the current word match the pattern of INVALID examples (which would indicate it's wrong)?
3. What is the semantic similarity? (e.g., for GRAND_TOTAL, is it a currency value like "$24.01" or a label word like "TOTAL"?)

RESPONSE FORMAT (JSON):
{
  "decision": "VALID" | "INVALID" | "PENDING",
  "confidence": 0.0-1.0,
  "reasoning": "Brief explanation of why this decision was made",
  "key_patterns": ["pattern1", "pattern2"]
}

Respond with ONLY valid JSON, no markdown formatting.
```

**LLM Response:**
```json
{
  "decision": "INVALID",
  "confidence": 0.92,
  "reasoning": "The word 'TOTAL' is a label word, not a currency value. All VALID examples are currency values (e.g., '$18.50', '$24.01'), while INVALID examples are label words (e.g., 'TOTAL', 'AMOUNT', 'Amount:'). The current word matches the INVALID pattern with 0.88 similarity.",
  "key_patterns": [
    "VALID examples are currency values with $ symbol",
    "INVALID examples are label words like 'TOTAL', 'AMOUNT', 'Amount:'",
    "Current word 'TOTAL' is semantically similar to INVALID examples, not VALID examples"
  ]
}
```

**Key Points:**
- **ChromaDB provides**: Similarity scores, word IDs, basic metadata
- **DynamoDB provides**: Full word data (text, position, geometry), surrounding context
- **Merged together**: LLM gets complete picture of both current word and similar examples
- **LLM decides**: Uses pattern recognition to determine validity, not just similarity thresholds

**Example: "TOTAL" vs Currency Value - Step by Step**

**Week 1: First Mistake (Manual Correction)**
```
Receipt 1:
- Word "TOTAL" labeled as GRAND_TOTAL (PENDING)
- Human reviewer corrects to INVALID
- ChromaDB compaction updates metadata:
  invalid_labels = ",GRAND_TOTAL,"  ← Stored in ChromaDB!
```

**Week 2: System Learns (Automatic Detection)**
```
Receipt 2:
- Word "TOTAL" labeled as GRAND_TOTAL (PENDING)
- Enhanced validation queries ChromaDB:

  Query 1: VALID examples
  where={"valid_labels": {"$in": ["GRAND_TOTAL"]}}
  Results: "$24.01", "$15.99", "$100.00" (currency values)
  Similarity: 0.35 (low - "TOTAL" is not similar to currency values)

  Query 2: INVALID examples  ← NEW!
  where={"invalid_labels": {"$in": ["GRAND_TOTAL"]}}
  Results: "TOTAL" (from Receipt 1), "AMOUNT", "AMOUNT:" (past mistakes)
  Similarity: 0.88 (high - "TOTAL" is very similar to past mistakes!)

- Decision Logic:
  - valid_similarity (0.35) < invalid_similarity (0.88)
  - invalid_similarity > 0.75 (threshold)
  - **Decision: INVALID** (learned from past mistake!)
```

**Week 3: Pattern Learned (Automatic)**
```
Receipt 3:
- Word "TOTAL" → Immediately marked INVALID
- System has learned: "TOTAL" is not a currency value
- No CoVe needed (ChromaDB similarity is strong enough)
```

**Week 4: Correct Identification**
```
Receipt 4:
- Word "$24.01" labeled as GRAND_TOTAL (PENDING)
- Enhanced validation queries ChromaDB:

  Query 1: VALID examples
  Results: "$24.01", "$15.99", "$100.00"
  Similarity: 0.82 (high - similar to valid currency values)

  Query 2: INVALID examples
  Results: "TOTAL", "AMOUNT", "AMOUNT:"
  Similarity: 0.30 (low - "$24.01" is not similar to label words)

- Decision Logic:
  - valid_similarity (0.82) > invalid_similarity (0.30)
  - valid_similarity > 0.75 (threshold)
  - **Decision: VALID** (correctly identifies currency value)
```

**Key Insight:**
- The system doesn't just query what exists - it **learns patterns** from corrections
- Each INVALID correction becomes a **negative example** for future validations
- Over time, the system builds a knowledge base of what NOT to label as GRAND_TOTAL

---

### 2. Contradiction Detection and Resolution

**Relationship to Step 1 (Enhanced ChromaDB):**

Step 1 and Step 2 are complementary but different:

- **Step 1 (Enhanced ChromaDB)**: Cross-receipt pattern matching
  - Compares a SINGLE label to historical examples from OTHER receipts
  - Uses similarity search: "Is this word similar to past valid examples or past invalid examples?"
  - Focus: Learning from past mistakes across the entire dataset

- **Step 2 (Contradiction Detection)**: Intra-receipt logical consistency
  - Compares labels WITHIN THE SAME RECEIPT
  - Uses logical rules: "Do these labels make sense together on this receipt?"
  - Focus: Detecting inconsistencies within a single receipt

**How They Work Together:**

Step 1 can **trigger** Step 2's contradiction checks:
- If Step 1 finds "TOTAL" is similar to invalid examples → Step 2 checks for "label_word_not_value" contradiction
- If Step 1 finds multiple similar valid examples → Step 2 checks for "multiple_grand_totals" contradiction

Step 2 can **use** Step 1's results:
- When detecting "label_word_not_value", query ChromaDB to confirm pattern
- When detecting "math_mismatch", use ChromaDB to find which label is likely wrong

**Detect Logical Inconsistencies:**

```python
def detect_contradictions(
    labels: List[ReceiptWordLabel],
    receipt_text: str,
    word_text_lookup: Dict[Tuple[int, int], str],
    chromadb_validation_results: Optional[Dict[str, Any]] = None,  # NEW: Feed from Step 1
) -> List[Dict[str, Any]]:
    """Detect logical inconsistencies that indicate labeling errors.

    Args:
        labels: Labels to check for contradictions
        receipt_text: Full receipt text for context
        word_text_lookup: Map of (line_id, word_id) -> word text
        chromadb_validation_results: Optional results from Step 1 (Enhanced ChromaDB)
                                    Format: {label_id: {"decision": "VALID/INVALID", "similarity": 0.85, ...}}
    """

    contradictions = []

    # Check 1: Multiple GRAND_TOTAL labels?
    grand_totals = [l for l in labels if l.label == "GRAND_TOTAL"]
    if len(grand_totals) > 1:
        word_texts = [word_text_lookup.get((l.line_id, l.word_id), "") for l in grand_totals]
        contradictions.append({
            "type": "multiple_grand_totals",
            "labels": grand_totals,
            "word_texts": word_texts,
            "action": "re_verify_all_with_cove",
            "priority": "high",
        })

    # Check 2: Mathematical inconsistency?
    subtotal = find_label("SUBTOTAL", labels, word_text_lookup)
    tax = find_label("TAX", labels, word_text_lookup)
    grand_total = find_label("GRAND_TOTAL", labels, word_text_lookup)

    if subtotal and tax and grand_total:
        expected = subtotal["value"] + tax["value"]
        actual = grand_total["value"]
        if abs(expected - actual) > 0.01:
            contradictions.append({
                "type": "math_mismatch",
                "expected": expected,
                "actual": actual,
                "difference": abs(expected - actual),
                "labels": [subtotal["label"], tax["label"], grand_total["label"]],
                "action": "re_verify_all_three_with_cove",
                "priority": "high",
            })

    # Check 3: "TOTAL" word labeled as GRAND_TOTAL (common mistake)
    # This can be triggered by Step 1 (Enhanced ChromaDB) results
    for label in labels:
        if label.label == "GRAND_TOTAL":
            word_text = word_text_lookup.get((label.line_id, label.word_id), "")

            # Option A: Direct pattern check (existing logic)
            if word_text.upper().strip() in ["TOTAL", "TOTAL:", "AMOUNT", "AMOUNT:"]:
                contradictions.append({
                    "type": "label_word_not_value",
                    "label": label,
                    "word_text": word_text,
                    "action": "re_verify_with_cove",
                    "priority": "high",
                    "reason": "GRAND_TOTAL should be a currency value, not the word 'TOTAL'",
                    "triggered_by": "pattern_match",  # Direct pattern detection
                })

            # Option B: Triggered by Step 1 (Enhanced ChromaDB) results
            elif chromadb_validation_results:
                label_id = f"{label.image_id}#{label.receipt_id}#{label.line_id}#{label.word_id}"
                chroma_result = chromadb_validation_results.get(label_id, {})

                # If ChromaDB found it similar to invalid examples, check for contradiction
                if chroma_result.get("decision") == "INVALID":
                    invalid_similarity = chroma_result.get("invalid_similarity", 0.0)
                    valid_similarity = chroma_result.get("valid_similarity", 0.0)

                    # High similarity to invalid examples suggests contradiction
                    if invalid_similarity > 0.75 and valid_similarity < 0.65:
                        contradictions.append({
                            "type": "label_word_not_value",
                            "label": label,
                            "word_text": word_text,
                            "action": "re_verify_with_cove",
                            "priority": "high",
                            "reason": f"ChromaDB found word similar to invalid examples (similarity: {invalid_similarity:.2f}). GRAND_TOTAL should be a currency value, not a label word.",
                            "triggered_by": "chromadb_validation",  # Triggered by Step 1
                            "chromadb_evidence": {
                                "invalid_similarity": invalid_similarity,
                                "valid_similarity": valid_similarity,
                            },
                        })

    # Check 4: Multiple MERCHANT_NAME labels?
    merchants = [l for l in labels if l.label == "MERCHANT_NAME"]
    if len(merchants) > 1:
        contradictions.append({
            "type": "multiple_merchants",
            "labels": merchants,
            "action": "re_verify_all_with_cove",
            "priority": "medium",
        })

    return contradictions
```

**Example: "TOTAL" Contradiction Detection (Triggered by Step 1)**

**Receipt:**
```
Line 10: SUBTOTAL                    $20.00
Line 11: TAX                         $1.60
Line 12: TOTAL                       $21.60
```

**Step 1 (Enhanced ChromaDB) Results:**
```python
# Query ChromaDB for "TOTAL" word labeled as GRAND_TOTAL
chromadb_results = {
    "image123#receipt456#line12#word5": {
        "decision": "INVALID",
        "valid_similarity": 0.35,  # Not similar to valid currency values
        "invalid_similarity": 0.88,  # Very similar to past "TOTAL" mistakes
        "reasoning": "Word 'TOTAL' matches pattern of invalid examples (label words, not currency values)",
    }
}
```

**Step 2 (Contradiction Detection) - Triggered by Step 1:**
```python
contradictions = detect_contradictions(
    labels,
    receipt_text,
    word_text_lookup,
    chromadb_validation_results=chromadb_results,  # Feed from Step 1
)
# Returns:
[{
    "type": "label_word_not_value",
    "label": <label for "TOTAL">,
    "word_text": "TOTAL",
    "action": "re_verify_with_cove",
    "priority": "high",
    "reason": "ChromaDB found word similar to invalid examples (similarity: 0.88). GRAND_TOTAL should be a currency value, not a label word.",
    "triggered_by": "chromadb_validation",  # ← Triggered by Step 1!
    "chromadb_evidence": {
        "invalid_similarity": 0.88,
        "valid_similarity": 0.35,
    },
}]
```

**Resolution:**
- Step 1 identified pattern: "TOTAL" is similar to past invalid examples
- Step 2 detected contradiction: "TOTAL" is a label word, not a currency value
- Re-verify "TOTAL" label with CoVe (Step 3)
- CoVe generates question: "Is 'TOTAL' a currency amount or just a label?"
- CoVe answers: "It's just a label, not a currency value"
- **Action**: Mark "TOTAL" as INVALID, search for actual currency value nearby
- **Result**: "$21.60" correctly labeled as GRAND_TOTAL

**Key Insight:**
- Step 1 (Enhanced ChromaDB) provides **evidence** (similarity scores, pattern matching)
- Step 2 (Contradiction Detection) uses that evidence to **detect logical inconsistencies**
- Together, they catch errors that neither could catch alone

---

### 3. Iterative CoVe Refinement

**Relationship to Step 1 (Enhanced ChromaDB):**

Step 1 provides **evidence and context** that feeds into Step 3's CoVe process:

- **Step 1 Results**: Similarity scores, valid/invalid examples, pattern matches
- **Step 3 Uses**: This evidence to generate better verification questions and focus CoVe on problematic labels

**How Step 1 Feeds into Step 3:**

1. **Question Generation**: Step 1's evidence helps CoVe generate more targeted questions
   - If Step 1 found word similar to invalid examples → CoVe asks: "Is this word a label or a value?"
   - If Step 1 found ambiguous similarity → CoVe asks: "Does this match the pattern of valid examples?"

2. **Focus Iterations**: Step 1 identifies which labels need more scrutiny
   - High confidence from Step 1 → Skip CoVe (already validated)
   - Low/ambiguous confidence → Prioritize for CoVe refinement

3. **Context for Revision**: Step 1's examples provide context for CoVe's revision step
   - Show valid/invalid examples to LLM during revision
   - LLM can compare current label to similar past examples

**Run CoVe Multiple Times with Feedback:**

```python
async def iterative_cove_validation(
    labels: List[ReceiptWordLabel],
    receipt_text: str,
    llm: ChatOllama,
    max_iterations: int = 3,
    chromadb_validation_results: Optional[Dict[str, Any]] = None,  # NEW: Feed from Step 1
    chromadb_examples: Optional[Dict[str, Dict[str, List[Dict[str, Any]]]]] = None,  # NEW: Valid/invalid examples
) -> Tuple[List[ReceiptWordLabel], Dict[str, Any]]:
    """Run CoVe multiple times, each iteration using feedback from previous.

    Args:
        labels: Labels to validate
        receipt_text: Full receipt text
        llm: LLM instance for CoVe
        max_iterations: Maximum number of CoVe iterations
        chromadb_validation_results: Optional results from Step 1 (Enhanced ChromaDB)
                                    Format: {label_id: {"decision": "VALID/INVALID", "similarity": 0.85, ...}}
        chromadb_examples: Optional valid/invalid examples from Step 1
                          Format: {label_id: {"valid": [...], "invalid": [...]}}
    """

    iteration_stats = []
    current_labels = labels.copy()

    # Filter labels based on Step 1 results (prioritize ambiguous ones)
    labels_to_verify = _prioritize_labels_for_cove(
        labels=current_labels,
        chromadb_validation_results=chromadb_validation_results,
    )

    for iteration in range(max_iterations):
        logger.info(f"   🔄 CoVe Iteration {iteration + 1}/{max_iterations}")

        # Build task description with Step 1 context
        task_description = _build_cove_task_description(
            iteration=iteration + 1,
            previous_errors=iteration_stats[-1]['errors_found'] if iteration_stats else 0,
            chromadb_validation_results=chromadb_validation_results,
            labels_to_verify=labels_to_verify,
        )

        # Run CoVe with Step 1 context
        verified_labels, cove_verified = await apply_chain_of_verification_with_context(
            initial_answer=create_phase_context_response(current_labels),
            receipt_text=receipt_text,
            task_description=task_description,
            response_model=PhaseContextResponse,
            llm=llm,
            enable_cove=True,
            chromadb_examples=chromadb_examples,  # NEW: Provide examples to CoVe
        )

        # Check if we found errors
        errors_found = sum(
            1 for l in verified_labels
            if l.validation_status == "INVALID"
        )

        # Count corrections made
        corrections = 0
        for i, verified_label in enumerate(verified_labels):
            if verified_label.validation_status != current_labels[i].validation_status:
                corrections += 1

        iteration_stats.append({
            "iteration": iteration + 1,
            "errors_found": errors_found,
            "corrections": corrections,
            "cove_verified": cove_verified,
        })

        if not errors_found and cove_verified:
            # No errors found, verification passed
            logger.info(f"   ✅ Iteration {iteration + 1}: No errors found, validation complete")
            return verified_labels, {"iterations": iteration_stats, "converged": True}

        # Errors found - use revised labels as input for next iteration
        logger.info(f"   🔄 Iteration {iteration + 1}: Found {errors_found} errors, refining...")
        current_labels = verified_labels

    logger.info(f"   ⚠️  Reached max iterations ({max_iterations}), returning final result")
    return current_labels, {"iterations": iteration_stats, "converged": False}


def _prioritize_labels_for_cove(
    labels: List[ReceiptWordLabel],
    chromadb_validation_results: Optional[Dict[str, Any]],
) -> List[ReceiptWordLabel]:
    """Prioritize labels for CoVe based on Step 1 results.

    Strategy:
    - High confidence from Step 1 → Skip (already validated)
    - Low/ambiguous confidence → Prioritize for CoVe
    - Similar to invalid examples → High priority
    """
    if not chromadb_validation_results:
        return labels  # No Step 1 results, verify all

    prioritized = []
    for label in labels:
        label_id = f"{label.image_id}#{label.receipt_id}#{label.line_id}#{label.word_id}"
        chroma_result = chromadb_validation_results.get(label_id, {})

        decision = chroma_result.get("decision", "PENDING")
        confidence = chroma_result.get("confidence", 0.5)

        # Skip high-confidence validations from Step 1
        if decision == "VALID" and confidence > 0.80:
            logger.debug(f"   ⏭️  Skipping {label.label} - high confidence from Step 1 ({confidence:.2f})")
            continue

        # Prioritize ambiguous or invalid results
        if decision == "INVALID" or decision == "PENDING" or confidence < 0.70:
            prioritized.append(label)

    return prioritized


def _build_cove_task_description(
    iteration: int,
    previous_errors: int,
    chromadb_validation_results: Optional[Dict[str, Any]],
    labels_to_verify: List[ReceiptWordLabel],
) -> str:
    """Build task description for CoVe with Step 1 context."""

    base_description = (
        f"Iteration {iteration}: Verify labels. "
        f"Previous iteration found {previous_errors} errors."
    )

    if not chromadb_validation_results:
        return base_description

    # Add Step 1 context
    invalid_count = sum(
        1 for label in labels_to_verify
        if chromadb_validation_results.get(
            f"{label.image_id}#{label.receipt_id}#{label.line_id}#{label.word_id}",
            {}
        ).get("decision") == "INVALID"
    )

    ambiguous_count = sum(
        1 for label in labels_to_verify
        if chromadb_validation_results.get(
            f"{label.image_id}#{label.receipt_id}#{label.line_id}#{label.word_id}",
            {}
        ).get("decision") == "PENDING"
    )

    context = (
        f"\n\nChromaDB validation (Step 1) found:\n"
        f"- {invalid_count} labels similar to invalid examples (high priority for verification)\n"
        f"- {ambiguous_count} labels with ambiguous similarity scores\n"
        f"\nFocus verification questions on these problematic labels."
    )

    return base_description + context


async def apply_chain_of_verification_with_context(
    initial_answer: Any,
    receipt_text: str,
    task_description: str,
    response_model: Type[T],
    llm: ChatOllama,
    enable_cove: bool = True,
    chromadb_examples: Optional[Dict[str, Dict[str, List[Dict[str, Any]]]]] = None,
) -> Tuple[T, bool]:
    """Enhanced CoVe that uses Step 1's examples as context."""

    # Enhanced question generation with Step 1 examples
    questions_response = await generate_verification_questions_with_examples(
        initial_answer=initial_answer,
        receipt_text=receipt_text,
        task_description=task_description,
        llm=llm,
        chromadb_examples=chromadb_examples,  # NEW: Provide examples
    )

    # Rest of CoVe process (answer questions, revise) remains the same
    # ... (existing CoVe logic) ...


async def generate_verification_questions_with_examples(
    initial_answer: Any,
    receipt_text: str,
    task_description: str,
    llm: ChatOllama,
    chromadb_examples: Optional[Dict[str, Dict[str, List[Dict[str, Any]]]]] = None,
) -> VerificationQuestionsResponse:
    """Generate verification questions with Step 1's examples as context."""

    # Convert answer to JSON
    if hasattr(initial_answer, "model_dump"):
        answer_json = json.dumps(initial_answer.model_dump(), indent=2)
    else:
        answer_json = str(initial_answer)

    # Build examples context from Step 1
    examples_context = ""
    if chromadb_examples:
        examples_context = "\n\nHISTORICAL EXAMPLES FROM SIMILAR VALIDATIONS:\n"
        for label_id, examples in chromadb_examples.items():
            valid_examples = examples.get("valid", [])
            invalid_examples = examples.get("invalid", [])

            if valid_examples:
                examples_context += f"\nValid examples for this label type:\n"
                for ex in valid_examples[:3]:  # Top 3
                    examples_context += f"  - '{ex.get('word_text', '')}' (similarity: {ex.get('similarity', 0):.2f})\n"

            if invalid_examples:
                examples_context += f"\nInvalid examples (common mistakes):\n"
                for ex in invalid_examples[:3]:  # Top 3
                    examples_context += f"  - '{ex.get('word_text', '')}' (similarity: {ex.get('similarity', 0):.2f})\n"

    # Enhanced prompt with examples
    prompt = f"""You are a verification expert. Your task is to generate specific, answerable questions that can verify the accuracy of an initial analysis.

TASK: {task_description}

INITIAL ANSWER:
{answer_json}

RECEIPT TEXT (source material):
{receipt_text[:2000]}

{examples_context}

CRITICAL INSTRUCTIONS:
1. Use the historical examples above to identify common mistakes
2. Generate questions that check if the current labels match valid patterns or invalid patterns
3. Focus on labels that are similar to past invalid examples

Generate 3-5 specific verification questions. Each question should:
1. Target a specific claim in the initial answer
2. Be answerable by examining the receipt text
3. Help identify potential errors or uncertainties
4. Consider patterns from historical examples (both valid and invalid)

Focus on:
- Verifying amounts match what's actually on the receipt
- Checking that label types (GRAND_TOTAL, TAX, etc.) are correctly assigned
- Ensuring line_ids point to the correct locations
- Validating that extracted text matches receipt content
- Comparing to historical valid/invalid examples

Return ONLY valid JSON matching the schema. No markdown formatting."""

    # ... (rest of question generation logic) ...
```

**Example: Iterative Refinement for "TOTAL" (With Step 1 Context)**

**Step 1 (Enhanced ChromaDB) Results:**
```python
chromadb_results = {
    "image123#receipt456#line12#word5": {
        "decision": "INVALID",
        "valid_similarity": 0.35,
        "invalid_similarity": 0.88,
        "reasoning": "Word 'TOTAL' matches pattern of invalid examples",
    }
}

chromadb_examples = {
    "image123#receipt456#line12#word5": {
        "valid": [
            {"word_text": "$20.00", "similarity": 0.35, "context": "TOTAL $20.00"},
            {"word_text": "$15.99", "similarity": 0.32, "context": "Amount $15.99"},
        ],
        "invalid": [
            {"word_text": "TOTAL", "similarity": 0.88, "context": "Subtotal $15.99 TOTAL $18.50"},
            {"word_text": "AMOUNT", "similarity": 0.82, "context": "Total AMOUNT $24.01"},
        ],
    }
}
```

**Step 3 (Iterative CoVe) - Iteration 1:**

**Input:**
- "TOTAL" labeled as GRAND_TOTAL
- Step 1 found: Similar to invalid examples (0.88 similarity)

**CoVe Question Generation (Enhanced with Step 1 Context):**
```
HISTORICAL EXAMPLES FROM SIMILAR VALIDATIONS:

Valid examples for this label type:
  - '$20.00' (similarity: 0.35)
  - '$15.99' (similarity: 0.32)

Invalid examples (common mistakes):
  - 'TOTAL' (similarity: 0.88)  ← Current word matches this!
  - 'AMOUNT' (similarity: 0.82)

Question: "Is 'TOTAL' a currency amount like '$20.00' and '$15.99', or is it a label word like the invalid examples 'TOTAL' and 'AMOUNT'?"
```

**CoVe Answer:**
```
"No, 'TOTAL' is a label word, not a currency value. Valid GRAND_TOTAL examples are currency values like '$20.00', while invalid examples are label words like 'TOTAL' and 'AMOUNT'. The current word 'TOTAL' matches the invalid pattern."
```

**Result:**
- Mark "TOTAL" as INVALID
- **Errors Found: 1**

**Iteration 2:**

**Input:**
- "TOTAL" marked as INVALID
- "$21.60" not labeled
- Step 1 context: Need to find actual currency value

**CoVe Question:**
```
"Based on the invalid 'TOTAL' label we just corrected, what is the actual grand total currency amount on this receipt? Look for a value like '$20.00' or '$15.99' (valid examples from Step 1)."
```

**CoVe Answer:**
```
"$21.60 is the grand total amount. It appears after the word 'TOTAL' and matches the pattern of valid currency values."
```

**Result:**
- Label "$21.60" as GRAND_TOTAL
- **Errors Found: 0, Converged!**

**Key Benefits of Step 1 → Step 3 Integration:**
- **Better Questions**: CoVe asks targeted questions based on Step 1's evidence
- **Faster Convergence**: Step 1 identifies problematic labels, CoVe focuses on them
- **More Accurate**: CoVe uses historical examples to understand patterns
- **Fewer Iterations**: Step 1's context helps CoVe make better decisions faster

---

### 4. Cross-Validation Conflict Resolution

**Purpose:**
Step 4 resolves conflicts when different validation methods disagree. It combines results from Steps 1-3 using weighted voting to make a final decision.

**Why It's Needed:**
- **Step 1 (ChromaDB)** might say: VALID (high similarity to valid examples)
- **Step 2 (Contradiction)** might say: INVALID (logical inconsistency detected)
- **Step 3 (CoVe)** might say: VALID (LLM verified it's correct)

**Step 4's Role:**
- Combines all three methods' results
- Uses weighted voting (some methods are more reliable than others)
- Makes final decision when methods disagree
- Handles edge cases where one method is wrong

**How Steps 1-3 Feed Into Step 4:**

1. **Step 1 Results** → `chromadb_results`: Similarity scores, decisions, confidence
2. **Step 2 Results** → `contradiction_results`: Logical inconsistencies, priority
3. **Step 3 Results** → `cove_results`: LLM verification, reasoning, confidence

**Intelligently Combine Multiple Validation Methods:**

```python
def resolve_validation_conflicts(
    labels: List[ReceiptWordLabel],
    chromadb_results: Dict[str, Dict[str, Any]],  # From Step 1
    cove_results: Dict[str, Dict[str, Any]],  # From Step 3
    contradiction_results: List[Dict[str, Any]],  # From Step 2
) -> List[ReceiptWordLabel]:
    """Resolve conflicts between validation methods using weighted voting.

    Args:
        labels: Labels to resolve conflicts for
        chromadb_results: Step 1 results - Format: {label_id: {"status": "VALID/INVALID", "confidence": 0.85, "reason": "...", ...}}
        cove_results: Step 3 results - Format: {label_id: {"status": "VALID/INVALID", "confidence": 0.90, "reasoning": "...", ...}}
        contradiction_results: Step 2 results - Format: [{"type": "...", "label": <label>, "priority": "high/medium", ...}]

    Returns:
        Labels with final validation_status resolved
    """

    # Build contradiction lookup
    contradiction_invalidated = {
        c["label"].id for c in contradiction_results
        if c.get("action") == "re_verify_with_cove"
    }

    for label in labels:
        # Collect votes from each method
        votes = {
            "valid": 0,
            "invalid": 0,
            "pending": 0,
        }

        # Step 1 (ChromaDB) vote (weight: 1 - fast but can be wrong)
        chroma_result = chromadb_results.get(label.id, {})
        chroma_status = chroma_result.get("status") or chroma_result.get("decision", "PENDING")
        chroma_confidence = chroma_result.get("confidence", 0.5)

        if chroma_status == "VALID":
            votes["valid"] += 1
            # Bonus weight for high confidence
            if chroma_confidence > 0.85:
                votes["valid"] += 0.5
        elif chroma_status == "INVALID":
            votes["invalid"] += 1
            # Bonus weight for learning from mistakes (Step 1's key feature)
            if chroma_result.get("reason") == "similar_to_invalid_examples":
                votes["invalid"] += 1  # Extra weight: learning from past mistakes is valuable
            # Bonus weight for high confidence
            if chroma_confidence > 0.85:
                votes["invalid"] += 0.5

        # Step 3 (CoVe) vote (weight: 2 - more reliable, uses LLM reasoning)
        cove_result = cove_results.get(label.id, {})
        cove_status = cove_result.get("status") or cove_result.get("decision", "PENDING")
        cove_confidence = cove_result.get("confidence", 0.5)

        if cove_status == "VALID":
            votes["valid"] += 2
            # Bonus weight for high confidence
            if cove_confidence > 0.90:
                votes["valid"] += 1
        elif cove_status == "INVALID":
            votes["invalid"] += 2
            # Bonus weight for high confidence
            if cove_confidence > 0.90:
                votes["invalid"] += 1

        # Step 2 (Contradiction Detection) vote (weight: 3 - strongest signal, logical rules)
        if label.id in contradiction_invalidated:
            votes["invalid"] += 3
            # Contradictions are hard rules - very strong signal
            # Check priority from Step 2
            contradiction = next(
                (c for c in contradiction_results if c.get("label", {}).id == label.id),
                None
            )
            if contradiction and contradiction.get("priority") == "high":
                votes["invalid"] += 1  # Extra weight for high-priority contradictions

        # Decision: weighted majority wins
        total_votes = votes["valid"] + votes["invalid"]

        if votes["invalid"] > votes["valid"]:
            # Invalid wins
            label.validation_status = "INVALID"
            logger.debug(
                f"   ❌ {label.label} marked INVALID: "
                f"invalid={votes['invalid']:.1f}, valid={votes['valid']:.1f} votes"
            )
        elif votes["valid"] > votes["invalid"]:
            # Valid wins
            label.validation_status = "VALID"
            logger.debug(
                f"   ✅ {label.label} marked VALID: "
                f"valid={votes['valid']:.1f}, invalid={votes['invalid']:.1f} votes"
            )
        else:
            # Tie or no clear winner - keep as PENDING for manual review
            label.validation_status = "PENDING"
            logger.debug(
                f"   ⏸️  {label.label} kept PENDING: "
                f"tie (valid={votes['valid']:.1f}, invalid={votes['invalid']:.1f})"
            )

    return labels
```

**Example 1: "TOTAL" - All Methods Agree (Unanimous)**

**Step 1 (ChromaDB) Results:**
```python
chromadb_results = {
    "label_123": {
        "status": "INVALID",
        "confidence": 0.88,
        "reason": "similar_to_invalid_examples",
        "invalid_similarity": 0.88,
        "valid_similarity": 0.35,
    }
}
```

**Step 2 (Contradiction Detection) Results:**
```python
contradiction_results = [{
    "type": "label_word_not_value",
    "label": <label for "TOTAL">,
    "priority": "high",
    "action": "re_verify_with_cove",
}]
```

**Step 3 (CoVe) Results:**
```python
cove_results = {
    "label_123": {
        "status": "INVALID",
        "confidence": 0.92,
        "reasoning": "Word 'TOTAL' is a label word, not a currency value",
    }
}
```

**Step 4 (Conflict Resolution) - Voting:**
- **Valid votes**: 0
- **Invalid votes**:
  - 1 (ChromaDB base) + 1 (bonus for learning from mistakes) + 0.5 (high confidence) = 2.5
  - 2 (CoVe base) + 1 (high confidence) = 3
  - 3 (Contradiction base) + 1 (high priority) = 4
  - **Total: 9.5 votes**

**Decision: INVALID** (unanimous, very strong signal)

---

**Example 2: "$20.00" - Methods Disagree (Conflict Resolution Needed)**

**Step 1 (ChromaDB) Results:**
```python
chromadb_results = {
    "label_456": {
        "status": "VALID",
        "confidence": 0.82,
        "valid_similarity": 0.82,
        "invalid_similarity": 0.30,
    }
}
```

**Step 2 (Contradiction Detection) Results:**
```python
contradiction_results = []  # No contradictions found
```

**Step 3 (CoVe) Results:**
```python
cove_results = {
    "label_456": {
        "status": "INVALID",
        "confidence": 0.75,
        "reasoning": "Amount doesn't match receipt context - appears to be a line item, not grand total",
    }
}
```

**Step 4 (Conflict Resolution) - Voting:**
- **Valid votes**:
  - 1 (ChromaDB base) + 0.5 (high confidence) = 1.5
- **Invalid votes**:
  - 2 (CoVe base) = 2

**Decision: INVALID** (CoVe wins - LLM reasoning overrides similarity search)

**Why CoVe Wins:**
- CoVe has higher weight (2 vs 1) because it uses LLM reasoning
- CoVe found contextual issue that ChromaDB couldn't detect
- ChromaDB similarity might be misleading (similar words, different context)

---

**Example 3: Ambiguous Case - Tie (Needs Manual Review)**

**Step 1 (ChromaDB) Results:**
```python
chromadb_results = {
    "label_789": {
        "status": "PENDING",  # Ambiguous similarity
        "confidence": 0.65,
        "valid_similarity": 0.65,
        "invalid_similarity": 0.60,
    }
}
```

**Step 2 (Contradiction Detection) Results:**
```python
contradiction_results = []  # No contradictions
```

**Step 3 (CoVe) Results:**
```python
cove_results = {
    "label_789": {
        "status": "PENDING",  # LLM couldn't decide
        "confidence": 0.55,
        "reasoning": "Insufficient evidence to determine validity",
    }
}
```

**Step 4 (Conflict Resolution) - Voting:**
- **Valid votes**: 0
- **Invalid votes**: 0
- **Pending votes**: All methods returned PENDING

**Decision: PENDING** (tie - needs manual review)

**Why Manual Review:**
- All methods are uncertain
- No clear signal from any validation method
- Human judgment needed for edge case

---

### 5. Store Invalid Examples for Future Learning

**Save Mistakes to ChromaDB:**

```python
async def store_invalid_examples(
    invalid_labels: List[ReceiptWordLabel],
    chroma_client: VectorStoreInterface,
    dynamo_client: DynamoClient,
    word_text_lookup: Dict[Tuple[int, int], str],
) -> None:
    """Store invalid labels in ChromaDB for future learning."""

    for label in invalid_labels:
        word_text = word_text_lookup.get((label.line_id, label.word_id), "")
        if not word_text:
            continue

        # Get word embedding
        chroma_id = f"IMAGE#{label.image_id}#RECEIPT#{label.receipt_id:05d}#LINE#{label.line_id:05d}#WORD#{label.word_id:05d}"
        results = chroma_client.get_by_ids(
            collection_name="words",
            ids=[chroma_id],
            include=["embeddings"],
        )

        if not results or not results.get("embeddings"):
            continue

        embedding = results["embeddings"][0]

        # Update metadata to include invalid_labels
        chroma_client.update(
            collection_name="words",
            ids=[chroma_id],
            metadatas=[{
                "invalid_labels": label.label,  # Store what label was incorrectly applied
                "invalidated_at": datetime.utcnow().isoformat(),
                "invalidated_reason": "self_correcting_validation",
            }],
        )

        logger.info(f"   📚 Stored invalid example: '{word_text}' incorrectly labeled as {label.label}")
```

**Example: Learning Over Time**

**Timeline:**

**Week 1:**
- Receipt 1: "TOTAL" → Manually corrected to INVALID
- Stored in ChromaDB: `invalid_labels: ["GRAND_TOTAL"]`

**Week 2:**
- Receipt 2: "TOTAL" → System queries ChromaDB
  - Finds invalid example from Week 1
  - `invalid_similarity = 0.92` (very similar!)
  - **Automatically marked INVALID** (learned!)

**Week 3:**
- Receipt 3: "TOTAL" → Immediately marked INVALID
- Receipt 4: "AMOUNT" → Also marked INVALID (similar pattern learned)

**Week 4:**
- System has learned: Words like "TOTAL", "AMOUNT", "AMOUNT:" are not currency values
- **Accuracy improves from 60% to 85%** for GRAND_TOTAL labels

---

## Complete Workflow Integration

### Updated Step Function Flow

```python
async def self_correcting_validation_workflow(
    pending_labels: List[ReceiptWordLabel],
    receipt_text: str,
    receipt_lines: List[ReceiptLine],
    receipt_words: List[ReceiptWord],
    chroma_client: VectorStoreInterface,
    llm: ChatOllama,
    dynamo_client: DynamoClient,
) -> List[ReceiptWordLabel]:
    """Complete self-correcting validation workflow."""

    # Build word text lookup
    word_text_lookup = {
        (word.line_id, word.word_id): word.text
        for word in receipt_words
    }

    # Step 1: Enhanced ChromaDB (VALID + INVALID examples)
    logger.info("Step 1: Enhanced ChromaDB validation (learning from mistakes)...")
    chromadb_results = {}
    for label in pending_labels:
        result = await validate_with_invalid_examples(
            label=label,
            chroma_client=chroma_client,
            word_embedding=get_word_embedding(label, chroma_client),
        )
        chromadb_results[label.id] = result
        if result["status"] in ["VALID", "INVALID"]:
            label.validation_status = result["status"]

    # Step 2: Detect contradictions
    logger.info("Step 2: Detecting contradictions...")
    remaining_labels = [l for l in pending_labels if l.validation_status == "PENDING"]
    contradictions = detect_contradictions(
        labels=remaining_labels + [l for l in pending_labels if l.validation_status == "VALID"],
        receipt_text=receipt_text,
        word_text_lookup=word_text_lookup,
    )

    # Step 3: Iterative CoVe for affected labels
    if contradictions:
        logger.info(f"Step 3: Found {len(contradictions)} contradictions, running iterative CoVe...")
        affected_labels = get_affected_labels(contradictions, pending_labels)
        cove_results_dict = {}
        cove_verified_labels, stats = await iterative_cove_validation(
            labels=affected_labels,
            receipt_text=receipt_text,
            llm=llm,
            max_iterations=3,
        )
        for label in cove_verified_labels:
            cove_results_dict[label.id] = {
                "status": label.validation_status,
                "iterations": stats["iterations"],
            }
    else:
        logger.info("Step 3: No contradictions, running CoVe on remaining PENDING labels...")
        remaining = [l for l in pending_labels if l.validation_status == "PENDING"]
        cove_results_dict = {}
        cove_verified_labels, stats = await iterative_cove_validation(
            labels=remaining,
            receipt_text=receipt_text,
            llm=llm,
        )
        for label in cove_verified_labels:
            cove_results_dict[label.id] = {
                "status": label.validation_status,
                "iterations": stats["iterations"],
            }

    # Step 4: Resolve conflicts
    logger.info("Step 4: Resolving validation conflicts...")
    all_labels = pending_labels.copy()
    final_labels = resolve_validation_conflicts(
        labels=all_labels,
        chromadb_results=chromadb_results,
        cove_results=cove_results_dict,
        contradiction_results=contradictions,
    )

    # Step 5: Store invalid examples for future learning
    invalid_labels = [l for l in final_labels if l.validation_status == "INVALID"]
    if invalid_labels:
        logger.info(f"Step 5: Storing {len(invalid_labels)} invalid examples for future learning...")
        await store_invalid_examples(
            labels=invalid_labels,
            chroma_client=chroma_client,
            dynamo_client=dynamo_client,
            word_text_lookup=word_text_lookup,
        )

    return final_labels
```

---

## Implementation Plan

### Phase 1: Enhanced ChromaDB (Week 1)
- [ ] Add `invalid_labels` field to ChromaDB metadata schema
- [ ] Update `validate_labels_chromadb` to query INVALID examples
- [ ] Implement decision logic for VALID vs INVALID similarity comparison
- [ ] Test with "TOTAL" vs currency value examples

### Phase 2: Contradiction Detection (Week 1-2)
- [ ] Implement `detect_contradictions()` function
- [ ] Add checks for:
  - Multiple GRAND_TOTALs
  - Mathematical inconsistencies (SUBTOTAL + TAX ≠ GRAND_TOTAL)
  - "TOTAL" word labeled as GRAND_TOTAL
  - Multiple MERCHANT_NAMEs
- [ ] Integrate into validation workflow

### Phase 3: Iterative CoVe (Week 2)
- [ ] Implement `iterative_cove_validation()` function
- [ ] Add iteration tracking and convergence detection
- [ ] Update CoVe prompts to include previous iteration feedback
- [ ] Test with contradiction scenarios

### Phase 4: Conflict Resolution (Week 2-3)
- [ ] Implement `resolve_validation_conflicts()` function
- [ ] Add weighted voting system
- [ ] Test with mixed validation results
- [ ] Ensure contradictions override other signals

### Phase 5: Store Invalid Examples (Week 3)
- [ ] Implement `store_invalid_examples()` function
- [ ] Update ChromaDB metadata schema
- [ ] Add invalidation tracking (timestamp, reason)
- [ ] Test learning over time with "TOTAL" examples

### Phase 6: Integration & Testing (Week 3-4)
- [ ] Integrate all components into `validate-pending-labels-dev-sf`
- [ ] End-to-end testing with real receipts
- [ ] Measure accuracy improvement over time
- [ ] Monitor learning effectiveness

---

## Expected Outcomes

### Accuracy Improvements

**Before Self-Correcting System:**
- GRAND_TOTAL accuracy: ~60%
- "TOTAL" word false positives: ~40%
- System doesn't improve over time

**After Self-Correcting System (Week 1):**
- GRAND_TOTAL accuracy: ~70%
- "TOTAL" word false positives: ~25%
- System starts learning from mistakes

**After Self-Correcting System (Week 4):**
- GRAND_TOTAL accuracy: ~85%
- "TOTAL" word false positives: ~5%
- System has learned common patterns

**After Self-Correcting System (Month 3):**
- GRAND_TOTAL accuracy: ~92%
- "TOTAL" word false positives: ~1%
- System continuously improving

### Learning Metrics

Track over time:
- Number of invalid examples stored
- Similarity scores for invalid example matches
- Reduction in false positives/negatives
- Iteration convergence rate
- Contradiction detection rate

---

## Example: Complete "TOTAL" vs Currency Value Flow

### Receipt Example
```
Line 10: SUBTOTAL                    $20.00
Line 11: TAX                         $1.60
Line 12: TOTAL                       $21.60
```

### Initial Labels (Before Validation)
- Line 12, Word "TOTAL" → `GRAND_TOTAL` (PENDING) ❌
- Line 12, Word "$21.60" → Not labeled

### Step 1: Enhanced ChromaDB
- Query VALID examples: Similarity to "$24.01", "$15.99" = 0.35 (low)
- Query INVALID examples: Similarity to "TOTAL" (from past mistakes) = 0.88 (high!)
- **Result**: INVALID (learned from past mistakes)

### Step 2: Contradiction Detection
- Detects: "TOTAL" is a label word, not a currency value
- **Contradiction Found**: `label_word_not_value`

### Step 3: Iterative CoVe
- **Iteration 1**:
  - Question: "Is 'TOTAL' a currency amount?"
  - Answer: "No, it's just a label"
  - Result: Mark "TOTAL" as INVALID
- **Iteration 2**:
  - Question: "What is the actual grand total amount on this receipt?"
  - Answer: "$21.60 is the grand total"
  - Result: Label "$21.60" as GRAND_TOTAL
- **Converged**: No more errors found

### Step 4: Conflict Resolution
- ChromaDB: INVALID (7 votes)
- CoVe: INVALID (2 votes)
- Contradiction: INVALID (3 votes)
- **Final Decision**: "TOTAL" → INVALID, "$21.60" → VALID GRAND_TOTAL

### Step 5: Store Invalid Example
- Store "TOTAL" as invalid example in ChromaDB
- Next receipt with "TOTAL" will immediately be marked INVALID

### Final Result
- Line 12, Word "TOTAL" → `INVALID` ✅
- Line 12, Word "$21.60" → `GRAND_TOTAL` (VALID) ✅
- System learned: "TOTAL" is not a currency value

---

## Benefits Summary

1. **Learns from Mistakes**: Stores and queries INVALID examples
2. **Self-Corrects**: Iterative CoVe refines labels over multiple passes
3. **Detects Contradictions**: Finds logical inconsistencies automatically
4. **Cross-Validates**: Combines multiple methods intelligently
5. **Improves Over Time**: Accuracy increases as system learns

This system will continuously improve, reducing false positives and false negatives as it processes more receipts and learns from corrections.

