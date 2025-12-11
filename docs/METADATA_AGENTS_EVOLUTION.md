# Metadata Agents Evolution: What Makes the Harmonizer Better

## Age Comparison

**Both agents were created at the same time:**
- **Created**: December 5, 2025, 19:22:13 (commit `ddc2b5d7e`)
- **Initial sizes**:
  - Harmonizer: 852 lines
  - Metadata Finder: 567 lines

**Current state (after 5 days of development):**
- **Harmonizer**: 2,312 lines (+1,460 lines, +171% growth)
  - 7 commits total
  - Most recent: Dec 10, 2025 (address sanitization, CoVe integration)
- **Metadata Finder**: 696 lines (+129 lines, +23% growth)
  - 4 commits total
  - Most recent: Dec 10, 2025 (retry logic improvements)

**Conclusion**: While both were created simultaneously, the **Harmonizer has received significantly more polish and is the more mature agent**.

## Key Improvements in the Harmonizer

### 1. **Address Verification** (`verify_address_on_receipt`)
**What it does**: Verifies that an address from metadata or Google Places actually appears on the receipt text.

**Why it's critical**: Catches wrong place_id assignments. For example:
- Metadata says: "55 Fulton St, New York, NY"
- Receipt text shows: California address
- **This indicates a wrong place_id assignment** - the receipt doesn't belong to that place

**Implementation**:
- Extracts street number, street name, city/state from address
- Checks if these appear in receipt text (allowing for OCR errors)
- Returns evidence and recommendation to use `find_correct_metadata` if mismatch

**Location**: `harmonizer_workflow.py:595-829`

### 2. **Address Sanitization** (Multiple Layers)
**What it does**: Prevents reasoning text and malformed ZIP codes from being saved as addresses.

**Why it's needed**: LLMs sometimes inject reasoning into addresses:
- Bad: `"123 Main St, Los Angeles, CA 90001? Actually need to verify this"`
- Bad: `"123 Main St, Los Angeles, CA 913001"` (malformed ZIP)
- Good: `"123 Main St, Los Angeles, CA 91301"`

**Implementation**:
- **In agent tool** (`submit_harmonization`): Validates address before accepting
  - Rejects addresses with `?`, `"actually need"`, `"lind0"` (observed typo)
  - Rejects malformed ZIPs (6+ consecutive digits)
- **In Lambda handler** (`sanitize_canonical_address`): Additional sanitization
  - Strips quotes and whitespace
  - Fixes malformed ZIPs: `913001` → `91301` (truncates extra trailing digits)
  - Returns previous value if address is invalid (avoids overwriting with bad data)

**Location**:
- Agent tool: `harmonizer_workflow.py:1785-1803`
- Lambda handler: `harmonize_metadata.py:134-163`

### 3. **Text Consistency Verification** (CoVe Integration)
**What it does**: Verifies that all receipts in a place_id group actually contain text consistent with canonical metadata.

**Why it's needed**: Even if receipts share the same place_id, they might be from different places if:
- Wrong place_id was assigned
- Multiple businesses at same address
- OCR errors caused incorrect grouping

**Implementation**:
- Spins up CoVe (Consistency Verification) sub-agent
- Checks each receipt's text against canonical metadata
- Returns verdicts: MATCH, MISMATCH, or UNSURE for each receipt
- Identifies outliers that may need different place_id

**Location**: `harmonizer_workflow.py:1617-1735`

### 4. **Sub-Agent Integration** (`find_correct_metadata`)
**What it does**: When a receipt has wrong metadata (especially wrong place_id), spins up the Metadata Finder agent as a sub-agent to find correct metadata.

**Why it's needed**: If address doesn't match receipt text, the receipt likely has wrong place_id. Instead of just harmonizing to wrong values, fix the root cause.

**Implementation**:
- Lazy-loads ChromaDB if needed (from S3 bucket)
- Creates metadata finder graph
- Runs metadata finder agent
- Returns correct place_id, merchant_name, address, phone
- Falls back to Google Places search if ChromaDB unavailable

**Location**: `harmonizer_workflow.py:1291-1598`

### 5. **Address-Like Name Detection** (`_is_address_like`)
**What it does**: Detects when Google Places returns an address as the merchant name.

**Why it's needed**: Google Places sometimes returns addresses like "123 Main St" as the business name. This should never be used as a merchant name.

**Implementation**:
- Checks if name starts with a number
- Checks for street indicators (st, street, ave, blvd, etc.)
- Returns True if looks like address
- Used by `find_businesses_at_address` tool to find actual business

**Location**: `harmonizer_workflow.py:1891-1924`

### 6. **Comprehensive Retry Logic**
**What it does**: Handles transient errors with exponential backoff and jitter.

**Why it's needed**: Ollama/LangGraph can have transient network issues, connection errors, server errors (5xx).

**Implementation**:
- 5 retries with exponential backoff (2s, 4s, 8s, 16s, 32s)
- Jitter to prevent thundering herd
- Capped at 30s max wait
- Fails fast on rate limits (429)
- Distinguishes connection errors vs server errors

**Location**: `harmonizer_workflow.py:1983-2099`

**Note**: Metadata Finder also has this (added Dec 10), but Harmonizer had it first and it's more comprehensive.

### 7. **Better Error Handling & Fallbacks**
**What it does**: Multiple fallback methods for fetching receipt details.

**Why it's needed**: DynamoDB queries can fail for various reasons (missing entities, trailing characters in image_id, etc.)

**Implementation**:
- `_fetch_receipt_details_fallback()`: Alternative methods to fetch receipt details
- Sanitizes image_id (removes trailing `?` and whitespace)
- Tries multiple image_id variants
- Fetches lines/words directly if receipt entity missing
- Handles cases where metadata exists but OCR text is missing

**Location**: `harmonizer_workflow.py:215-360`

### 8. **More Sophisticated Tooling**
**What it does**: More tools for group analysis and verification.

**Tools unique to Harmonizer**:
- `get_group_summary`: See all receipts in place_id group with metadata variations
- `get_field_variations`: Analyze variations of a specific field across group
- `display_receipt_text`: Formatted receipt text with verification prompt
- `verify_address_on_receipt`: Verify address matches receipt text
- `verify_text_consistency`: CoVe text consistency check
- `find_correct_metadata`: Sub-agent to fix wrong metadata
- `find_businesses_at_address`: Find businesses when Google returns address as name

**Metadata Finder tools** (simpler, focused on single receipt):
- `get_my_metadata`, `get_my_lines`, `get_my_words`: Context about current receipt
- `verify_with_google_places`: Search Google Places
- `search_lines`, `search_words`: Similarity search
- `submit_metadata`: Submit findings

### 9. **Better Prompting & Instructions**
**What it does**: More detailed system prompt with specific strategies for edge cases.

**Key differences**:
- **Harmonizer**: 200+ lines of detailed instructions
  - Specific strategies for each step
  - Critical rules highlighted (address verification, address-like names)
  - Decision guidelines for each field
  - Confidence scoring guidelines
- **Metadata Finder**: 110 lines of instructions
  - Simpler, focused on finding missing metadata
  - Less emphasis on edge cases

**Location**:
- Harmonizer: `harmonizer_workflow.py:76-207`
- Metadata Finder: `receipt_metadata_finder_workflow.py:74-183`

### 10. **Lazy Loading Support**
**What it does**: Can download ChromaDB from S3 on-demand for sub-agents.

**Why it's needed**: Lambda functions have limited disk space. ChromaDB is large (~GB). Only download when sub-agent is actually needed.

**Implementation**:
- Passes `chromadb_bucket` to tools
- Lazy-loads when `find_correct_metadata` is called
- Downloads both lines and words collections
- Caches in state for subsequent calls
- Falls back to Google Places if ChromaDB unavailable

**Location**: `harmonizer_workflow.py:1319-1426`

## What Metadata Finder Does Well (Simpler is Better)

The Metadata Finder is **simpler and more focused**, which has advantages:

1. **Single-purpose**: Just finds missing metadata - no group analysis complexity
2. **Faster**: No need to analyze multiple receipts
3. **More reliable**: Fewer moving parts, less can go wrong
4. **Better for initial population**: When receipts have no metadata, use Metadata Finder first

## When to Use Which Agent

### Use Metadata Finder When:
- Receipt has missing metadata (no place_id, merchant_name, address, or phone)
- Initial metadata population
- Single receipt processing
- Need to find metadata from scratch

### Use Harmonizer When:
- Receipts share place_id but have inconsistent metadata
- Need to ensure consistency across a group
- Suspect wrong place_id assignments
- Need to verify addresses match receipt text
- Production batch processing (via Lambda)

## Summary: What Makes Harmonizer Better

1. ✅ **Address verification** - Catches wrong place_id assignments
2. ✅ **Address sanitization** - Prevents reasoning text and malformed ZIPs
3. ✅ **Text consistency verification** - CoVe integration for outlier detection
4. ✅ **Sub-agent integration** - Can fix wrong place_ids automatically
5. ✅ **Address-like name detection** - Prevents using addresses as merchant names
6. ✅ **Comprehensive retry logic** - Handles transient errors gracefully
7. ✅ **Better error handling** - Multiple fallback methods
8. ✅ **More sophisticated tooling** - Group analysis and verification tools
9. ✅ **Better prompting** - Detailed instructions for edge cases
10. ✅ **Lazy loading** - Efficient ChromaDB usage in Lambda

The Harmonizer is the **production-ready, polished agent** that handles real-world edge cases. The Metadata Finder is the **simpler, focused agent** for initial metadata population.
