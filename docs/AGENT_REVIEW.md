# Agent Review: Place ID Finder & Harmonizer

## Overview

This branch introduces three complementary agents for managing receipt metadata:

1. **Receipt Metadata Finder Agent** ⭐ - Finds ALL missing metadata for receipts (NEW & IMPROVED)
2. **Harmonizer V2** - Rule-based consensus for metadata consistency (legacy)
3. **Harmonizer V3** ⭐ - Agent-based harmonization with intelligent reasoning (NEW)

These agents work together to improve data quality: the finder populates missing place_ids, and the harmonizer ensures consistency once place_ids exist.

---

## 1. Receipt Metadata Finder Agent ⭐ (NEW & IMPROVED)

### Purpose
Finds **ALL missing metadata** for receipts, not just place_ids:
- `place_id` (Google Place ID)
- `merchant_name` (business name)
- `address` (formatted address)
- `phone_number` (phone number)

This is the first step in the data quality pipeline.

### Architecture

**Location**:
- `receipt_agent/receipt_agent/tools/receipt_metadata_finder.py` (NEW)
- `receipt_agent/receipt_agent/graph/receipt_metadata_finder_workflow.py` (NEW)

**Agent-Based Approach** (recommended):
- Uses LLM agent with LangGraph
- Examines receipt content (lines, words, labels)
- Extracts metadata from receipt itself (PRIMARY source)
- Searches ChromaDB for similar receipts
- Uses Google Places API for missing fields
- Reasons about the best values for each field
- Can fill in partial metadata even if some fields can't be found

### Key Features

✅ **Comprehensive Metadata Finding**
- Finds ALL missing metadata, not just place_id
- Extracts from receipt content (PRIMARY source)
- Uses Google Places for validation and missing fields
- Can fill in partial metadata intelligently

✅ **Intelligent Extraction**
- Extracts metadata from receipt labels (MERCHANT_NAME, ADDRESS, PHONE)
- Falls back to line text if labels aren't available
- Uses Google Places to validate and fill gaps
- Tracks source for each field (receipt_content, google_places, similar_receipts)

✅ **Agent-Based Reasoning**
- Uses LangGraph with ReAct pattern
- Accesses receipt context (lines, words, metadata)
- Searches ChromaDB for similar receipts
- Handles edge cases (e.g., address returned as merchant name)
- Reasons about what's missing and how to find it

✅ **Robust Error Handling**
- Retry logic for server errors (3 attempts with exponential backoff)
- Can work with partial data
- Marks receipts needing review (no searchable data)

✅ **Batch Processing**
- Loads all receipts with missing metadata
- Progress tracking
- Dry-run mode for safety

### Strengths

1. **Comprehensive Approach**
   - Finds ALL missing metadata, not just place_id
   - Extracts from receipt content first (most reliable)
   - Uses Google Places for validation and missing fields
   - Can work with partial data

2. **Intelligent Source Priority**
   - Receipt content (labels, lines) - PRIMARY source
   - Google Places - For validation and missing fields
   - Similar receipts - For verification only
   - Clear source tracking for each field

3. **Comprehensive Prompt Engineering**
   - Clear instructions about extracting from receipt content first
   - Handles edge case: "NEVER accept an address as a merchant name"
   - Explicit guidance on when to use `find_businesses_at_address`
   - Instructions to fill in as many fields as possible

4. **Well-Structured Results**
   - `MetadataMatch` dataclass with all fields
   - `FinderResult` aggregates statistics per field
   - Tracks which fields were found and their sources
   - `UpdateResult` tracks application of fixes

### Improvements Over Place ID Finder

1. **Finds ALL Metadata** ✅
   - Not just place_id, but merchant_name, address, phone too
   - Can fill in partial metadata intelligently

2. **Extracts from Receipt Content** ✅
   - Uses receipt labels (MERCHANT_NAME, ADDRESS, PHONE) as PRIMARY source
   - Falls back to line text if labels aren't available
   - More reliable than just searching Google Places

3. **Source Tracking** ✅
   - Tracks where each field came from
   - Helps with debugging and quality assessment

4. **Partial Fills** ✅
   - Can fill in some fields even if others can't be found
   - Better than all-or-nothing approach

### Potential Future Improvements

1. **Field-Specific Confidence**
   - Currently has overall confidence
   - Could have per-field confidence thresholds

2. **Better Extraction Logic**
   - Could use OCR confidence scores
   - Could validate extracted data against patterns

3. **Incremental Updates**
   - Currently processes all receipts
   - Could support incremental updates (only changed receipts)

### Code Quality

✅ **Good**
- Clear docstrings
- Type hints throughout
- Dataclasses for structured data
- Comprehensive logging

⚠️ **Minor Issues**
- Some long methods (e.g., `apply_fixes()` is 270 lines)
- Could extract helper methods for readability

---

## 2. Harmonizer V2 (Rule-Based - Legacy)

### Purpose
Ensures metadata consistency across receipts sharing the same Google Place ID using rule-based consensus.

### Architecture

**Location**: `receipt_agent/receipt_agent/tools/harmonizer_v2.py`

**Approach**:
1. **Group by place_id**: Load all receipts and group by place_id
2. **Compute consensus**: For each group, find most common value for each field
3. **Validate with Google**: Optionally confirm consensus against Google Places API
4. **Identify outliers**: Flag receipts that differ from consensus
5. **Apply fixes**: Update canonical fields in DynamoDB

### Limitations (Why V3 is Better)

❌ **Majority Voting Can Fail**
- If majority has OCR errors, wrong data wins
- No reasoning about why values differ

❌ **No Edge Case Handling**
- Struggles with address-like merchant names
- Conflicts between consensus and Google Places are not resolved intelligently

❌ **Limited Confidence**
- Confidence based only on group size
- Doesn't consider quality of data

---

## 3. Harmonizer V3 (Agent-Based - NEW ⭐)

### Purpose
Ensures metadata consistency using an LLM agent that reasons about receipts, validates with Google Places, and handles edge cases intelligently.

### Architecture

**Location**:
- `receipt_agent/receipt_agent/tools/harmonizer_v3.py` - Main class
- `receipt_agent/receipt_agent/graph/harmonizer_workflow.py` - Agent workflow

**How It Works**:
1. **Load & Group**: Same as V2 - group receipts by place_id
2. **Identify Inconsistent Groups**: Flag groups where metadata differs
3. **Run Agent per Group**: LLM agent reasons about correct values
4. **Apply Fixes**: Update receipts to match canonical values

### Key Features

✅ **Agent-Based Reasoning**
- Uses LLM to reason about each inconsistent group
- Can understand context (OCR errors, formatting differences)
- Makes intelligent decisions about edge cases

✅ **Google Places as Source of Truth**
- Agent validates against Google Places first
- Handles address-like merchant names intelligently
- Uses `find_businesses_at_address` when Google returns an address

✅ **Confidence Scoring**
- Agent provides confidence scores with reasoning
- High confidence (≥80%): Clear consensus with Google validation
- Medium (50-80%): Some disagreement but clear best choice
- Low (<50%): Significant conflicts, may need review

✅ **Detailed Reasoning**
- Agent explains why it chose specific values
- Tracks source of decision (Google Places vs consensus)
- Provides audit trail for each harmonization

### Agent Tools

The harmonizer agent has access to:

1. **Group Analysis Tools**
   - `get_group_summary`: See all receipts and their metadata
   - `get_receipt_content`: View actual OCR content (lines, words)
   - `get_field_variations`: Detailed analysis of field differences

2. **Google Places Tools**
   - `verify_place_id`: Get official data from Google
   - `find_businesses_at_address`: Resolve address-like names

3. **Decision Tool**
   - `submit_harmonization`: Submit canonical values with reasoning

### System Prompt Highlights

The agent is instructed to:
- Always verify with Google Places first
- Never accept an address as a merchant name
- Prefer properly formatted values (Title Case over ALL CAPS)
- Provide confidence scores based on evidence quality

### Usage Example

```python
from receipt_agent.tools.harmonizer_v3 import MerchantHarmonizerV3

harmonizer = MerchantHarmonizerV3(dynamo_client, places_client)

# Analyze all receipts (processes inconsistent groups with agent)
report = await harmonizer.harmonize_all()
harmonizer.print_summary(report)

# Apply fixes (dry run first)
result = await harmonizer.apply_fixes(dry_run=True)
print(f"Would update {result.total_updated} receipts")

# Actually apply fixes
result = await harmonizer.apply_fixes(dry_run=False)
print(f"Updated {result.total_updated} receipts")
```

### Example Output

```
HARMONIZER V3 REPORT (Agent-Based)
======================================================================
Total receipts: 571
  With place_id: 506
  Without place_id: 65 (cannot harmonize)

Place ID groups: 155
  Consistent: 120 (no action needed)
  Inconsistent: 35 (processed by agent)

Agent Results (35 groups):
  ✅ High confidence (≥80%): 28
  ⚠️  Medium confidence (50-80%): 5
  ❌ Low confidence (<50%): 2

Receipts needing updates: 142
```

### Strengths

1. **Intelligent Edge Case Handling**
   - Recognizes OCR errors
   - Handles address-like merchant names
   - Resolves conflicts intelligently

2. **Transparent Reasoning**
   - Every decision has reasoning
   - Confidence scores help prioritize reviews
   - Clear audit trail

3. **Google Places Integration**
   - Source of truth for canonical data
   - Caching prevents rate limit issues
   - Handles validation gracefully

4. **Production Ready**
   - Retry logic for server errors
   - Dry-run mode for safety
   - Comprehensive logging

### Code Quality

✅ **Excellent**
- Clean separation: workflow in `graph/`, class in `tools/`
- Reuses existing agentic patterns
- Well-documented with examples
- Comprehensive error handling

---

## Integration & Workflow

### Recommended Workflow

1. **Run Receipt Metadata Finder** (for receipts with missing metadata) ⭐
   ```python
   finder = ReceiptMetadataFinder(dynamo_client, places_client, chroma_client, embed_fn)
   report = await finder.find_all_metadata_agentic()
   await finder.apply_fixes(dry_run=False)
   ```

2. **Run Harmonizer V3** (for receipts with place_id) ⭐
   ```python
   harmonizer = MerchantHarmonizerV3(dynamo_client, places_client)
   report = await harmonizer.harmonize_all()
   await harmonizer.apply_fixes(dry_run=False)
   ```

### Test Scripts

```bash
# Test Receipt Metadata Finder (NEW & IMPROVED)
python scripts/test_receipt_metadata_finder.py --limit 10

# Test Harmonizer V3
python scripts/test_harmonizer_v3.py --limit 5

# Apply fixes with dry run
python scripts/test_receipt_metadata_finder.py --apply --dry-run

# Actually apply fixes
python scripts/test_receipt_metadata_finder.py --apply
```

### Dependencies

**Receipt Metadata Finder**:
- DynamoDB client
- Google Places client
- ChromaDB client (required for agent-based approach)
- Embedding function (required for agent-based approach)
- LLM (Ollama) for agent reasoning

**Harmonizer V3**:
- DynamoDB client
- Google Places client (for validation)
- LLM (Ollama) for agent reasoning

### Shared Components

All agents use:
- `receipt_agent.tools.agentic` - Shared agentic tools
- `receipt_agent.config.settings` - Configuration
- `receipt_dynamo` - DynamoDB client
- `receipt_places` - Google Places client

---

## Overall Assessment

### Strengths

1. **Clear Separation of Responsibilities**
   - Finder: Populate missing place_ids
   - Harmonizer V3: Ensure consistency with intelligent reasoning

2. **Agent-Based Architecture**
   - Both tools now use LLM agents for complex reasoning
   - Simple fallbacks for straightforward cases

3. **Excellent Documentation**
   - Clear docstrings with examples
   - Usage examples
   - Expected output examples

4. **Production Ready**
   - Retry logic for transient errors
   - Dry-run mode for safety
   - Comprehensive logging with LangSmith support

### What's New in V3 ⭐

1. **Agent-Based Reasoning** ✅ IMPLEMENTED
   - LLM agent reasons about each inconsistent group
   - Handles edge cases (OCR errors, address-like names)
   - Provides confidence scores and reasoning

2. **Intelligent Conflict Resolution** ✅ IMPLEMENTED
   - Agent decides between consensus and Google Places
   - Explains reasoning for each decision

3. **Transparent Audit Trail** ✅ IMPLEMENTED
   - Every decision has reasoning
   - Tracks source (Google Places vs consensus)

---

## Recommendations

### Completed ✅

1. ✅ **Agent-based Harmonizer** - Implemented as V3!
2. ✅ **Confidence scoring** with agent reasoning
3. ✅ **Edge case handling** for address-like names
4. ✅ **Google Places as source of truth**
5. ✅ **Test script** for V3

### Future Work

1. 🔲 **Automated pipeline** - Run both agents periodically
2. 🔲 **Metrics dashboard** - Track success rates over time
3. 🔲 **Unit tests** - Add tests for core logic
4. 🔲 **Feedback loop** - Improve prompts based on results
5. 🔲 **Low-confidence review queue** - UI for manual review

---

## Conclusion

With V3, both agents now use intelligent LLM-based reasoning:

| Agent | Approach | Best For |
|-------|----------|----------|
| Receipt Metadata Finder ⭐ | Agent + Receipt Content + Google Places | Finding ALL missing metadata |
| Harmonizer V3 | Agent + Google Places | Ensuring consistency |
| Harmonizer V2 | Rule-based consensus | Simple cases (legacy) |

**Key Strengths**:
- ✅ Both agents use LLM reasoning
- ✅ Google Places as source of truth
- ✅ Confidence scoring with explanations
- ✅ Edge case handling
- ✅ Production-ready with retries and dry-run

This is now a complete agent-based solution for receipt metadata quality! 🎯

