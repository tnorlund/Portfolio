# Agent On-the-Fly Word Embedding

## Overview

The realtime embedding system is **fully functional** and can be used by agents to embed words on-the-fly without needing the full receipt context or pre-stored embeddings in ChromaDB.

## Current State

### ✅ Functional Components

1. **`format_word_for_embedding()`** - Helper function for on-the-fly embedding
   - Location: `receipt_label/receipt_label/embedding/word/realtime.py`
   - Input: Just word text + left/right neighbors
   - Output: Formatted string ready for embedding

2. **`embed_words_realtime()`** - Full embedding function
   - Location: `receipt_label/receipt_label/embedding/word/realtime.py`
   - Input: List of ReceiptWord entities
   - Output: List of (word, embedding) tuples

### Current Agent Usage

Agents currently use **stored embeddings** from ChromaDB:
- `find_similar_to_my_word()` - Fetches stored embedding from ChromaDB
- `search_similar_words()` - Uses stored embedding to query ChromaDB

## On-the-Fly Embedding for Agents

### Use Case

An agent can embed a word on-the-fly when:
- The word doesn't exist in ChromaDB yet
- The agent wants to search for similar words using a hypothetical word
- The agent needs to embed a word from partial context (e.g., from user input)

### Example Agent Tool

Here's what an agent tool would look like:

```python
from receipt_label.embedding.word.realtime import format_word_for_embedding
from receipt_label.utils import get_client_manager

@tool
def embed_word_on_the_fly(
    word_text: str,
    left_words: List[str] = None,
    right_words: List[str] = None,
    context_size: int = 2,
) -> dict:
    """
    Embed a word on-the-fly for similarity search.

    Useful when:
    - Word doesn't exist in ChromaDB
    - Searching for similar words using hypothetical context
    - Embedding words from partial receipt information

    Args:
        word_text: The word to embed
        left_words: List of words to the left (use ["<EDGE>"] if at edge)
        right_words: List of words to the right (use ["<EDGE>"] if at edge)
        context_size: Number of context words on each side (default: 2)

    Returns:
        Dictionary with:
        - formatted_text: The formatted string used for embedding
        - embedding: The embedding vector
        - can_query: Whether this can be used to query ChromaDB
    """
    # Default to <EDGE> if no context provided
    if left_words is None:
        left_words = ["<EDGE>"] * context_size
    if right_words is None:
        right_words = ["<EDGE>"] * context_size

    # Format the word with context
    formatted_text = format_word_for_embedding(
        word_text=word_text,
        left_words=left_words,
        right_words=right_words,
        context_size=context_size,
    )

    # Get OpenAI client and embed
    client_manager = get_client_manager()
    openai_client = client_manager.openai

    response = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=[formatted_text],
    )

    embedding = response.data[0].embedding

    return {
        "formatted_text": formatted_text,
        "embedding": embedding,
        "can_query": True,
    }

@tool
def search_with_on_the_fly_embedding(
    word_text: str,
    left_words: List[str] = None,
    right_words: List[str] = None,
    n_results: int = 10,
    merchant_name: str = None,
) -> list[dict]:
    """
    Embed a word on-the-fly and search for similar words in ChromaDB.

    This is useful when you want to find similar words but don't have
    the word stored in ChromaDB, or want to search with hypothetical context.

    Args:
        word_text: The word to search for
        left_words: Context words to the left
        right_words: Context words to the right
        n_results: Number of results to return
        merchant_name: Optional merchant filter

    Returns:
        List of similar words with metadata
    """
    # Embed on-the-fly
    embed_result = embed_word_on_the_fly(word_text, left_words, right_words)
    query_embedding = embed_result["embedding"]

    # Query ChromaDB
    client_manager = get_client_manager()
    chroma_client = client_manager.chroma

    where_filter = None
    if merchant_name:
        where_filter = {"merchant_name": {"$eq": merchant_name}}

    results = chroma_client.query(
        collection_name="words",
        query_embeddings=[query_embedding],
        n_results=n_results,
        where=where_filter,
        include=["documents", "metadatas", "distances"],
    )

    # Format results
    similar_words = []
    if results and results.get("documents"):
        for i, doc in enumerate(results["documents"][0]):
            metadata = results["metadatas"][0][i] if results.get("metadatas") else {}
            distance = results["distances"][0][i] if results.get("distances") else None
            similarity = 1 - distance if distance is not None else None

            similar_words.append({
                "text": doc,
                "image_id": metadata.get("image_id"),
                "receipt_id": metadata.get("receipt_id"),
                "line_id": metadata.get("line_id"),
                "word_id": metadata.get("word_id"),
                "label_status": metadata.get("label_status"),
                "valid_labels": metadata.get("valid_labels"),
                "similarity": similarity,
            })

    return similar_words
```

## Example Agent Workflow

### Scenario: Agent wants to validate a word that doesn't exist in ChromaDB

```python
# Agent receives a word from user input or partial receipt data
word_text = "Total"
left_context = ["Subtotal", "Items"]  # From receipt analysis
right_context = ["Tax", "Discount"]    # From receipt analysis

# Embed on-the-fly
embedding_result = embed_word_on_the_fly(
    word_text=word_text,
    left_words=left_context,
    right_words=right_context,
)

# Search for similar words
similar_words = search_with_on_the_fly_embedding(
    word_text=word_text,
    left_words=left_context,
    right_words=right_context,
    n_results=20,
)

# Agent can now:
# 1. Check if similar words have VALID labels
# 2. Determine if this word should be labeled similarly
# 3. Validate the word based on consensus
```

## Benefits for Agents

1. **No Pre-storage Required**: Embed words that don't exist in ChromaDB
2. **Hypothetical Queries**: Test "what if" scenarios with different context
3. **Partial Context**: Work with incomplete receipt information
4. **Real-time Validation**: Validate words as they're discovered

## Integration Points

### Current Agent Tools That Could Use This

1. **Label Validation Agent**
   - Embed pending labels on-the-fly
   - Search for similar validated words
   - Make validation decisions

2. **Receipt Metadata Finder**
   - Embed merchant names from partial OCR
   - Search for similar merchant names
   - Validate merchant identification

3. **Label Harmonizer**
   - Embed words from different receipts
   - Find semantic similarities
   - Group similar labels

## Implementation Status

✅ **Core Functions**: Fully implemented and tested
- `format_word_for_embedding()` - Ready to use
- `embed_words_realtime()` - Functional

⚠️ **Agent Integration**: Not yet implemented
- Need to add tools to agent toolkits
- Need to update agent workflows to use on-the-fly embedding

## Next Steps

1. Add `embed_word_on_the_fly` tool to agent toolkits
2. Add `search_with_on_the_fly_embedding` tool
3. Update agent workflows to use on-the-fly embedding when appropriate
4. Test with real agent scenarios
