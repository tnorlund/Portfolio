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
