"""
ChromaDB tools for agentic similarity search.

These tools enable the agent to query ChromaDB for similar receipts,
find patterns across merchants, and validate metadata consistency.
"""

import logging
from typing import Any, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from receipt_agent.state.models import ChromaSearchResult

logger = logging.getLogger(__name__)


class QuerySimilarLinesInput(BaseModel):
    """Input schema for query_similar_lines tool."""

    query_text: str = Field(
        description=(
            "Text to search for similar lines " "(e.g., address or phone line)"
        )
    )
    n_results: int = Field(
        default=10,
        description="Number of similar lines to return",
        ge=1,
        le=50,
    )
    merchant_filter: Optional[str] = Field(
        default=None,
        description="Optional merchant name to filter results",
    )
    min_similarity: float = Field(
        default=0.5,
        description="Minimum similarity score threshold",
        ge=0.0,
        le=1.0,
    )


class QuerySimilarWordsInput(BaseModel):
    """Input schema for query_similar_words tool."""

    word_text: str = Field(description="Word text to search for similar words")
    label_type: Optional[str] = Field(
        default=None,
        description=(
            "Filter by label type (e.g., 'MERCHANT_NAME', 'PHONE', 'ADDRESS')"
        ),
    )
    n_results: int = Field(
        default=10,
        description="Number of similar words to return",
        ge=1,
        le=50,
    )


class SearchByMerchantInput(BaseModel):
    """Input schema for search_by_merchant_name tool."""

    merchant_name: str = Field(description="Merchant name to search for")
    n_results: int = Field(
        default=20,
        description="Maximum number of receipts to return",
        ge=1,
        le=100,
    )


class SearchByPlaceIdInput(BaseModel):
    """Input schema for search_by_place_id tool."""

    place_id: str = Field(description="Google Place ID to search for")


@tool(args_schema=QuerySimilarLinesInput)
def query_similar_lines(
    query_text: str,
    n_results: int = 10,
    merchant_filter: Optional[str] = None,
    min_similarity: float = 0.5,
    # Injected at runtime
    _chroma_client: Any = None,
    _embed_fn: Any = None,
) -> list[dict[str, Any]]:
    """
    Search ChromaDB for receipt lines similar to the query text.

    Use this tool to find receipts with similar addresses, phone numbers,
    or merchant information. Returns matching lines with their metadata
    including merchant_name, address, phone, and similarity scores.

    This is useful for:
    - Finding other receipts from the same merchant
    - Verifying address patterns across receipts
    - Cross-referencing phone numbers
    """
    if _chroma_client is None:
        return [{"error": "ChromaDB client not configured"}]

    try:
        # Generate embedding for query
        if _embed_fn is None:
            return [{"error": "Embedding function not configured"}]

        query_embedding = _embed_fn([query_text])[0]

        # Build where clause for filtering
        where_clause = None
        if merchant_filter:
            where_clause = {
                "merchant_name": {"$eq": merchant_filter.strip().title()}
            }

        # Query ChromaDB
        results = _chroma_client.query(
            collection_name="lines",
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where_clause,
            include=["metadatas", "documents", "distances"],
        )

        # Process results
        output: list[dict[str, Any]] = []
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for idx, (doc_id, doc, meta, dist) in enumerate(
            zip(ids, documents, metadatas, distances)
        ):
            # Convert distance to similarity (ChromaDB uses L2 distance)
            # For normalized embeddings: similarity = 1 - (distance / 2)
            similarity = max(0.0, 1.0 - (dist / 2))

            if similarity < min_similarity:
                continue

            output.append(
                {
                    "rank": idx + 1,
                    "chroma_id": doc_id,
                    "text": doc,
                    "similarity_score": round(similarity, 4),
                    "image_id": meta.get("image_id"),
                    "receipt_id": meta.get("receipt_id"),
                    "line_id": meta.get("line_id"),
                    "merchant_name": meta.get("merchant_name"),
                    "normalized_phone": meta.get("normalized_phone_10"),
                    "normalized_address": meta.get("normalized_full_address"),
                }
            )

        logger.info(
            "Found %s similar lines above threshold %s",
            len(output),
            min_similarity,
        )
        return output

    except Exception as e:
        logger.error("Error querying ChromaDB: %s", e)
        return [{"error": str(e)}]


@tool(args_schema=QuerySimilarWordsInput)
def query_similar_words(
    word_text: str,
    label_type: Optional[str] = None,
    n_results: int = 10,
    # Injected at runtime
    _chroma_client: Any = None,
    _embed_fn: Any = None,
) -> list[dict[str, Any]]:
    """
    Search ChromaDB for words similar to the query word.

    Use this tool to find similar labeled words across receipts.
    This helps validate that a word's label is consistent with
    how similar words are labeled on other receipts.

    Filter by label_type to find words with specific labels
    (e.g., 'MERCHANT_NAME', 'PHONE', 'ADDRESS', 'TOTAL').
    """
    if _chroma_client is None:
        return [{"error": "ChromaDB client not configured"}]

    try:
        if _embed_fn is None:
            return [{"error": "Embedding function not configured"}]

        query_embedding = _embed_fn([word_text])[0]

        # Build where clause for label filtering
        where_clause = None
        if label_type:
            where_clause = {"label": {"$eq": label_type.upper()}}

        results = _chroma_client.query(
            collection_name="words",
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where_clause,
            include=["metadatas", "documents", "distances"],
        )

        output: list[dict[str, Any]] = []
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for idx, (doc_id, doc, meta, dist) in enumerate(
            zip(ids, documents, metadatas, distances)
        ):
            similarity = max(0.0, 1.0 - (dist / 2))

            output.append(
                {
                    "rank": idx + 1,
                    "chroma_id": doc_id,
                    "word_text": doc,
                    "similarity_score": round(similarity, 4),
                    "image_id": meta.get("image_id"),
                    "receipt_id": meta.get("receipt_id"),
                    "line_id": meta.get("line_id"),
                    "word_id": meta.get("word_id"),
                    "label": meta.get("label"),
                    "validation_status": meta.get("validation_status"),
                }
            )

        return output

    except Exception as e:
        logger.error("Error querying ChromaDB words: %s", e)
        return [{"error": str(e)}]


@tool(args_schema=SearchByMerchantInput)
def search_by_merchant_name(
    merchant_name: str,
    n_results: int = 20,
    # Injected at runtime
    _chroma_client: Any = None,
    _embed_fn: Any = None,
) -> dict[str, Any]:
    """
    Find all receipts from a specific merchant in ChromaDB.

    Use this tool to discover all receipts associated with a merchant.
    Returns summary statistics about the merchant including:
    - Number of receipts found
    - Common addresses and phone numbers
    - Place IDs associated with this merchant

    This helps validate merchant consistency and identify canonical data.
    """
    if _chroma_client is None:
        return {"error": "ChromaDB client not configured"}

    try:
        if _embed_fn is None:
            return {"error": "Embedding function not configured"}

        # Search by embedding the merchant name
        query_embedding = _embed_fn([merchant_name])[0]

        # Filter by merchant name
        where_clause = {
            "merchant_name": {"$eq": merchant_name.strip().title()}
        }

        results = _chroma_client.query(
            collection_name="lines",
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where_clause,
            include=["metadatas"],
        )

        metadatas = results.get("metadatas", [[]])[0]

        # Aggregate results
        receipts_found: set[tuple[str, int]] = set()
        addresses: dict[str, int] = {}
        phones: dict[str, int] = {}
        place_ids: dict[str, int] = {}

        for meta in metadatas:
            img_id = meta.get("image_id")
            rcpt_id = meta.get("receipt_id")
            if img_id and rcpt_id:
                receipts_found.add((img_id, int(rcpt_id)))

            addr = meta.get("normalized_full_address")
            if addr:
                addresses[addr] = addresses.get(addr, 0) + 1

            phone = meta.get("normalized_phone_10")
            if phone:
                phones[phone] = phones.get(phone, 0) + 1

            pid = meta.get("place_id")
            if pid:
                place_ids[pid] = place_ids.get(pid, 0) + 1

        return {
            "merchant_name": merchant_name,
            "receipt_count": len(receipts_found),
            "receipts": [
                {"image_id": r[0], "receipt_id": r[1]}
                for r in list(receipts_found)[:10]  # Limit to 10
            ],
            "addresses": dict(
                sorted(addresses.items(), key=lambda x: -x[1])[:5]
            ),
            "phone_numbers": dict(
                sorted(phones.items(), key=lambda x: -x[1])[:5]
            ),
            "place_ids": dict(
                sorted(place_ids.items(), key=lambda x: -x[1])[:3]
            ),
        }

    except Exception as e:
        logger.error("Error searching by merchant: %s", e)
        return {"error": str(e)}


@tool(args_schema=SearchByPlaceIdInput)
def search_by_place_id(
    place_id: str,
    # Injected at runtime
    _chroma_client: Any = None,
) -> dict[str, Any]:
    """
    Find all receipts associated with a Google Place ID.

    Use this tool to verify that a Place ID is consistently used
    across receipts and to find the canonical merchant data for
    a specific location.

    Returns all receipts with this Place ID and their merchant names.
    """
    if _chroma_client is None:
        return {"error": "ChromaDB client not configured"}

    try:
        # Query with where filter on place_id
        where_clause = {"place_id": {"$eq": place_id}}

        results = _chroma_client.get(
            collection_name="lines",
            where=where_clause,
            include=["metadatas"],
            limit=100,
        )

        metadatas = results.get("metadatas", [])

        # Aggregate
        merchant_names: dict[str, int] = {}
        receipts: set[tuple[str, int]] = set()

        for meta in metadatas:
            name = meta.get("merchant_name")
            if name:
                merchant_names[name] = merchant_names.get(name, 0) + 1

            img_id = meta.get("image_id")
            rcpt_id = meta.get("receipt_id")
            if img_id and rcpt_id:
                receipts.add((img_id, int(rcpt_id)))

        # Determine canonical name (most common)
        canonical_name = max(
            merchant_names.items(), key=lambda x: x[1], default=(None, 0)
        )[0]

        return {
            "place_id": place_id,
            "canonical_merchant_name": canonical_name,
            "merchant_name_variants": merchant_names,
            "receipt_count": len(receipts),
            "receipts": [
                {"image_id": r[0], "receipt_id": r[1]}
                for r in list(receipts)[:10]
            ],
        }

    except Exception as e:
        logger.error("Error searching by place_id: %s", e)
        return {"error": str(e)}
