# Agent Tools Comparison: Receipt Metadata Finder vs Harmonizer

## Overview

Both agents use LLM reasoning but have different tool sets optimized for their specific tasks:

- **Receipt Metadata Finder**: Finds ALL missing metadata for individual receipts
- **Harmonizer**: Ensures consistency across groups of receipts sharing the same place_id

---

## Tool Categories

### 1. Context Tools (Understanding the Receipt)

#### Place ID Finder Agent
| Tool | Purpose | Returns |
|------|---------|---------|
| `get_my_metadata` | Get current metadata (may not have place_id) | merchant_name, place_id, address, phone, validation_status |
| `get_my_lines` | See all text lines on the receipt | List of lines with line_id, text, has_embedding |
| `get_my_words` | See labeled words (MERCHANT_NAME, PHONE, ADDRESS, etc.) | List of words with line_id, word_id, text, label |

#### Harmonizer Agent
| Tool | Purpose | Returns |
|------|---------|---------|
| `get_group_summary` | See all receipts in the place_id group | place_id, receipt_count, receipts list, field_summary (variations) |
| `get_receipt_content` | View actual content of a specific receipt | lines, labeled_words for a specific receipt |
| `get_field_variations` | See all variations of a field across the group | unique_values, variations, case_analysis |

**Key Difference**:
- Place ID Finder focuses on **one receipt** at a time
- Harmonizer focuses on **groups of receipts** sharing the same place_id

---

### 2. Similarity Search Tools (Finding Related Receipts)

#### Place ID Finder Agent ✅ HAS THESE
| Tool | Purpose | Uses |
|------|---------|-------|
| `find_similar_to_my_line` | Find similar lines on other receipts | Uses stored embeddings from ChromaDB |
| `find_similar_to_my_word` | Find similar words on other receipts | Uses stored embeddings from ChromaDB |
| `search_lines` | Search by arbitrary text (address, phone, merchant name) | Generates new embedding for query |
| `search_words` | Search for specific labeled words | Generates new embedding for query |

#### Harmonizer Agent ❌ DOES NOT HAVE THESE
- No similarity search tools
- Works with a pre-grouped set of receipts (by place_id)
- Doesn't need to find similar receipts - they're already grouped

**Why**: Harmonizer receives a pre-grouped set of receipts, so it doesn't need to search for similar ones.

---

### 3. Aggregation Tools (Understanding Consensus)

#### Place ID Finder Agent
| Tool | Purpose | Returns |
|------|---------|---------|
| `get_merchant_consensus` | Get canonical data for a merchant based on all receipts | receipt_count, most_common_place_id, most_common_address, most_common_phone, agreement percentages |

#### Harmonizer Agent
| Tool | Purpose | Returns |
|------|---------|---------|
| `get_field_variations` | See variations of a specific field (merchant_name, address, phone) | unique_values, variations with counts, case_analysis |

**Key Difference**:
- Place ID Finder: Looks across **all receipts for a merchant** (by name)
- Harmonizer: Looks at **one place_id group** (already grouped)

---

### 4. Google Places Tools (Source of Truth)

#### Place ID Finder Agent
| Tool | Purpose | Parameters |
|------|---------|------------|
| `verify_with_google_places` | Search Google Places API | phone_number, address, merchant_name, place_id |
| `find_businesses_at_address` | Find businesses at a specific address | address (returns multiple candidates) |

#### Harmonizer Agent
| Tool | Purpose | Parameters |
|------|---------|------------|
| `verify_place_id` | Get official data for a specific place_id | None (uses place_id from group) |
| `find_businesses_at_address` | Find businesses at a specific address | address (returns multiple candidates) |

**Key Difference**:
- Place ID Finder: **Searches** Google Places (phone, address, text search)
- Harmonizer: **Validates** an existing place_id (already known)

---

### 5. Comparison Tools

#### Place ID Finder Agent
| Tool | Purpose | Returns |
|------|---------|---------|
| `compare_with_receipt` | Detailed comparison with another receipt | same_merchant, same_place_id, same_address, same_phone, differences |
| `get_place_id_info` | Get all receipts using a specific Place ID | receipt_count, canonical_merchant_name, merchant_name_variants, addresses |

#### Harmonizer Agent
❌ **No comparison tools** - Works with a single group, doesn't need to compare across groups

---

### 6. Decision Tools (Terminating the Workflow)

#### Place ID Finder Agent
| Tool | Purpose | Parameters |
|------|---------|------------|
| `submit_place_id` | Submit the found place_id | place_id, place_name, place_address, place_phone, confidence, reasoning, search_methods_used |

#### Harmonizer Agent
| Tool | Purpose | Parameters |
|------|---------|------------|
| `submit_harmonization` | Submit canonical values for the group | canonical_merchant_name, canonical_address, canonical_phone, confidence, reasoning, source |

**Key Difference**:
- Place ID Finder: Submits **one place_id** for **one receipt**
- Harmonizer: Submits **canonical values** for **all receipts in a group**

---

## Complete Tool Lists

### Receipt Metadata Finder Agent Tools (13 tools) ⭐
1. ✅ `get_my_metadata` - Context
2. ✅ `get_my_lines` - Context
3. ✅ `get_my_words` - Context
4. ✅ `find_similar_to_my_line` - Similarity Search
5. ✅ `find_similar_to_my_word` - Similarity Search
6. ✅ `search_lines` - Similarity Search
7. ✅ `search_words` - Similarity Search
8. ✅ `get_merchant_consensus` - Aggregation
9. ✅ `get_place_id_info` - Comparison
10. ✅ `compare_with_receipt` - Comparison
11. ✅ `verify_with_google_places` - Google Places
12. ✅ `find_businesses_at_address` - Google Places
13. ✅ `submit_metadata` - Decision (submits ALL fields)

### Harmonizer Agent Tools (6 tools)
1. ✅ `get_group_summary` - Group Analysis
2. ✅ `get_receipt_content` - Group Analysis
3. ✅ `get_field_variations` - Group Analysis
4. ✅ `verify_place_id` - Google Places
5. ✅ `find_businesses_at_address` - Google Places
6. ✅ `submit_harmonization` - Decision

---

## Why the Differences?

### Receipt Metadata Finder Needs: ⭐
- **Similarity search** because it needs to find other receipts from the same merchant
- **Merchant consensus** to verify metadata from similar receipts
- **Google Places search** because it's finding missing fields from scratch
- **Comparison tools** to verify matches
- **Receipt content extraction** to extract metadata from the receipt itself (PRIMARY source)

### Harmonizer Needs:
- **Group analysis** because it works with pre-grouped receipts
- **Field variation analysis** to understand inconsistencies
- **Google Places validation** because place_id is already known
- **No similarity search** because receipts are already grouped

---

## Shared Tools

Both agents share:
- ✅ `find_businesses_at_address` - Handles edge case where Google returns address as merchant name
- ✅ Google Places integration - Both use it as source of truth
- ✅ Similar decision pattern - Both submit results with confidence and reasoning

---

## Tool Usage Patterns

### Receipt Metadata Finder Workflow: ⭐
1. Get receipt content (`get_my_lines`, `get_my_words`)
2. Extract metadata from receipt content (PRIMARY source)
3. Search for similar receipts (`find_similar_to_my_line`, `search_lines`)
4. Check merchant consensus (`get_merchant_consensus`)
5. Search Google Places for missing fields (`verify_with_google_places`)
6. Compare results (`compare_with_receipt`)
7. Submit ALL metadata (`submit_metadata`) - place_id, merchant_name, address, phone

### Harmonizer Workflow:
1. Get group summary (`get_group_summary`)
2. Check Google Places (`verify_place_id`)
3. Analyze variations (`get_field_variations`)
4. Inspect specific receipts if needed (`get_receipt_content`)
5. Submit harmonization (`submit_harmonization`)

---

## Summary

| Aspect | Receipt Metadata Finder ⭐ | Harmonizer |
|--------|----------------|------------|
| **Focus** | Single receipt | Group of receipts |
| **Primary Goal** | Find ALL missing metadata | Ensure consistency |
| **Tools Count** | 13 tools | 6 tools |
| **Needs ChromaDB** | ✅ Yes (for similarity search) | ❌ No |
| **Needs Embeddings** | ✅ Yes (for similarity search) | ❌ No |
| **Google Places Usage** | Search (find missing fields) | Validate (verify place_id) |
| **Data Sources** | Receipt content (PRIMARY) + Google Places | Google Places (PRIMARY) |
| **Complexity** | Higher (more tools, more search) | Lower (focused on group analysis) |

Both agents are optimized for their specific tasks! 🎯

