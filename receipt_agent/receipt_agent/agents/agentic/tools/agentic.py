"""
Guardrailed agentic tools for receipt metadata validation.

These tools are designed to be used by a ReAct agent with full autonomy
while enforcing constraints on how ChromaDB can be queried.

Guard Rails:
- Tools construct record IDs internally (agent can't make arbitrary queries)
- Collections are hardcoded ("lines", "words")
- Current receipt is automatically excluded from search results (when context is set)
- Result counts are capped
- Decision tool enforces valid status values
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from receipt_agent.utils.chroma_helpers import load_dual_chroma_from_s3
from receipt_agent.utils.receipt_text import format_receipt_text_receipt_space

logger = logging.getLogger(__name__)


# ==============================================================================
# Receipt Context - Injected at runtime
# ==============================================================================


@dataclass
class ReceiptContext:
    """Context for the receipt being validated. Injected into tools at runtime."""

    image_id: str
    receipt_id: int

    # Cached data (loaded once)
    lines: Optional[list[dict]] = None
    words: Optional[list[dict]] = None
    metadata: Optional[dict] = None
    line_embeddings: Optional[dict[str, list[float]]] = None
    word_embeddings: Optional[dict[str, list[float]]] = None


def _build_line_id(image_id: str, receipt_id: int, line_id: int) -> str:
    """Build ChromaDB document ID for a line."""
    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"


def _build_word_id(
    image_id: str, receipt_id: int, line_id: int, word_id: int
) -> str:
    """Build ChromaDB document ID for a word."""
    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}"


# ==============================================================================
# Tool Input Schemas
# ==============================================================================


class FindSimilarToMyLineInput(BaseModel):
    """Input for find_similar_to_my_line tool."""

    line_id: int = Field(
        description="Line ID on this receipt to use for similarity search"
    )
    n_results: int = Field(
        default=10, ge=1, le=20, description="Number of results (max 20)"
    )


class FindSimilarToMyWordInput(BaseModel):
    """Input for find_similar_to_my_word tool."""

    line_id: int = Field(description="Line ID containing the word")
    word_id: int = Field(description="Word ID to use for similarity search")
    n_results: int = Field(
        default=10, ge=1, le=20, description="Number of results (max 20)"
    )


class SearchLinesInput(BaseModel):
    """Input for search_lines tool."""

    query: str = Field(
        description="Text to search for (address, phone, merchant name)"
    )
    n_results: int = Field(
        default=10, ge=1, le=20, description="Number of results (max 20)"
    )
    merchant_filter: Optional[str] = Field(
        default=None, description="Optionally filter by merchant"
    )


class SearchWordsInput(BaseModel):
    """Input for search_words tool."""

    query: str = Field(description="Word text to search for")
    label_filter: Optional[str] = Field(
        default=None,
        description="Filter by label: MERCHANT_NAME, PHONE, ADDRESS, TOTAL, etc.",
    )
    n_results: int = Field(
        default=10, ge=1, le=20, description="Number of results (max 20)"
    )


class GetMerchantConsensusInput(BaseModel):
    """Input for get_merchant_consensus tool."""

    merchant_name: str = Field(description="Merchant name to look up")


class GetPlaceIdInfoInput(BaseModel):
    """Input for get_place_id_info tool."""

    place_id: str = Field(description="Google Place ID to look up")


class CompareWithReceiptInput(BaseModel):
    """Input for compare_with_receipt tool."""

    other_image_id: str = Field(
        description="Image ID of receipt to compare with"
    )
    other_receipt_id: int = Field(description="Receipt ID to compare with")


class SubmitDecisionInput(BaseModel):
    """Input for submit_decision tool."""

    status: Literal["VALIDATED", "INVALID", "NEEDS_REVIEW"] = Field(
        description="Validation status: VALIDATED (correct), INVALID (wrong), NEEDS_REVIEW (uncertain)"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence score 0.0 to 1.0"
    )
    reasoning: str = Field(description="Brief explanation of the decision")
    evidence: list[str] = Field(
        default_factory=list,
        description="Key findings that support the decision",
    )


class VerifyWithGooglePlacesInput(BaseModel):
    """Input for verify_with_google_places tool."""

    merchant_name: str = Field(
        description="Merchant name to verify against Google Places"
    )
    address: Optional[str] = Field(
        default=None,
        description="Optional address to narrow the search",
    )
    phone: Optional[str] = Field(
        default=None,
        description="Optional phone number to narrow the search",
    )


class FindBusinessesAtAddressInput(BaseModel):
    """Input for find_businesses_at_address_wrapper tool."""

    address: str = Field(description="Address to search for businesses")


# ==============================================================================
# Tool Factory - Creates tools with injected dependencies
# ==============================================================================


def ensure_chroma_state(
    state: dict,
) -> tuple[Any, Callable[[list[str]], list[list[float]]]]:
    """
    Ensure chroma_client and embed_fn are loaded in state, lazy-loading if needed.

    Args:
        state: State dictionary that may contain chroma_client, embed_fn, chromadb_bucket

    Returns:
        Tuple of (chroma_client, embed_fn)

    Raises:
        RuntimeError: If chroma_client/embed_fn are not available and cannot be lazy-loaded
    """
    chroma_client = state.get("chroma_client")
    embed_fn = state.get("embed_fn")

    # If both are already loaded, return them
    if chroma_client and embed_fn:
        return chroma_client, embed_fn

    # Try to lazy-load if bucket is available
    chromadb_bucket = state.get("chromadb_bucket") or os.environ.get(
        "CHROMADB_BUCKET"
    )
    if not chromadb_bucket:
        raise RuntimeError(
            "ChromaDB client and embedding function are not available and "
            "cannot be lazy-loaded: chromadb_bucket is not configured. "
            "Either provide chroma_client and embed_fn when creating tools, "
            "or set chromadb_bucket for lazy loading."
        )

    try:
        logger.info(
            "Lazy-loading ChromaDB (dual collections) and embeddings..."
        )
        chroma_client, embed_fn = load_dual_chroma_from_s3(
            chromadb_bucket=chromadb_bucket,
            base_chroma_path=os.environ.get(
                "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY"
            ),
            verify_integrity=False,
        )
        # Cache in state for subsequent calls
        state["chroma_client"] = chroma_client
        state["embed_fn"] = embed_fn
        return chroma_client, embed_fn

    except Exception as e:
        raise RuntimeError(
            f"Failed to lazy-load ChromaDB client and embedding function: {e!s}. "
            "Ensure chromadb_bucket is configured correctly and accessible."
        ) from e


def create_agentic_tools(
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
    places_api: Optional[Any] = None,
    chromadb_bucket: Optional[str] = None,
) -> tuple[list[Any], dict]:
    """
    Create guardrailed tools for the agentic validator.

    Args:
        dynamo_client: DynamoDB client
        chroma_client: ChromaDB client (may be None, will be lazy-loaded via ensure_chroma_state if bucket provided)
        embed_fn: Embedding function (may be None, will be lazy-loaded via ensure_chroma_state if bucket provided)
        places_api: Optional Google Places API client
        chromadb_bucket: Optional S3 bucket name for lazy loading ChromaDB collections

    Returns:
        (tools, state_holder) - tools list and a dict to hold runtime state

    Note:
        Tools that use chroma_client or embed_fn will automatically lazy-load them
        via ensure_chroma_state() if they are None and chromadb_bucket is configured.
        If lazy loading fails, tools will return error responses.
    """
    # State holder - will be populated before each validation
    state = {
        "context": None,
        "decision": None,
        "chroma_client": chroma_client,  # Cache ChromaDB client
        "embed_fn": embed_fn,  # Cache embedding function
        "chromadb_bucket": chromadb_bucket,  # Store bucket for lazy loading
    }

    # ========== CONTEXT TOOLS ==========

    @tool
    def get_my_lines() -> list[dict]:
        """
        Get all lines from the receipt being validated.

        Returns a list of lines with:
        - line_id: Unique ID for this line
        - text: The text content of the line
        - has_embedding: Whether this line has a stored embedding

        Use this first to understand what content is on the receipt.
        """
        ctx: ReceiptContext = state["context"]
        if ctx is None:
            return [{"error": "No receipt context set"}]

        if ctx.lines is None:
            # Load lines from DynamoDB
            try:
                receipt_details = dynamo_client.get_receipt_details(
                    image_id=ctx.image_id,
                    receipt_id=ctx.receipt_id,
                )
                ctx.lines = [
                    {
                        "line_id": line.line_id,
                        "text": line.text,
                        "has_embedding": _build_line_id(
                            ctx.image_id, ctx.receipt_id, line.line_id
                        )
                        in (ctx.line_embeddings or {}),
                    }
                    for line in (receipt_details.lines or [])
                ]
            except Exception as e:
                logger.exception("Error loading lines")
                return [{"error": str(e)}]

        return ctx.lines

    @tool
    def get_my_words() -> list[dict]:
        """
        Get all labeled words from the receipt being validated.

        Returns a list of words with:
        - line_id: Line containing this word
        - word_id: Unique ID for this word within the line
        - text: The word text
        - label: The assigned label (MERCHANT_NAME, PHONE, ADDRESS, TOTAL, etc.)

        Use this to see what entities were extracted from the receipt.
        """
        ctx: ReceiptContext = state["context"]
        if ctx is None:
            return [{"error": "No receipt context set"}]

        if ctx.words is None:
            # Load words from DynamoDB
            try:
                receipt_details = dynamo_client.get_receipt_details(
                    image_id=ctx.image_id,
                    receipt_id=ctx.receipt_id,
                )
                ctx.words = [
                    {
                        "line_id": word.line_id,
                        "word_id": word.word_id,
                        "text": word.text,
                        "label": getattr(word, "label", None),
                    }
                    for word in (receipt_details.words or [])
                ]
            except Exception as e:
                logger.exception("Error loading words")
                return [{"error": str(e)}]

        return ctx.words

    @tool
    def get_receipt_text() -> dict:
        """
        Get formatted receipt text in receipt space (no image warp).

        Groups visually contiguous lines (centroid overlap) into rows and
        returns merged text with line breaks.
        """
        ctx: ReceiptContext = state["context"]
        if ctx is None:
            return {"error": "No receipt context set"}

        try:
            receipt_details = dynamo_client.get_receipt_details(
                image_id=ctx.image_id,
                receipt_id=ctx.receipt_id,
            )
            lines = receipt_details.lines or []
        except Exception as exc:
            logger.exception("Error loading lines for receipt text")
            return {"error": str(exc)}

        formatted = format_receipt_text_receipt_space(lines)
        return {"formatted_text": formatted, "line_count": len(lines)}

    @tool
    def get_my_metadata() -> dict:
        """
        Get the current metadata stored for this receipt.

        Returns:
        - merchant_name: Current merchant name
        - place_id: Google Place ID
        - address: Stored address
        - phone: Stored phone number
        - validation_status: Current validation status

        This is the metadata you need to validate.
        """
        ctx: ReceiptContext = state["context"]
        if ctx is None:
            return {"error": "No receipt context set"}

        if ctx.metadata is None:
            # Try receipt_place first (new entity)
            try:
                place = dynamo_client.get_receipt_place(
                    image_id=ctx.image_id,
                    receipt_id=ctx.receipt_id,
                )
                if place:
                    ctx.metadata = {
                        "merchant_name": place.merchant_name,
                        "place_id": place.place_id,
                        "address": place.formatted_address,
                        "phone": place.phone_number,
                        "validation_status": place.validation_status,
                    }
            except Exception as e:
                logger.debug("No receipt_place found: %s", e)

            # Fallback to legacy receipt_metadata if no place found
            if ctx.metadata is None:
                try:
                    metadata = dynamo_client.get_receipt_metadata(
                        image_id=ctx.image_id,
                        receipt_id=ctx.receipt_id,
                    )
                    if metadata:
                        ctx.metadata = {
                            "merchant_name": metadata.merchant_name,
                            "place_id": metadata.place_id,
                            "address": metadata.address,
                            "phone": metadata.phone_number,
                            "validation_status": metadata.validation_status,
                        }
                    else:
                        ctx.metadata = {
                            "error": "No metadata found for this receipt"
                        }
                except Exception as e:
                    logger.exception("Error loading metadata")
                    ctx.metadata = {"error": str(e)}

        return ctx.metadata

    # ========== SIMILARITY SEARCH TOOLS (using MY embeddings) ==========

    @tool(args_schema=FindSimilarToMyLineInput)
    def find_similar_to_my_line(
        line_id: int, n_results: int = 10
    ) -> list[dict]:
        """
        Find lines on OTHER receipts similar to one of YOUR lines.

        Uses the stored embedding for your line to search ChromaDB.
        Automatically excludes your receipt from results.

        Args:
            line_id: Which of your lines to use (from get_my_lines)
            n_results: How many results to return (max 20)

        Returns similar lines with:
        - image_id, receipt_id: Which receipt this line is from
        - text: The line text
        - similarity: How similar (0.0 to 1.0)
        - merchant_name, address, phone: Metadata from that receipt
        """
        ctx: ReceiptContext = state["context"]
        if ctx is None:
            return [{"error": "No receipt context set"}]

        # Ensure ChromaDB is loaded
        try:
            chroma_client, _ = ensure_chroma_state(state)
        except RuntimeError as e:
            return [{"error": str(e)}]

        # Build the record ID for this line
        doc_id = _build_line_id(ctx.image_id, ctx.receipt_id, line_id)

        # Get the embedding for this line
        if ctx.line_embeddings and doc_id in ctx.line_embeddings:
            query_embedding = ctx.line_embeddings[doc_id]
        else:
            # Try to fetch from ChromaDB
            try:
                result = chroma_client.get(
                    collection_name="lines",
                    ids=[doc_id],
                    include=["embeddings"],
                )
                if result.get("embeddings") and len(result["embeddings"]) > 0:
                    query_embedding = result["embeddings"][0]
                else:
                    return [
                        {"error": f"No embedding found for line {line_id}"}
                    ]
            except Exception as e:
                logger.exception(
                    "Error getting embedding for line_id=%s doc_id=%s",
                    line_id,
                    doc_id,
                )
                return [{"error": f"Could not get embedding: {e}"}]

        # Search for similar lines
        try:
            results = chroma_client.query(
                collection_name="lines",
                query_embeddings=[query_embedding],
                n_results=n_results + 10,  # Get extra to filter out self
                include=["metadatas", "documents", "distances"],
            )

            output = []
            ids = results.get("ids", [[]])[0]
            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for _doc_id, doc, meta, dist in zip(
                ids, documents, metadatas, distances, strict=True
            ):
                # Skip if same receipt
                if (
                    meta.get("image_id") == ctx.image_id
                    and int(meta.get("receipt_id", -1)) == ctx.receipt_id
                ):
                    continue

                similarity = max(0.0, 1.0 - (dist / 2))

                output.append(
                    {
                        "image_id": meta.get("image_id"),
                        "receipt_id": meta.get("receipt_id"),
                        "text": doc,
                        "similarity": round(similarity, 4),
                        "merchant_name": meta.get("merchant_name"),
                        "address": meta.get("normalized_full_address"),
                        "phone": meta.get("normalized_phone_10"),
                        "place_id": meta.get("place_id"),
                    }
                )

                if len(output) >= n_results:
                    break

            logger.info(
                f"find_similar_to_my_line({line_id}) returned {len(output)} results"
            )
            return output

        except Exception as e:
            logger.exception("Error in similarity search")
            return [{"error": str(e)}]

    @tool(args_schema=FindSimilarToMyWordInput)
    def find_similar_to_my_word(
        line_id: int, word_id: int, n_results: int = 10
    ) -> list[dict]:
        """
        Find words on OTHER receipts similar to one of YOUR words.

        Uses the stored embedding for your word to search ChromaDB.
        Automatically excludes your receipt from results.

        Args:
            line_id: Line containing the word
            word_id: Which word to use (from get_my_words)
            n_results: How many results to return (max 20)

        Returns similar words with:
        - image_id, receipt_id: Which receipt
        - text: The word text
        - label: What label it has
        - similarity: How similar (0.0 to 1.0)
        """
        ctx: ReceiptContext = state["context"]
        if ctx is None:
            return [{"error": "No receipt context set"}]

        # Ensure ChromaDB is loaded
        try:
            chroma_client, _ = ensure_chroma_state(state)
        except RuntimeError as e:
            return [{"error": str(e)}]

        # Build the record ID for this word
        doc_id = _build_word_id(ctx.image_id, ctx.receipt_id, line_id, word_id)

        # Get the embedding for this word
        if ctx.word_embeddings and doc_id in ctx.word_embeddings:
            query_embedding = ctx.word_embeddings[doc_id]
        else:
            # Try to fetch from ChromaDB
            try:
                result = chroma_client.get(
                    collection_name="words",
                    ids=[doc_id],
                    include=["embeddings"],
                )
                if result.get("embeddings") and len(result["embeddings"]) > 0:
                    query_embedding = result["embeddings"][0]
                else:
                    return [
                        {
                            "error": f"No embedding found for word {line_id}/{word_id}"
                        }
                    ]
            except Exception as e:
                logger.exception(
                    "Error getting embedding for word line_id=%s word_id=%s doc_id=%s",
                    line_id,
                    word_id,
                    doc_id,
                )
                return [{"error": f"Could not get embedding: {e}"}]

        # Search for similar words
        try:
            results = chroma_client.query(
                collection_name="words",
                query_embeddings=[query_embedding],
                n_results=n_results + 10,
                include=["metadatas", "documents", "distances"],
            )

            output = []
            ids = results.get("ids", [[]])[0]
            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for _doc_id, doc, meta, dist in zip(
                ids, documents, metadatas, distances, strict=True
            ):
                # Skip if same receipt
                if (
                    meta.get("image_id") == ctx.image_id
                    and int(meta.get("receipt_id", -1)) == ctx.receipt_id
                ):
                    continue

                similarity = max(0.0, 1.0 - (dist / 2))

                output.append(
                    {
                        "image_id": meta.get("image_id"),
                        "receipt_id": meta.get("receipt_id"),
                        "text": doc,
                        "label": meta.get("label"),
                        "similarity": round(similarity, 4),
                    }
                )

                if len(output) >= n_results:
                    break

            logger.info(
                f"find_similar_to_my_word({line_id}, {word_id}) returned {len(output)} results"
            )
            return output

        except Exception as e:
            logger.exception("Error in word similarity search")
            return [{"error": str(e)}]

    # ========== TEXT SEARCH TOOLS (generate new embedding) ==========

    @tool(args_schema=SearchLinesInput)
    def search_lines(
        query: str, n_results: int = 10, merchant_filter: Optional[str] = None
    ) -> list[dict]:
        """
        Search for lines similar to arbitrary text.

        Generates a new embedding for the query text and searches ChromaDB.

        Args:
            query: Text to search for (e.g., "123 Main Street" or "510-555-1234")
            n_results: How many results (max 20)
            merchant_filter: Optionally limit results to a specific merchant

        Returns matching lines with merchant metadata.
        """
        ctx: ReceiptContext = state["context"]

        # Ensure ChromaDB is loaded
        try:
            chroma_client, embed_fn = ensure_chroma_state(state)
        except RuntimeError as e:
            return [{"error": str(e)}]

        try:
            # Generate embedding for query
            query_embedding = embed_fn([query])[0]

            # Build where clause
            where_clause = None
            if merchant_filter:
                where_clause = {
                    "merchant_name": {"$eq": merchant_filter.strip()}
                }

            results = chroma_client.query(
                collection_name="lines",
                query_embeddings=[query_embedding],
                n_results=n_results + 5,
                where=where_clause,
                include=["metadatas", "documents", "distances"],
            )

            output = []
            ids = results.get("ids", [[]])[0]
            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for _doc_id, doc, meta, dist in zip(
                ids, documents, metadatas, distances, strict=True
            ):
                # Skip if same receipt (if context is set)
                if ctx and (
                    meta.get("image_id") == ctx.image_id
                    and int(meta.get("receipt_id", -1)) == ctx.receipt_id
                ):
                    continue

                similarity = max(0.0, 1.0 - (dist / 2))

                output.append(
                    {
                        "image_id": meta.get("image_id"),
                        "receipt_id": meta.get("receipt_id"),
                        "text": doc,
                        "similarity": round(similarity, 4),
                        "merchant_name": meta.get("merchant_name"),
                        "address": meta.get("normalized_full_address"),
                        "phone": meta.get("normalized_phone_10"),
                    }
                )

                if len(output) >= n_results:
                    break

            logger.info(
                f"search_lines('{query[:30]}...') returned {len(output)} results"
            )
            return output

        except Exception as e:
            logger.exception("Error in search_lines")
            return [{"error": str(e)}]

    @tool(args_schema=SearchWordsInput)
    def search_words(
        query: str, label_filter: Optional[str] = None, n_results: int = 10
    ) -> list[dict]:
        """
        Search for words similar to arbitrary text.

        Args:
            query: Word to search for
            label_filter: Filter by label (MERCHANT_NAME, PHONE, ADDRESS, TOTAL)
            n_results: How many results (max 20)

        Returns matching words with their labels.
        """
        ctx: ReceiptContext = state["context"]

        # Ensure ChromaDB is loaded
        try:
            chroma_client, embed_fn = ensure_chroma_state(state)
        except RuntimeError as e:
            return [{"error": str(e)}]

        try:
            query_embedding = embed_fn([query])[0]

            where_clause = None
            if label_filter:
                where_clause = {"label": {"$eq": label_filter.upper()}}

            results = chroma_client.query(
                collection_name="words",
                query_embeddings=[query_embedding],
                n_results=n_results + 5,
                where=where_clause,
                include=["metadatas", "documents", "distances"],
            )

            output = []
            ids = results.get("ids", [[]])[0]
            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for _doc_id, doc, meta, dist in zip(
                ids, documents, metadatas, distances, strict=True
            ):
                if ctx and (
                    meta.get("image_id") == ctx.image_id
                    and int(meta.get("receipt_id", -1)) == ctx.receipt_id
                ):
                    continue

                similarity = max(0.0, 1.0 - (dist / 2))

                output.append(
                    {
                        "image_id": meta.get("image_id"),
                        "receipt_id": meta.get("receipt_id"),
                        "text": doc,
                        "label": meta.get("label"),
                        "similarity": round(similarity, 4),
                    }
                )

                if len(output) >= n_results:
                    break

            return output

        except Exception as e:
            logger.exception("Error in search_words")
            return [{"error": str(e)}]

    # ========== AGGREGATION TOOLS ==========

    @tool(args_schema=GetMerchantConsensusInput)
    def get_merchant_consensus(merchant_name: str) -> dict:
        """
        Get the canonical data for a merchant based on all receipts.

        Looks up all receipts for this merchant and returns consensus data:
        - receipt_count: Total receipts found
        - most_common_place_id: The most frequently used Place ID
        - most_common_address: Most common address
        - most_common_phone: Most common phone
        - place_id_agreement: What % of receipts agree on place_id
        - address_agreement: What % agree on address
        - phone_agreement: What % agree on phone

        Use this to understand what the "correct" metadata should be.
        """
        try:
            metadatas, _ = dynamo_client.get_receipt_metadatas_by_merchant(
                merchant_name=merchant_name,
                limit=100,
            )

            if not metadatas:
                return {
                    "error": f"No receipts found for merchant '{merchant_name}'",
                    "receipt_count": 0,
                }

            # Aggregate
            place_ids = {}
            addresses = {}
            phones = {}

            for meta in metadatas:
                if meta.place_id:
                    place_ids[meta.place_id] = (
                        place_ids.get(meta.place_id, 0) + 1
                    )
                if meta.address:
                    addresses[meta.address] = (
                        addresses.get(meta.address, 0) + 1
                    )
                if meta.phone_number:
                    phones[meta.phone_number] = (
                        phones.get(meta.phone_number, 0) + 1
                    )

            total = len(metadatas)

            # Find most common
            most_common_place_id = (
                max(place_ids.items(), key=lambda x: x[1])[0]
                if place_ids
                else None
            )
            most_common_address = (
                max(addresses.items(), key=lambda x: x[1])[0]
                if addresses
                else None
            )
            most_common_phone = (
                max(phones.items(), key=lambda x: x[1])[0] if phones else None
            )

            return {
                "merchant_name": merchant_name,
                "receipt_count": total,
                "most_common_place_id": most_common_place_id,
                "most_common_address": most_common_address,
                "most_common_phone": most_common_phone,
                "place_id_agreement": (
                    round(place_ids.get(most_common_place_id, 0) / total, 3)
                    if most_common_place_id
                    else 0
                ),
                "address_agreement": (
                    round(addresses.get(most_common_address, 0) / total, 3)
                    if most_common_address
                    else 0
                ),
                "phone_agreement": (
                    round(phones.get(most_common_phone, 0) / total, 3)
                    if most_common_phone
                    else 0
                ),
                "all_place_ids": dict(
                    sorted(place_ids.items(), key=lambda x: -x[1])[:5]
                ),
                "all_addresses": dict(
                    sorted(addresses.items(), key=lambda x: -x[1])[:3]
                ),
                "all_phones": dict(
                    sorted(phones.items(), key=lambda x: -x[1])[:3]
                ),
            }

        except Exception as e:
            logger.exception("Error in get_merchant_consensus")
            return {"error": str(e)}

    @tool(args_schema=GetPlaceIdInfoInput)
    def get_place_id_info(place_id: str) -> dict:
        """
        Get information about a Google Place ID.

        Returns all receipts associated with this Place ID and their metadata.
        Use this to verify a Place ID is legitimate and consistently used.
        """
        try:
            # Ensure ChromaDB is loaded
            try:
                chroma_client, embed_fn = ensure_chroma_state(state)
            except RuntimeError as e:
                return {
                    "error": str(e),
                    "place_id": place_id,
                    "receipt_count": 0,
                    "message": "ChromaDB not available",
                }

            # Verify collection exists before querying
            try:
                collection = chroma_client.get_collection("lines")
                logger.debug(
                    f"Found 'lines' collection with {collection.count()} vectors"
                )
            except Exception as e:
                error_str = str(e)
                if (
                    "not found" in error_str.lower()
                    or "does not exist" in error_str.lower()
                ):
                    # Collection not found - this shouldn't happen if ensure_chroma_state succeeded
                    # Return error since ensure_chroma_state should have loaded everything
                    logger.exception(
                        "'lines' collection not found in ChromaDB even after lazy loading"
                    )
                    return {
                        "error": "Collection 'lines' not found in ChromaDB",
                        "place_id": place_id,
                        "receipt_count": 0,
                        "message": "ChromaDB 'lines' collection not available",
                    }
                else:
                    raise  # Re-raise if it's a different error

            # Query ChromaDB for lines with this place_id
            # We need to use query() with an embedding and a where filter
            # Generate a dummy embedding to satisfy the query requirement
            dummy_embedding = embed_fn(["place id lookup"])[0]

            results = chroma_client.query(
                collection_name="lines",
                query_embeddings=[dummy_embedding],
                n_results=100,
                where={"place_id": {"$eq": place_id}},
                include=["metadatas"],
            )

            # Query results are nested in lists
            metadatas = (
                results.get("metadatas", [[]])[0]
                if results.get("metadatas")
                else []
            )

            if not metadatas:
                return {
                    "place_id": place_id,
                    "receipt_count": 0,
                    "message": "No receipts found with this Place ID",
                }

            # Extract unique receipts
            receipt_set = set()
            for meta in metadatas:
                image_id = meta.get("image_id")
                receipt_id = meta.get("receipt_id")
                if image_id and receipt_id:
                    receipt_set.add((image_id, int(receipt_id)))

            receipt_count = len(receipt_set)

            return {
                "place_id": place_id,
                "receipt_count": receipt_count,
                "message": f"Found {receipt_count} receipt(s) with this Place ID",
            }

        except Exception as e:
            logger.exception("Error in get_place_id_info")
            return {
                "error": f"{e!s}",
                "place_id": place_id,
                "receipt_count": 0,
                "message": "Error querying ChromaDB",
            }

    # ========== COMPARISON TOOL ==========

    @tool(args_schema=CompareWithReceiptInput)
    def compare_with_receipt(
        other_image_id: str, other_receipt_id: int
    ) -> dict:
        """
        Compare your receipt with another specific receipt.

        Returns a detailed comparison:
        - same_merchant: Do they have the same merchant name?
        - same_place_id: Same Google Place ID?
        - same_address: Same address?
        - same_phone: Same phone?
        - differences: List of specific differences

        Use this for detailed comparison with a promising match.
        """
        ctx: ReceiptContext = state["context"]
        if ctx is None:
            return {"error": "No receipt context set"}

        try:
            # Ensure my_metadata is a proper entity with attribute access
            # ctx.metadata may be None or a dict (from get_my_metadata)
            if ctx.metadata is None or isinstance(ctx.metadata, dict):
                # Fetch as proper entity for attribute access
                my_metadata = dynamo_client.get_receipt_metadata(
                    image_id=ctx.image_id,
                    receipt_id=ctx.receipt_id,
                )
                # Cache the entity for subsequent accesses
                ctx.metadata = my_metadata
            else:
                # Already a proper entity
                my_metadata = ctx.metadata

            if not my_metadata:
                return {
                    "error": f"Receipt {ctx.image_id}#{ctx.receipt_id} metadata not found"
                }

            # Get metadata for the other receipt
            other_metadata = dynamo_client.get_receipt_metadata(
                image_id=other_image_id, receipt_id=other_receipt_id
            )

            if not other_metadata:
                return {
                    "error": f"Receipt {other_image_id}#{other_receipt_id} not found"
                }

            # Compare
            differences = []
            same_merchant = (
                my_metadata.merchant_name == other_metadata.merchant_name
            )
            if not same_merchant:
                differences.append(
                    f"Merchant: '{my_metadata.merchant_name}' vs '{other_metadata.merchant_name}'"
                )

            same_place_id = my_metadata.place_id == other_metadata.place_id
            if not same_place_id:
                differences.append(
                    f"Place ID: '{my_metadata.place_id}' vs '{other_metadata.place_id}'"
                )

            same_address = my_metadata.address == other_metadata.address
            if not same_address:
                differences.append(
                    f"Address: '{my_metadata.address}' vs '{other_metadata.address}'"
                )

            same_phone = (
                my_metadata.phone_number == other_metadata.phone_number
            )
            if not same_phone:
                differences.append(
                    f"Phone: '{my_metadata.phone_number}' vs '{other_metadata.phone_number}'"
                )

            return {
                "same_merchant": same_merchant,
                "same_place_id": same_place_id,
                "same_address": same_address,
                "same_phone": same_phone,
                "differences": differences,
            }

        except Exception as e:
            logger.exception("Error in compare_with_receipt")
            return {"error": f"{e!s}"}

    # ========== GOOGLE PLACES TOOLS ==========

    @tool(args_schema=VerifyWithGooglePlacesInput)
    def verify_with_google_places(
        merchant_name: str,
        address: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> dict:
        """
        Verify merchant information against Google Places API.

        Returns:
        - found: Whether a matching business was found
        - place_id: Google Place ID if found
        - confidence: How confident the match is (0.0 to 1.0)
        - message: Human-readable result

        Use this to validate merchant metadata against Google's database.
        """
        if not places_api:
            return {
                "error": "Google Places API not configured",
                "found": False,
                "place_id": None,
                "confidence": 0.0,
            }

        try:
            # PlacesClient does not expose a generic search_places API; call the
            # concrete helpers it provides (phone -> address -> text search).
            result = None

            if phone and hasattr(places_api, "search_by_phone"):
                result = places_api.search_by_phone(phone)

            if (
                not result
                and address
                and hasattr(places_api, "search_by_address")
            ):
                result = places_api.search_by_address(address)

            if not result and hasattr(places_api, "search_by_text"):
                result = places_api.search_by_text(merchant_name)

            if not result:
                return {
                    "found": False,
                    "place_id": None,
                    "confidence": 0.0,
                    "message": "No matching business found in Google Places",
                }

            top = result
            return {
                "found": True,
                "place_id": top.get("place_id"),
                "confidence": (
                    top.get("rating", 0) / 5.0 if top.get("rating") else 0.5
                ),
                "message": f"Found {top.get('name', 'business')} via Google Places",
            }

        except Exception as e:
            logger.exception("Error in verify_with_google_places")
            return {
                "error": f"{e!s}",
                "found": False,
                "place_id": None,
                "confidence": 0.0,
            }

    # ========== DECISION TOOL (terminates agent loop) ==========

    @tool(args_schema=SubmitDecisionInput)
    def submit_decision(
        status: Literal["VALIDATED", "INVALID", "NEEDS_REVIEW"],
        reasoning: str,
        confidence: float = 0.0,
        evidence: list[str] | None = None,
    ) -> dict:
        """
        Submit your final decision about this receipt's metadata.

        This ends the validation workflow. Call this when you've made a decision.

        Args:
            status: VALIDATED (metadata is correct), INVALID (metadata is wrong),
                   or NEEDS_REVIEW (uncertain, needs human review)
            reasoning: Your explanation for this decision
            confidence: How confident you are (0.0 to 1.0)
            evidence: Key findings that support the decision

        Returns:
            Confirmation of your decision
        """
        if evidence is None:
            evidence = []
        state["decision"] = {
            "status": status,
            "reasoning": reasoning,
            "confidence": confidence,
            "evidence": evidence,
        }
        return {
            "status": "submitted",
            "message": f"Decision submitted: {status}",
        }

    # Add Google Places tools if API is available
    tools = [
        get_my_lines,
        get_my_words,
        get_receipt_text,
        get_my_metadata,
        find_similar_to_my_line,
        find_similar_to_my_word,
        search_lines,
        search_words,
        get_merchant_consensus,
        get_place_id_info,
        compare_with_receipt,
        verify_with_google_places,
        submit_decision,
    ]

    if places_api:
        # Add find_businesses_at_address tool
        @tool(args_schema=FindBusinessesAtAddressInput)
        def find_businesses_at_address_wrapper(address: str) -> dict:
            """
            Find businesses at a specific address using Google Places.

            Returns businesses found at that address with their place_ids.
            Use this when you have an address but need to find the business.
            """
            try:
                # Geocode address to get coordinates
                geocode_result = places_api.geocode_address(address)
                if not geocode_result:
                    return {
                        "error": f"Could not geocode address: {address}",
                        "address": address,
                        "count": 0,
                    }

                lat = geocode_result["lat"]
                lng = geocode_result["lng"]

                # Search for businesses near those coordinates
                businesses = places_api.search_nearby(
                    lat=lat,
                    lng=lng,
                    radius=50,
                )

                return {
                    "address": address,
                    "coordinates": {"lat": lat, "lng": lng},
                    "count": len(businesses),
                    "businesses": businesses,
                    "place_ids": [
                        b.get("place_id")
                        for b in businesses
                        if isinstance(b, dict)
                    ],
                    "message": f"Found {len(businesses)} business(es) at address",
                }

            except Exception as e:
                logger.exception("Error finding businesses at address")
                return {"error": f"{e!s}"}

        tools.append(find_businesses_at_address_wrapper)

    return tools, state
