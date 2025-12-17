"""Tools for on-the-fly word embedding for agents.

These tools allow agents to embed words without needing them to be pre-stored
in ChromaDB, enabling hypothetical queries and validation of words
from partial context.
"""

from typing import Any, List, Optional
from pydantic import BaseModel, Field

from receipt_label.embedding.word.realtime import format_word_for_embedding
from receipt_label.utils import get_client_manager


class EmbedWordOnTheFlyInput(BaseModel):
    """Input schema for embedding a word on-the-fly."""

    word_text: str = Field(description="The word text to embed")
    left_words: Optional[List[str]] = Field(
        default=None,
        description=(
            "List of words to the left (use ['<EDGE>'] if at edge, "
            "or omit for auto-edge)"
        ),
    )
    right_words: Optional[List[str]] = Field(
        default=None,
        description=(
            "List of words to the right (use ['<EDGE>'] if at edge, "
            "or omit for auto-edge)"
        ),
    )
    context_size: int = Field(
        default=2,
        description="Number of context words on each side (default: 2)",
    )


class SearchWithOnTheFlyEmbeddingInput(BaseModel):
    """Input schema for searching with on-the-fly embedding."""

    word_text: str = Field(description="The word text to search for")
    left_words: Optional[List[str]] = Field(
        default=None,
        description="List of words to the left (context for embedding)",
    )
    right_words: Optional[List[str]] = Field(
        default=None,
        description="List of words to the right (context for embedding)",
    )
    n_results: int = Field(
        default=10,
        description="Number of similar words to return (max 30)",
        ge=1,
        le=30,
    )
    merchant_name: Optional[str] = Field(
        default=None,
        description="Optional merchant name to filter results",
    )


def create_on_the_fly_embedding_tools(
    chroma_client: Any,
) -> tuple[List[Any], dict]:
    """
    Create tools for on-the-fly word embedding.

    Args:
        chroma_client: ChromaDB client instance

    Returns:
        Tuple of (tools_list, state_dict)
    """
    # pylint: disable=import-outside-toplevel
    from langchain_core.tools import tool

    state = {}

    @tool(args_schema=EmbedWordOnTheFlyInput)
    def embed_word_on_the_fly(
        word_text: str,
        left_words: Optional[List[str]] = None,
        right_words: Optional[List[str]] = None,
        context_size: int = 2,
    ) -> dict:
        """
        Embed a word on-the-fly for similarity search.

        Useful when:
        - Word doesn't exist in ChromaDB yet
        - Searching for similar words using hypothetical context
        - Embedding words from partial receipt information

        This creates an embedding without needing the word to be stored
        in ChromaDB.
        The embedding can then be used to search for similar words.

        Args:
            word_text: The word to embed
            left_words: List of words to the left (use ["<EDGE>"] if at edge,
                or omit for auto-edge)
            right_words: List of words to the right (use ["<EDGE>"] if at edge,
                or omit for auto-edge)
            context_size: Number of context words on each side (default: 2)

        Returns:
            Dictionary with:
            - formatted_text: The formatted string used for embedding
            - embedding_length: Length of the embedding vector
            - can_query: Whether this can be used to query ChromaDB
            - example_usage: How to use this embedding for search
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
            "embedding_length": len(embedding),
            "can_query": True,
            "example_usage": (
                "Use search_with_on_the_fly_embedding() to search ChromaDB "
                "with this word"
            ),
        }

    @tool(args_schema=SearchWithOnTheFlyEmbeddingInput)
    def search_with_on_the_fly_embedding(
        word_text: str,
        left_words: Optional[List[str]] = None,
        right_words: Optional[List[str]] = None,
        n_results: int = 10,
        merchant_name: Optional[str] = None,
    ) -> list[dict]:
        """
        Embed a word on-the-fly and search for similar words in ChromaDB.

        This is useful when you want to find similar words but don't have
        the word stored in ChromaDB, or want to search with hypothetical
        context.

        Example use cases:
        - Validate a word that hasn't been embedded yet
        - Search for similar words with different context
        - Test hypothetical scenarios (e.g., "what if this word had
          different neighbors?")

        Args:
            word_text: The word to search for
            left_words: Context words to the left (use ["<EDGE>"] if at edge)
            right_words: Context words to the right (use ["<EDGE>"] if at edge)
            n_results: Number of results to return (max 30)
            merchant_name: Optional merchant name to filter results

        Returns:
            List of similar words with:
            - text: The word text
            - image_id, receipt_id, line_id, word_id: Location identifiers
            - label_status: VALID, INVALID, PENDING, etc.
            - valid_labels: Comma-separated list of valid labels
            - similarity: Similarity score (0.0-1.0, higher is more similar)
            - merchant_name: Merchant name from that receipt
        """
        # Default to <EDGE> if no context provided
        if left_words is None:
            left_words = ["<EDGE>"] * 2
        if right_words is None:
            right_words = ["<EDGE>"] * 2

        # Format the word with context
        formatted_text = format_word_for_embedding(
            word_text=word_text,
            left_words=left_words,
            right_words=right_words,
            context_size=2,
        )

        # Get OpenAI client and embed
        client_manager = get_client_manager()
        openai_client = client_manager.openai

        response = openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=[formatted_text],
        )

        query_embedding = response.data[0].embedding

        # Query ChromaDB
        where_filter = None
        if merchant_name:
            where_filter = {"merchant_name": {"$eq": merchant_name}}

        results = chroma_client.query(
            collection_name="words",
            query_embeddings=[query_embedding],
            n_results=n_results + 5,  # Get extra to account for filtering
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        # Format results
        similar_words = []
        if results and results.get("documents"):
            ids = results.get("ids", [[]])[0]
            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for doc_id, doc, metadata, distance in zip(
                ids,
                documents,
                metadatas,
                distances,
            ):
                # Calculate similarity (distance is typically 0-2,
                # convert to 0-1)
                similarity = max(0.0, 1.0 - (distance / 2))

                similar_words.append(
                    {
                        "text": doc,
                        "image_id": metadata.get("image_id"),
                        "receipt_id": metadata.get("receipt_id"),
                        "line_id": metadata.get("line_id"),
                        "word_id": metadata.get("word_id"),
                        "label_status": metadata.get("label_status"),
                        "valid_labels": metadata.get("valid_labels"),
                        "invalid_labels": metadata.get("invalid_labels"),
                        "similarity": round(similarity, 4),
                        "merchant_name": metadata.get("merchant_name"),
                    }
                )

        return similar_words

    tools = [embed_word_on_the_fly, search_with_on_the_fly_embedding]

    return tools, state
