# Receipt Label

A Python package for labeling and validating receipt data using GPT and Pinecone.

## Embedding Strategy

For each receipt word, we generate two embeddings to capture both semantic and spatial context:

1. **Word-level embedding**

   - Text: `<word> [label=ITEM_NAME] (pos=top-left)`
   - Captures the token’s semantic content and its key attributes in a single vector.

2. **Context-level embedding**
   - Text: the concatenated words with similar Y-position (using `get_hybrid_context`)
   - Captures layout and neighboring-word relationships to provide visual context.

**Purpose:**

- The dual embeddings enrich prompts by retrieving both token-level and line-level examples from Pinecone.
- Semantic neighbors (word view) help validate the token’s meaning in isolation.
- Context neighbors (context view) help validate how the token is used in layout (e.g., line items, addresses).

**Metadata Updates:**

- On the **valid** path: update each embedding’s metadata with `status: VALID`.
- On the **invalid** path: update metadata with `status: INVALID` and add `proposed_label: <new_label>`.
- We reuse the same vector IDs so we never duplicate embeddings—only metadata changes.

This approach allows agentic, data‑driven validation and label proposal, while keeping Pinecone storage efficient and easy to query.

## Label Validation Strategy

## 🧪 Label Validation Strategy

This project uses a layered, multi-pass approach to label validation in order to combine efficiency, semantic similarity, and multi-hop reasoning.

### 🔹 Pass 1: Batch Label Validation with GPT

All `ReceiptWordLabel` entries are processed via batch completions using OpenAI’s function calling. GPT evaluates each label in context and flags whether it is valid. If the label is deemed incorrect, it may suggest a corrected label and provide a rationale. This step is fully parallelizable using the OpenAI Batch API.

### 🔹 Pass 2: Embedding-Based Refinement

For any labels marked as invalid in the first pass, a second evaluation is conducted using Pinecone. The model is provided with:

- The word and its receipt context
- The original and GPT-suggested labels
- A list of nearby Pinecone embeddings with known correct labels

GPT uses this expanded semantic context to reconsider its earlier assessment. This step improves precision on edge cases like numbers, prepositions, or ambiguous merchant terms.

## 🔹 Pass 3: Agentic Label Resolution

The final pass uses the OpenAI Agents SDK to resolve remaining ambiguous or inconsistent labels. The agent can:

- Call Pinecone to compare embeddings across receipts
- Query DynamoDB for past receipt structure
- Apply logical rules (e.g., label propagation across lines)
- Chain multiple reasoning steps before finalizing a label

This stage is ideal for advanced logic, correction propagation, and multi-hop validation workflows.
