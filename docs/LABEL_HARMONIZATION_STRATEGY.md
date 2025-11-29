# Receipt Word Label Harmonization Strategy

## Overview

This document outlines the strategy for finding and harmonizing receipt word labels, similar to how we handle receipt metadata. The approach uses **two separate agents** to balance speed at upload time with comprehensive cleanup later.

## Key Principles

1. **Two-Agent Architecture**: Fast upload-time labeling + comprehensive batch harmonization
2. **Efficient Loading**: Use DynamoDB GSIs to load subsets, not all labels
3. **ChromaDB Integration**: Leverage existing word embeddings for similarity search
4. **Check Before Update**: Always check existing labels before creating/updating
5. **Automatic ChromaDB Updates**: ChromaDB updates automatically via stream, no manual updates needed

---

## Agent 1: Label Finder (Upload-Time)

**Purpose**: Find missing labels for a single receipt quickly at upload time.

**Similar to**: `ReceiptMetadataFinder`

### Workflow

1. **Input**: Single receipt (image_id, receipt_id)
2. **Load existing labels**: Check what labels already exist for this receipt
3. **Identify missing labels**: Determine which CORE_LABELS are missing
4. **Use ChromaDB**: Find similar words with VALID labels from other receipts
5. **Use LLM agent**: Reason about what labels should be assigned
6. **Create PENDING labels**: Save new labels with `validation_status="PENDING"`

### Key Features

- **Fast**: Works on single receipt, no batch processing
- **Context-aware**: Uses ChromaDB to find similar words
- **Respects existing**: Checks DynamoDB before creating labels
- **Agent-based**: Uses LLM to reason about label assignments

### Tools Available

- `get_my_labels`: Get existing labels for this receipt
- `get_my_words`: Get words for this receipt
- `get_my_lines`: Get lines for this receipt
- `search_words`: Find similar words with labels in ChromaDB
- `get_merchant_consensus`: Get canonical labels for merchant (if available)
- `submit_labels`: Create new labels

### Output

- Creates `ReceiptWordLabel` entities with `validation_status="PENDING"`
- ChromaDB automatically updated via DynamoDB stream

---

## Agent 2: Label Harmonizer (Batch Processing)

**Purpose**: Harmonize labels across receipts, ensuring consistency within merchants.

**Similar to**: `MerchantHarmonizerV2`

### Workflow

1. **Load labels efficiently**: Use GSIs to load subsets
2. **Group by merchant + label type**: Organize labels for harmonization
3. **Find similar words**: Use ChromaDB to find semantically similar words
4. **Compute consensus**: Find most common VALID label for similar words
5. **Identify outliers**: Find labels that differ from consensus
6. **Update DynamoDB**: Fix outliers to match consensus

### Efficient Loading Strategy

**Option A: By Label Type (Recommended)**
```python
# Process one CORE_LABEL at a time
for label_type in CORE_LABELS:
    labels = dynamo.get_receipt_word_labels_by_label(label_type)  # GSI1
    # Group by merchant, find consensus, update outliers
```

**Option B: By Validation Status**
```python
# Process PENDING labels only
pending_labels = dynamo.list_receipt_word_labels_with_status(ValidationStatus.PENDING)  # GSI3
# Group by merchant + label type, find consensus
```

**Option C: Hybrid (Best Performance)**
```python
# Process PENDING labels, grouped by label type
for label_type in CORE_LABELS:
    # Get PENDING labels for this type
    all_labels = dynamo.get_receipt_word_labels_by_label(label_type)  # GSI1
    pending_labels = [l for l in all_labels if l.validation_status == "PENDING"]

    # Group by merchant
    labels_by_merchant = group_by_merchant(pending_labels)

    # Harmonize each merchant group
    for merchant, labels in labels_by_merchant.items():
        harmonize_labels(merchant, labels, label_type)
```

### Grouping Strategy

**Primary Grouping**: `merchant_name` + `label` type

**How to get merchant_name**:
1. Extract `(image_id, receipt_id)` pairs from labels
2. Batch fetch `ReceiptMetadata` for all pairs
3. Group labels by `merchant_name` from metadata

**Secondary Grouping**: ChromaDB similarity clusters
- Within each merchant + label type group
- Use ChromaDB to find similar words
- Build consensus from similar words

### Consensus Calculation

For each group (merchant + label type + similarity cluster):

1. **Filter by validation_status**:
   - Prefer `VALID` labels over `PENDING`
   - Ignore `INVALID` labels

2. **Count label occurrences**:
   - Most common label = consensus
   - Weight by validation_status (VALID > PENDING)

3. **Confidence scoring**:
   - Group size (more similar words = higher confidence)
   - Validation status distribution (more VALID = higher confidence)
   - Similarity scores (higher similarity = higher confidence)

### Harmonization Process

```python
def harmonize_labels(merchant: str, labels: List[ReceiptWordLabel], label_type: str):
    # 1. Get word embeddings from ChromaDB
    chroma_ids = [build_chromadb_id(l) for l in labels]
    words_with_embeddings = chroma_client.get_by_ids("words", chroma_ids)

    # 2. Cluster similar words (using embeddings)
    clusters = cluster_similar_words(words_with_embeddings, threshold=0.85)

    # 3. For each cluster, find consensus
    for cluster in clusters:
        cluster_labels = [l for l in labels if l in cluster]
        consensus = compute_consensus(cluster_labels)

        # 4. Update outliers
        for label in cluster_labels:
            if label.label != consensus:
                label.label = consensus
                label.validation_status = "VALID"  # If high confidence
                dynamo.update_receipt_word_label(label)
```

### Key Features

- **Efficient**: Uses GSIs to load subsets, not all labels
- **Merchant-aware**: Groups by merchant for consistency
- **Similarity-based**: Uses ChromaDB to find similar words
- **Consensus-driven**: Updates outliers to match consensus
- **Respects validation**: Prefers VALID labels over PENDING
- **Pattern discovery**: Learns patterns from VALID/INVALID labels

### Pattern Discovery

The harmonizer can discover common patterns from VALID and INVALID labels, which can then be used for validation:

#### 1. Text Patterns (for structured labels)

**For DATE, TIME, PHONE_NUMBER labels:**

```python
def discover_text_patterns(valid_labels: List[ReceiptWordLabel],
                          invalid_labels: List[ReceiptWordLabel],
                          words: Dict[Tuple[int, int], ReceiptWord]) -> Dict[str, Pattern]:
    """
    Discover text patterns that distinguish VALID from INVALID labels.

    Returns patterns like:
    - VALID DATE patterns: ["MM/DD/YYYY", "YYYY-MM-DD", ...]
    - INVALID DATE patterns: ["random text", "prices", ...]
    """
    valid_texts = [words[(l.line_id, l.word_id)].text for l in valid_labels]
    invalid_texts = [words[(l.line_id, l.word_id)].text for l in invalid_labels]

    # Extract common patterns from valid texts
    valid_patterns = extract_common_patterns(valid_texts)
    invalid_patterns = extract_common_patterns(invalid_texts)

    return {
        "valid_patterns": valid_patterns,
        "invalid_patterns": invalid_patterns,
        "confidence": calculate_pattern_confidence(valid_patterns, invalid_patterns)
    }
```

**Usage**: These patterns can be used to validate new PENDING labels:
- If text matches a VALID pattern → mark as VALID
- If text matches an INVALID pattern → mark as INVALID
- If text matches neither → keep as PENDING for further review

#### 2. Position Patterns (spatial relationships)

**For all label types:**

```python
def discover_position_patterns(valid_labels: List[ReceiptWordLabel],
                              invalid_labels: List[ReceiptWordLabel],
                              words: Dict[Tuple[int, int], ReceiptWord]) -> Dict[str, PositionPattern]:
    """
    Discover spatial patterns that distinguish VALID from INVALID labels.

    Returns patterns like:
    - GRAND_TOTAL: Usually at bottom (y > 0.8), right-aligned (x > 0.7)
    - MERCHANT_NAME: Usually at top (y < 0.2), centered (0.3 < x < 0.7)
    - DATE: Usually near top (y < 0.3), varies by merchant
    """
    valid_positions = [(words[(l.line_id, l.word_id)].calculate_centroid(), l.label)
                      for l in valid_labels]
    invalid_positions = [(words[(l.line_id, l.word_id)].calculate_centroid(), l.label)
                        for l in invalid_labels]

    # Cluster positions by label type
    position_clusters = cluster_by_label_type(valid_positions)

    # Extract common position ranges
    position_patterns = {}
    for label_type, positions in position_clusters.items():
        x_range = (min(p[0][0] for p in positions), max(p[0][0] for p in positions))
        y_range = (min(p[0][1] for p in positions), max(p[0][1] for p in positions))

        position_patterns[label_type] = {
            "x_range": x_range,
            "y_range": y_range,
            "typical_x": median([p[0][0] for p in positions]),
            "typical_y": median([p[0][1] for p in positions]),
            "confidence": len(positions) / (len(valid_positions) + len(invalid_positions))
        }

    return position_patterns
```

**Usage**: Validate new labels by checking if they're in expected positions:
- If label is in expected position range → higher confidence
- If label is in unexpected position → flag for review

#### 3. Context Patterns (neighboring labels)

**For all label types:**

```python
def discover_context_patterns(valid_labels: List[ReceiptWordLabel],
                             invalid_labels: List[ReceiptWordLabel],
                             all_labels: List[ReceiptWordLabel]) -> Dict[str, ContextPattern]:
    """
    Discover context patterns (what labels appear near each other).

    Returns patterns like:
    - GRAND_TOTAL usually appears after SUBTOTAL and TAX
    - DATE and TIME usually appear together
    - PRODUCT_NAME usually appears with QUANTITY and UNIT_PRICE
    """
    # Build label sequences for each receipt
    receipt_label_sequences = {}
    for label in all_labels:
        key = (label.image_id, label.receipt_id)
        if key not in receipt_label_sequences:
            receipt_label_sequences[key] = []
        receipt_label_sequences[key].append((label.line_id, label.word_id, label.label))

    # Find common sequences
    context_patterns = {}
    for label_type in CORE_LABELS:
        # Find what labels appear before/after this label type
        before_labels = []
        after_labels = []

        for sequence in receipt_label_sequences.values():
            for i, (line_id, word_id, lbl) in enumerate(sequence):
                if lbl == label_type:
                    if i > 0:
                        before_labels.append(sequence[i-1][2])
                    if i < len(sequence) - 1:
                        after_labels.append(sequence[i+1][2])

        context_patterns[label_type] = {
            "common_before": most_common(before_labels),
            "common_after": most_common(after_labels),
            "confidence": len(before_labels) + len(after_labels)
        }

    return context_patterns
```

**Usage**: Validate new labels by checking context:
- If label appears in expected context → higher confidence
- If label appears in unexpected context → flag for review

#### 4. Merchant-Specific Patterns

**For all label types:**

```python
def discover_merchant_patterns(valid_labels: List[ReceiptWordLabel],
                               merchant_name: str) -> Dict[str, MerchantPattern]:
    """
    Discover merchant-specific patterns.

    Returns patterns like:
    - "Vons" always formats dates as "MM/DD/YYYY"
    - "Trader Joe's" always puts GRAND_TOTAL at bottom-right
    - "CVS" always includes LOYALTY_ID
    """
    # Group labels by merchant
    merchant_labels = [l for l in valid_labels if get_merchant(l) == merchant_name]

    # Extract merchant-specific patterns
    patterns = {}
    for label_type in CORE_LABELS:
        type_labels = [l for l in merchant_labels if l.label == label_type]
        if len(type_labels) >= 3:  # Need at least 3 examples
            patterns[label_type] = {
                "text_patterns": extract_text_patterns(type_labels),
                "position_patterns": extract_position_patterns(type_labels),
                "context_patterns": extract_context_patterns(type_labels),
                "confidence": len(type_labels)
            }

    return patterns
```

**Usage**: Use merchant-specific patterns for higher accuracy:
- If merchant has strong patterns → use them for validation
- If merchant has weak patterns → fall back to general patterns

### Pattern Storage

**Option 1: Store in DynamoDB** (Recommended)
```python
@dataclass
class LabelPattern:
    """Stores discovered patterns for a label type."""
    label_type: str
    merchant_name: Optional[str]  # None for general patterns
    pattern_type: str  # "text", "position", "context"
    pattern_data: Dict[str, Any]
    confidence: float
    sample_size: int
    timestamp: datetime
```

**Option 2: Store in ChromaDB metadata**
- Add pattern metadata to word embeddings
- Query patterns when validating new labels

**Option 3: In-memory cache**
- Load patterns at startup
- Update as new labels are validated
- Fast but not persistent

### Using Patterns for Validation

The harmonizer can use discovered patterns to validate new labels:

```python
def validate_with_patterns(label: ReceiptWordLabel,
                          word: ReceiptWord,
                          patterns: Dict[str, Pattern]) -> ValidationResult:
    """
    Validate a label using discovered patterns.

    Returns:
        - VALID if matches valid patterns
        - INVALID if matches invalid patterns
        - PENDING if no clear pattern match
    """
    label_type = label.label
    text = word.text
    position = word.calculate_centroid()

    # Check text patterns
    if label_type in ["DATE", "TIME", "PHONE_NUMBER"]:
        text_pattern = patterns.get(f"{label_type}_text")
        if text_pattern:
            if matches_valid_pattern(text, text_pattern["valid_patterns"]):
                return ValidationResult.VALID
            if matches_invalid_pattern(text, text_pattern["invalid_patterns"]):
                return ValidationResult.INVALID

    # Check position patterns
    position_pattern = patterns.get(f"{label_type}_position")
    if position_pattern:
        if is_in_expected_position(position, position_pattern):
            confidence += 0.2
        else:
            confidence -= 0.2

    # Check context patterns
    context_pattern = patterns.get(f"{label_type}_context")
    if context_pattern:
        if has_expected_context(label, context_pattern):
            confidence += 0.1

    # Check merchant-specific patterns
    merchant_pattern = patterns.get(f"{label_type}_{merchant_name}")
    if merchant_pattern:
        if matches_merchant_pattern(label, word, merchant_pattern):
            confidence += 0.3

    # Decision
    if confidence >= 0.8:
        return ValidationResult.VALID
    elif confidence <= 0.3:
        return ValidationResult.INVALID
    else:
        return ValidationResult.PENDING
```

### Pattern Discovery Workflow

```python
async def discover_patterns_for_label_type(label_type: str) -> Dict[str, Pattern]:
    """
    Discover patterns for a specific label type.

    1. Load all VALID labels for this type (GSI1)
    2. Load all INVALID labels for this type (GSI1 + filter)
    3. Get words for these labels
    4. Discover patterns:
       - Text patterns (for structured labels)
       - Position patterns (spatial)
       - Context patterns (neighboring labels)
       - Merchant-specific patterns
    5. Store patterns for future use
    """
    # Load labels
    valid_labels = []
    invalid_labels = []

    all_labels, _ = dynamo.get_receipt_word_labels_by_label(label_type)
    for label in all_labels:
        if label.validation_status == "VALID":
            valid_labels.append(label)
        elif label.validation_status == "INVALID":
            invalid_labels.append(label)

    # Get words
    word_keys = [(l.image_id, l.receipt_id, l.line_id, l.word_id)
                 for l in valid_labels + invalid_labels]
    words = batch_get_words(word_keys)

    # Discover patterns
    patterns = {
        "text": discover_text_patterns(valid_labels, invalid_labels, words),
        "position": discover_position_patterns(valid_labels, invalid_labels, words),
        "context": discover_context_patterns(valid_labels, invalid_labels, all_labels),
        "merchant": discover_merchant_patterns(valid_labels)
    }

    # Store patterns
    store_patterns(label_type, patterns)

    return patterns
```

### Output

- Updates `ReceiptWordLabel` entities in DynamoDB
- Updates `validation_status` (PENDING → VALID if high confidence)
- Discovers and stores patterns for future validation
- ChromaDB automatically updated via DynamoDB stream

---

## DynamoDB Access Patterns

### Existing GSIs

**GSI1**: Query by `label` type
- `GSI1PK`: `LABEL#{LABEL_TYPE}`
- Use: `get_receipt_word_labels_by_label(label_type)`
- **Best for**: Processing one label type at a time

**GSI3**: Query by `validation_status`
- `GSI3PK`: `VALIDATION_STATUS#{STATUS}`
- Use: `list_receipt_word_labels_with_status(status)`
- **Best for**: Processing PENDING labels only

### Efficient Joins

**Getting merchant_name for labels**:
```python
# Extract unique (image_id, receipt_id) pairs
receipt_keys = set((l.image_id, l.receipt_id) for l in labels)

# Batch fetch metadata (can use batch_get_item or individual calls)
metadata_by_key = {}
for image_id, receipt_id in receipt_keys:
    metadata = dynamo.get_receipt_metadata(image_id, receipt_id)
    metadata_by_key[(image_id, receipt_id)] = metadata

# Group labels by merchant
labels_by_merchant = defaultdict(list)
for label in labels:
    key = (label.image_id, label.receipt_id)
    metadata = metadata_by_key.get(key)
    merchant = metadata.merchant_name if metadata else None
    labels_by_merchant[merchant].append(label)
```

### Potential New GSI (Future Consideration)

**GSI4**: Query by `merchant_name` + `label` type
- Would require denormalizing `merchant_name` into labels
- **Trade-off**: Faster queries vs. data duplication
- **Recommendation**: Start without it, add if needed

---

## ChromaDB Integration

### Current State

- Words are embedded and stored in ChromaDB `words` collection
- Metadata includes: `merchant_name`, `validated_labels`, `invalid_labels`
- Updates happen automatically via DynamoDB stream

### How Harmonizer Uses ChromaDB

1. **Find similar words**:
   ```python
   # Get embedding for target word
   chroma_id = build_chromadb_id(label)
   word_embedding = chroma_client.get_by_ids("words", [chroma_id])["embeddings"][0]

   # Query for similar words with same label type
   similar_words = chroma_client.query(
       collection_name="words",
       query_embeddings=[word_embedding],
       n_results=20,
       where={
           "$and": [
               {"merchant_name": {"$eq": merchant_name}},
               {"validated_labels": {"$contains": label_type}}
           ]
       }
   )
   ```

2. **Build consensus from similar words**:
   - Extract labels from similar words' metadata
   - Count occurrences
   - Most common = consensus

3. **No manual ChromaDB updates needed**:
   - DynamoDB stream automatically updates ChromaDB
   - Just update DynamoDB, ChromaDB follows

---

## Implementation Plan

### Phase 1: Label Finder Agent

1. Create `receipt_agent/tools/label_finder.py`
2. Create `receipt_agent/graph/label_finder_workflow.py`
3. Add tools:
   - `get_my_labels`: Get existing labels for receipt
   - `search_words`: Find similar words with labels
   - `submit_labels`: Create new labels
4. Test on single receipts

### Phase 2: Label Harmonizer

1. Create `receipt_agent/tools/label_harmonizer.py`
2. Implement efficient loading using GSIs
3. Implement merchant grouping
4. Implement ChromaDB similarity clustering
5. Implement consensus calculation
6. **Implement pattern discovery**:
   - Text patterns (DATE, TIME, PHONE_NUMBER)
   - Position patterns (spatial relationships)
   - Context patterns (neighboring labels)
   - Merchant-specific patterns
7. **Integrate pattern-based validation**:
   - Use patterns to validate new labels
   - Update patterns as new labels are validated
8. Test on batches

### Phase 3: Pattern-Based Validation

1. **Create pattern storage**:
   - Design pattern storage (DynamoDB table or in-memory cache)
   - Implement pattern serialization/deserialization
2. **Integrate patterns into validation pipeline**:
   - Use patterns in `validate_pending_labels` lambda
   - Use patterns in Label Finder agent
   - Use patterns in Label Harmonizer
3. **Pattern maintenance**:
   - Periodic pattern re-discovery
   - Pattern versioning
   - Pattern confidence tracking

### Phase 4: Integration

1. Integrate Label Finder into upload pipeline
2. Set up batch job for Label Harmonizer
3. Set up periodic pattern discovery job
4. Monitor and tune performance

---

## Key Design Decisions

### 1. Two Agents vs. One

**Decision**: Two separate agents
- **Rationale**: Upload-time speed vs. batch processing efficiency
- **Label Finder**: Fast, single receipt, creates PENDING labels
- **Label Harmonizer**: Comprehensive, batch processing, updates to VALID

### 2. Loading Strategy

**Decision**: Load by label type (GSI1) or by validation_status (GSI3)
- **Rationale**: Avoid loading all labels (too slow)
- **Process one label type at a time** or **process PENDING labels only**
- **Hybrid approach**: Process PENDING labels, grouped by label type

### 3. Grouping Key

**Decision**: `merchant_name` + `label` type
- **Rationale**: Same merchant should have consistent labels
- **Similar to**: Metadata harmonizer uses `place_id` (same merchant)
- **Get merchant_name**: Join with ReceiptMetadata

### 4. Consensus Calculation

**Decision**: Weight by validation_status and group size
- **Prefer VALID labels** over PENDING
- **Larger groups** = higher confidence
- **Higher similarity** = higher confidence

### 5. ChromaDB Updates

**Decision**: No manual updates needed
- **Rationale**: DynamoDB stream automatically updates ChromaDB
- **Just update DynamoDB**, ChromaDB follows
- **Simpler code**, no dual-write complexity

---

## Performance Considerations

### Label Finder (Single Receipt)

- **Fast**: Single receipt, no batch processing
- **ChromaDB queries**: ~5-10 per receipt (one per missing label type)
- **DynamoDB reads**: ~3-5 per receipt (metadata, words, existing labels)
- **Expected time**: < 5 seconds per receipt

### Label Harmonizer (Batch)

- **Efficient loading**: Use GSIs, process one label type at a time
- **ChromaDB queries**: Batch queries for similar words
- **DynamoDB reads**: Batch fetch metadata for receipt keys
- **DynamoDB writes**: Batch update labels
- **Expected throughput**: ~100-500 receipts per minute

### Optimization Opportunities

1. **Cache merchant_name**: Don't re-fetch metadata for same receipts
2. **Batch ChromaDB queries**: Query multiple words at once
3. **Parallel processing**: Process multiple label types in parallel
4. **Incremental processing**: Only process new/changed labels

---

## Example Usage

### Label Finder (Upload Time)

```python
from receipt_agent.tools.label_finder import LabelFinder

finder = LabelFinder(dynamo_client, chroma_client, embed_fn)

# Find missing labels for a receipt
result = await finder.find_labels_for_receipt(image_id, receipt_id)
# Creates PENDING labels in DynamoDB
```

### Label Harmonizer (Batch)

```python
from receipt_agent.tools.label_harmonizer import LabelHarmonizer

harmonizer = LabelHarmonizer(dynamo_client, chroma_client, embed_fn)

# Harmonize all PENDING labels for GRAND_TOTAL
report = await harmonizer.harmonize_label_type("GRAND_TOTAL")
harmonizer.print_summary(report)

# Apply fixes
result = await harmonizer.apply_fixes(dry_run=False)
```

---

## Questions & Considerations

1. **GSI for merchant_name?**
   - Would require denormalizing merchant_name into labels
   - Start without it, add if performance requires

2. **Conflict resolution?**
   - When similar words have different labels, how to choose?
   - Use consensus (most common VALID label)

3. **Validation status updates?**
   - Should harmonizer update validation_status?
   - Yes: PENDING → VALID if high confidence

4. **Incremental processing?**
   - Only process new/changed labels?
   - Track last processed timestamp?

5. **Cross-merchant patterns?**
   - Some labels (DATE, TIME) might be consistent across merchants
   - Start with merchant-level, expand if needed

6. **Pattern storage?**
   - DynamoDB table vs. in-memory cache
   - Pattern versioning and updates
   - Pattern confidence thresholds

7. **Pattern discovery frequency?**
   - Real-time (as labels are validated)
   - Periodic (daily/weekly batch job)
   - On-demand (when patterns are stale)

---

## How Patterns Help with Validation

### Current Validation Flow

1. **Rule-based validation** (fast, cheap):
   - DATE, TIME, PHONE_NUMBER use regex patterns
   - Catches obvious mislabelings

2. **ChromaDB similarity** (medium speed, cheap):
   - Find similar words with VALID labels
   - Compare similarity scores

3. **CoVe validation** (slow, expensive):
   - LLM-based verification
   - Used for uncertain cases

### Enhanced Validation Flow (with Patterns)

1. **Pattern-based validation** (fast, cheap, learned):
   - Use discovered patterns to validate
   - Text patterns for structured labels
   - Position patterns for spatial validation
   - Context patterns for relationship validation
   - Merchant-specific patterns for accuracy

2. **Rule-based validation** (fast, cheap):
   - Keep existing regex patterns
   - Use as fallback if patterns unavailable

3. **ChromaDB similarity** (medium speed, cheap):
   - Find similar words with VALID labels
   - Compare similarity scores
   - **Enhanced**: Use patterns to filter/weight results

4. **CoVe validation** (slow, expensive):
   - LLM-based verification
   - Used only for uncertain cases
   - **Reduced**: Patterns catch more cases, fewer need CoVe

### Benefits of Pattern-Based Validation

1. **Higher accuracy**: Patterns learned from actual data, not just rules
2. **Merchant-specific**: Adapts to each merchant's format
3. **Context-aware**: Considers spatial and sequential relationships
4. **Self-improving**: Patterns improve as more labels are validated
5. **Faster**: Pattern matching is faster than LLM validation
6. **Cheaper**: Reduces need for expensive CoVe validation

### Example: Pattern-Based Validation in Action

```python
# New PENDING label: DATE on word "12/25/2023"
label = ReceiptWordLabel(..., label="DATE", validation_status="PENDING")
word = ReceiptWord(..., text="12/25/2023")

# Check patterns
patterns = load_patterns("DATE")

# Text pattern check
if matches_pattern(word.text, patterns["text"]["valid_patterns"]):
    # "12/25/2023" matches "MM/DD/YYYY" pattern
    confidence += 0.4

# Position pattern check
position = word.calculate_centroid()
if is_in_range(position, patterns["position"]["y_range"]):
    # Word is in expected position (top of receipt)
    confidence += 0.3

# Context pattern check
if has_label_before(label, "MERCHANT_NAME") and has_label_after(label, "TIME"):
    # DATE appears after MERCHANT_NAME and before TIME (expected)
    confidence += 0.2

# Merchant-specific pattern check
merchant_patterns = load_merchant_patterns("Vons", "DATE")
if matches_merchant_pattern(word, merchant_patterns):
    # Matches Vons-specific DATE format
    confidence += 0.1

# Decision
if confidence >= 0.8:
    label.validation_status = "VALID"  # High confidence, mark as VALID
elif confidence <= 0.3:
    label.validation_status = "INVALID"  # Low confidence, mark as INVALID
else:
    label.validation_status = "PENDING"  # Medium confidence, needs review
```

---

## Next Steps

1. **Review and approve strategy**
2. **Implement Label Finder agent** (Phase 1)
3. **Implement Label Harmonizer** (Phase 2)
4. **Test and tune performance**
5. **Integrate into pipelines**

