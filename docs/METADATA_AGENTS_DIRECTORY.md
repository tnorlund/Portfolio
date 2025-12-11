# Receipt Metadata Agents Directory

Complete overview of all metadata agents in the `receipt_agent` package, their purposes, and file locations.

## Directory Structure

```
receipt_agent/receipt_agent/
├── graph/                          # LangGraph workflow definitions (main agents)
│   ├── harmonizer_workflow.py      # Harmonizer Agent (most mature)
│   ├── receipt_metadata_finder_workflow.py  # Metadata Finder Agent
│   ├── cove_text_consistency_workflow.py    # CoVe Sub-Agent
│   ├── place_id_finder_workflow.py  # Place ID Finder (older)
│   ├── label_validation_workflow.py # Label Validation Agent
│   ├── label_suggestion_workflow.py # Label Suggestion Agent
│   ├── receipt_grouping_workflow.py # Receipt Grouping Agent
│   ├── agentic_workflow.py         # Agentic Validation Agent
│   └── workflow.py                 # Legacy Validation Workflow
├── tools/                          # Tool wrappers and implementations
│   ├── agentic.py                  # Core agentic tools (ChromaDB, DynamoDB, Places)
│   ├── harmonizer_v3.py            # Harmonizer tool wrapper
│   ├── receipt_metadata_finder.py  # Metadata Finder tool wrapper
│   └── ...
└── clients/                        # Client factories
    └── factory.py                  # Creates DynamoDB, ChromaDB, Places, embedding clients
```

## Primary Metadata Agents

### 1. **Harmonizer Agent** (Most Mature)
**Purpose**: Ensures metadata consistency across receipts sharing the same `place_id`

**Location**: `graph/harmonizer_workflow.py`

**Key Functions**:
- `create_harmonizer_graph()` - Creates the LangGraph workflow
- `run_harmonizer_agent()` - Runs the agent for a place_id group

**What it does**:
1. Groups receipts by `place_id` (all should have identical metadata)
2. Validates against Google Places (source of truth)
3. Verifies addresses match receipt text (catches wrong place_id assignments)
4. Uses Metadata Finder sub-agent to find correct metadata for wrong receipts
5. Uses CoVe sub-agent to verify text consistency
6. Determines canonical values and which receipts need updates

**Tools Used**:
- `get_group_summary` - See all receipts in the place_id group
- `verify_place_id` - Get official Google Places data
- `verify_address_on_receipt` - Verify address matches receipt text
- `find_correct_metadata` - Sub-agent to find correct metadata
- `verify_text_consistency` - CoVe sub-agent
- `submit_harmonization` - Submit canonical values (REQUIRED)

**Used By**:
- `MerchantHarmonizerV3` class (tool wrapper in `tools/harmonizer_v3.py`)
- Lambda handler: `infra/metadata_harmonizer_step_functions/lambdas/harmonize_metadata.py`

**State Model**: `HarmonizerAgentState` (defined in same file)

---

### 2. **Receipt Metadata Finder Agent**
**Purpose**: Finds ALL missing metadata for a receipt (place_id, merchant_name, address, phone_number)

**Location**: `graph/receipt_metadata_finder_workflow.py`

**Key Functions**:
- `create_receipt_metadata_finder_graph()` - Creates the LangGraph workflow
- `run_receipt_metadata_finder()` - Runs the agent for a single receipt

**What it does**:
1. Examines receipt content (lines, words, labels)
2. Extracts metadata from receipt itself (MERCHANT_NAME, ADDRESS, PHONE labels)
3. Searches Google Places API (phone → address → merchant_name priority)
4. Uses similarity search (ChromaDB) to verify findings
5. Reasons about best values for each field
6. Submits all found fields with confidence scores

**Tools Used**:
- `get_my_metadata`, `get_my_lines`, `get_my_words` - Context about current receipt
- `verify_with_google_places` - Search Google Places API
- `find_similar_to_my_line`, `search_lines` - Similarity search via ChromaDB
- `get_merchant_consensus` - Get canonical data from other receipts
- `submit_metadata` - Submit findings (REQUIRED)

**Used By**:
- `ReceiptMetadataFinder` class (tool wrapper in `tools/receipt_metadata_finder.py`)
- Harmonizer agent (as sub-agent via `find_correct_metadata` tool)

**State Model**: `ReceiptMetadataFinderState` (defined in same file)

---

### 3. **CoVe Text Consistency Agent** (Sub-Agent)
**Purpose**: Verifies that all receipts in a place_id group actually contain text consistent with canonical metadata

**Location**: `graph/cove_text_consistency_workflow.py`

**Key Functions**:
- `create_cove_text_consistency_graph()` - Creates the LangGraph workflow
- `run_cove_text_consistency()` - Runs the agent for a place_id group

**What it does**:
1. Checks each receipt in the group
2. Compares receipt text against canonical metadata
3. Identifies outliers (receipts that may be from a different place)
4. Returns verdicts: MATCH, MISMATCH, or UNSURE for each receipt

**Used By**: Harmonizer agent (via `verify_text_consistency` tool)

**State Model**: `CoveTextConsistencyState` (defined in same file)

---

## Secondary/Supporting Agents

### 4. **Place ID Finder Agent** (Older/Deprecated)
**Purpose**: Finds Google Place ID for a receipt (older version, less comprehensive than Metadata Finder)

**Location**: `graph/place_id_finder_workflow.py`

**Key Functions**:
- `create_place_id_finder_graph()` - Creates the LangGraph workflow

**Note**: This is an older agent that only finds `place_id`. The **Receipt Metadata Finder** is the newer, more comprehensive replacement that finds all metadata fields.

---

### 5. **Label Validation Agent**
**Purpose**: Validates label suggestions for receipt words using all available context

**Location**: `graph/label_validation_workflow.py`

**Key Functions**:
- `create_label_validation_graph()` - Creates the LangGraph workflow

**What it does**:
- Validates suggested labels for words (MERCHANT_NAME, ADDRESS, PHONE, etc.)
- Uses ChromaDB, DynamoDB, and Google Places metadata for context
- Returns validated label with confidence

**State Model**: `LabelValidationState` (defined in same file)

---

### 6. **Label Suggestion Agent**
**Purpose**: Finds unlabeled words on a receipt and suggests labels using ChromaDB similarity search

**Location**: `graph/label_suggestion_workflow.py`

**What it does**:
- Finds unlabeled words
- Uses ChromaDB similarity search to find similar labeled words
- Suggests labels with minimal LLM calls

**Note**: This is a simpler workflow (not a full LangGraph agent with ReAct pattern)

---

### 7. **Receipt Grouping Agent**
**Purpose**: Determines correct receipt groupings in images (identifies if receipts are incorrectly split)

**Location**: `graph/receipt_grouping_workflow.py`

**Key Functions**:
- `create_receipt_grouping_graph()` - Creates the LangGraph workflow

**What it does**:
- Tries different receipt combinations
- Evaluates which grouping makes the most sense
- Helps fix incorrect receipt splits

**State Model**: `GroupingState` (defined in same file)

---

### 8. **Agentic Validation Agent**
**Purpose**: Validates receipt metadata using ReAct pattern with full agent autonomy

**Location**: `graph/agentic_workflow.py`

**Key Functions**:
- `create_agentic_validation_graph()` - Creates the LangGraph workflow
- `run_agentic_validation()` - Runs the agent

**What it does**:
- Gives LLM full autonomy to decide which tools to call
- Tools enforce guard rails (construct IDs, hardcode collections, etc.)
- Agent reasons about evidence and submits validation decision

**State Model**: `AgentState` (defined in same file)

---

### 9. **Legacy Validation Workflow** (Non-Agent)
**Purpose**: Older validation workflow using fixed graph structure (not agent-based)

**Location**: `graph/workflow.py`

**Key Functions**:
- `create_validation_graph()` - Creates the fixed workflow graph
- `run_validation()` - Runs the validation

**Note**: This is a non-agent workflow with fixed nodes. The **Agentic Validation Agent** is the newer agent-based replacement.

**State Model**: `ValidationState` (from `state/models.py`)

---

## Agent Relationships

```
Harmonizer Agent (Primary)
    ├── Uses Metadata Finder Agent (as sub-agent)
    └── Uses CoVe Agent (as sub-agent)

Metadata Finder Agent (Primary)
    └── Standalone (can be used independently)

CoVe Agent (Sub-Agent)
    └── Only used by Harmonizer

Place ID Finder (Deprecated)
    └── Replaced by Metadata Finder

Label Validation Agent (Supporting)
    └── Standalone (validates word labels)

Label Suggestion Agent (Supporting)
    └── Standalone (suggests word labels)

Receipt Grouping Agent (Supporting)
    └── Standalone (fixes receipt splits)

Agentic Validation Agent (Supporting)
    └── Standalone (validates metadata)

Legacy Validation Workflow (Deprecated)
    └── Replaced by Agentic Validation Agent
```

## Tool Wrappers

These are convenience wrappers that make agents easier to use:

### `MerchantHarmonizerV3`
**Location**: `tools/harmonizer_v3.py`
**Wraps**: Harmonizer Agent
**Purpose**: High-level interface for harmonizing metadata

### `ReceiptMetadataFinder`
**Location**: `tools/receipt_metadata_finder.py`
**Wraps**: Metadata Finder Agent
**Purpose**: High-level interface for finding metadata

## Production Usage

### Lambda Handler
**Location**: `infra/metadata_harmonizer_step_functions/lambdas/harmonize_metadata.py`

**What it does**:
1. Loads receipts for place_ids (efficient GSI query)
2. Groups by place_id
3. For each inconsistent group:
   - Creates harmonizer graph
   - Runs harmonizer agent
   - Gets canonical values
4. Applies fixes (updates DynamoDB if not dry_run)

**Uses**: Harmonizer Agent (via `MerchantHarmonizerV3`)

## Summary

**Primary Metadata Agents** (for producing metadata):
1. **Harmonizer Agent** - Ensures consistency across place_id groups
2. **Metadata Finder Agent** - Finds missing metadata for receipts
3. **CoVe Agent** - Verifies text consistency (sub-agent)

**Supporting Agents** (for other tasks):
4. Label Validation Agent - Validates word labels
5. Label Suggestion Agent - Suggests word labels
6. Receipt Grouping Agent - Fixes receipt splits
7. Agentic Validation Agent - Validates metadata

**Deprecated/Legacy**:
8. Place ID Finder Agent - Replaced by Metadata Finder
9. Legacy Validation Workflow - Replaced by Agentic Validation Agent

The **Harmonizer** and **Metadata Finder** are the two main agents for producing receipt metadata (place_id, merchant_name, address, phone_number).
