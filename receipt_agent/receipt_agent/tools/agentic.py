"""
Guardrailed agentic tools for receipt metadata validation.

These tools are designed to be used by a ReAct agent with full autonomy
while enforcing constraints on how ChromaDB can be queried.

Guard Rails:
- Tools construct record IDs internally (agent can't make arbitrary queries)
- Collections are hardcoded ("lines", "words")
- Current receipt is automatically excluded from search results
- Result counts are capped
- Decision tool enforces valid status values
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

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
        description="Key findings that support the decision"
    )


# ==============================================================================
# Tool Factory - Creates tools with injected dependencies
# ==============================================================================


def create_agentic_tools(
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
    places_api: Optional[Any] = None,
) -> tuple[list[Any], dict]:
    """
    Create guardrailed tools for the agentic validator.

    Returns:
        (tools, state_holder) - tools list and a dict to hold runtime state
    """
    # State holder - will be populated before each validation
    state = {"context": None, "decision": None}

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
                logger.error(f"Error loading lines: {e}")
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
                logger.error(f"Error loading words: {e}")
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
            logger.error(f"Error loading lines for receipt text: {exc}")
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
                logger.error(f"Error loading metadata: {e}")
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

            for doc_id, doc, meta, dist in zip(
                ids, documents, metadatas, distances
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
            logger.error(f"Error in similarity search: {e}")
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

            for doc_id, doc, meta, dist in zip(
                ids, documents, metadatas, distances
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
            logger.error(f"Error in word similarity search: {e}")
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

            for doc_id, doc, meta, dist in zip(
                ids, documents, metadatas, distances
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
            logger.error(f"Error in search_lines: {e}")
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

            for doc_id, doc, meta, dist in zip(
                ids, documents, metadatas, distances
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
            logger.error(f"Error in search_words: {e}")
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
            logger.error(f"Error in get_merchant_consensus: {e}")
            return {"error": str(e)}

    @tool(args_schema=GetPlaceIdInfoInput)
    def get_place_id_info(place_id: str) -> dict:
        """
        Get information about a Google Place ID.

        Returns all receipts associated with this Place ID and their metadata.
        Use this to verify a Place ID is legitimate and consistently used.
        """
        try:
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

            # Aggregate
            receipts = set()
            merchant_names: dict[str, int] = {}
            addresses: dict[str, int] = {}

            for meta in metadatas:
                img_id = meta.get("image_id")
                rcpt_id = meta.get("receipt_id")
                if img_id and rcpt_id:
                    receipts.add((img_id, int(rcpt_id)))

                name = meta.get("merchant_name")
                if name:
                    merchant_names[name] = merchant_names.get(name, 0) + 1

                addr = meta.get("normalized_full_address")
                if addr:
                    addresses[addr] = addresses.get(addr, 0) + 1

            canonical_name = (
                max(merchant_names.items(), key=lambda x: x[1])[0]
                if merchant_names
                else None
            )

            return {
                "place_id": place_id,
                "receipt_count": len(receipts),
                "canonical_merchant_name": canonical_name,
                "merchant_name_variants": merchant_names,
                "addresses": dict(
                    sorted(addresses.items(), key=lambda x: -x[1])[:3]
                ),
            }

        except Exception as e:
            logger.error(f"Error in get_place_id_info: {e}")
            return {"error": str(e)}

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
            # Get my metadata
            my_meta = get_my_metadata()
            if "error" in my_meta:
                return my_meta

            # Get other receipt's metadata
            other_meta = dynamo_client.get_receipt_metadata(
                image_id=other_image_id,
                receipt_id=other_receipt_id,
            )

            if not other_meta:
                return {
                    "error": f"No metadata found for {other_image_id}#{other_receipt_id}"
                }

            # Compare
            differences = []

            same_merchant = (
                my_meta.get("merchant_name") == other_meta.merchant_name
            )
            if not same_merchant:
                differences.append(
                    f"Merchant: '{my_meta.get('merchant_name')}' vs '{other_meta.merchant_name}'"
                )

            same_place_id = my_meta.get("place_id") == other_meta.place_id
            if not same_place_id:
                differences.append(
                    f"Place ID: '{my_meta.get('place_id')}' vs '{other_meta.place_id}'"
                )

            same_address = my_meta.get("address") == other_meta.address
            if not same_address:
                differences.append(
                    f"Address: '{my_meta.get('address')}' vs '{other_meta.address}'"
                )

            same_phone = my_meta.get("phone") == other_meta.phone_number
            if not same_phone:
                differences.append(
                    f"Phone: '{my_meta.get('phone')}' vs '{other_meta.phone_number}'"
                )

            return {
                "compared_with": f"{other_image_id}#{other_receipt_id}",
                "same_merchant": same_merchant,
                "same_place_id": same_place_id,
                "same_address": same_address,
                "same_phone": same_phone,
                "all_match": same_merchant
                and same_place_id
                and same_address
                and same_phone,
                "differences": differences,
                "other_metadata": {
                    "merchant_name": other_meta.merchant_name,
                    "place_id": other_meta.place_id,
                    "address": other_meta.address,
                    "phone": other_meta.phone_number,
                },
            }

        except Exception as e:
            logger.error(f"Error in compare_with_receipt: {e}")
            return {"error": str(e)}

    # ========== GOOGLE PLACES TOOL ==========

    class VerifyWithPlacesInput(BaseModel):
        """Input schema for verify_with_google_places tool."""

        place_id: Optional[str] = Field(
            default=None, description="Google Place ID to verify directly"
        )
        phone_number: Optional[str] = Field(
            default=None, description="Phone number to search for"
        )
        address: Optional[str] = Field(
            default=None, description="Address to search for"
        )
        merchant_name: Optional[str] = Field(
            default=None, description="Merchant name for text search"
        )

    @tool(args_schema=VerifyWithPlacesInput)
    def verify_with_google_places(
        place_id: Optional[str] = None,
        phone_number: Optional[str] = None,
        address: Optional[str] = None,
        merchant_name: Optional[str] = None,
    ) -> dict:
        """
        Search Google Places API to find or verify a place_id.

        This is the PRIMARY way to find place_ids for receipts that don't have them.
        Use this instead of just copying place_ids from similar receipts.

        Search priority:
        1. phone_number: Most reliable if available
        2. address: Good if you have a complete address
        3. merchant_name: Text search as fallback
        4. place_id: Verify an existing place_id

        Returns:
        - found: Whether a place was found
        - place_id: Google Place ID
        - place_name: Official business name
        - place_address: Formatted address
        - place_phone: Phone number
        - search_method: Which method found it (phone/address/text/place_id)

        IMPORTANT: Always use this tool to search Google Places API, don't just copy
        place_ids from get_merchant_consensus. This ensures you're finding the correct
        place_id for THIS specific receipt location.
        """
        if places_api is None:
            return {
                "error": "Google Places API not configured",
                "found": False,
            }

        try:
            result: dict[str, Any] = {
                "search_method": None,
                "found": False,
                "place": None,
            }

            # Try place_id first (most reliable, direct lookup)
            if place_id:
                result["search_method"] = "place_id"
                logger.info(
                    "🔍 Places lookup by place_id: %s...", place_id[:20]
                )
                place_data = places_api.get_place_details(place_id)
                if place_data and place_data.get("name"):
                    result["found"] = True
                    place = place_data.get("place", place_data)
                    result["place_id"] = place.get("place_id")
                    result["place_name"] = place.get("name")
                    result["place_address"] = place.get("formatted_address")
                    result["place_phone"] = place.get(
                        "formatted_phone_number"
                    ) or place.get("international_phone_number")
                    result["place"] = {
                        "place_id": result["place_id"],
                        "name": result["place_name"],
                        "formatted_address": result["place_address"],
                        "phone_number": result["place_phone"],
                    }
                    return result

            # Try phone search (uses DynamoDB cache via PlacesClient)
            if phone_number and not result["found"]:
                result["search_method"] = "phone"
                logger.info(
                    "🔍 Places lookup by phone: %s (cached)", phone_number
                )
                place_data = places_api.search_by_phone(phone_number)
                if place_data and place_data.get("name"):
                    result["found"] = True
                    place = place_data.get("place", place_data)
                    result["place_id"] = place.get("place_id")
                    result["place_name"] = place.get("name")
                    result["place_address"] = place.get("formatted_address")
                    result["place_phone"] = place.get(
                        "formatted_phone_number"
                    ) or place.get("international_phone_number")
                    result["place"] = {
                        "place_id": result["place_id"],
                        "name": result["place_name"],
                        "formatted_address": result["place_address"],
                        "phone_number": result["place_phone"],
                    }
                    return result

            # Try address geocoding (uses DynamoDB cache via PlacesClient)
            if address and not result["found"]:
                result["search_method"] = "address"
                logger.info(
                    "🔍 Places lookup by address: %s... (cached)", address[:50]
                )
                place_data = places_api.search_by_address(address)
                if place_data and place_data.get("name"):
                    result["found"] = True
                    place = place_data.get("place", place_data)
                    result["place_id"] = place.get("place_id")
                    result["place_name"] = place.get("name")
                    result["place_address"] = place.get("formatted_address")
                    result["place_phone"] = place.get(
                        "formatted_phone_number"
                    ) or place.get("international_phone_number")
                    result["place"] = {
                        "place_id": result["place_id"],
                        "name": result["place_name"],
                        "formatted_address": result["place_address"],
                        "phone_number": result["place_phone"],
                    }
                    return result

            # Try text search with merchant name (not cached)
            if merchant_name and not result["found"]:
                result["search_method"] = "text_search"
                logger.info(
                    "🔍 Places text search: %s (NOT cached)", merchant_name
                )
                place_data = places_api.search_by_text(merchant_name)
                if place_data and place_data.get("name"):
                    result["found"] = True
                    place = place_data.get("place", place_data)
                    result["place_id"] = place.get("place_id")
                    result["place_name"] = place.get("name")
                    result["place_address"] = place.get("formatted_address")
                    result["place_phone"] = place.get(
                        "formatted_phone_number"
                    ) or place.get("international_phone_number")
                    result["place"] = {
                        "place_id": result["place_id"],
                        "name": result["place_name"],
                        "formatted_address": result["place_address"],
                        "phone_number": result["place_phone"],
                    }
                    return result

            result["message"] = "No matching business found in Google Places"
            return result

        except Exception as e:
            logger.error(f"Error in verify_with_google_places: {e}")
            return {
                "error": str(e),
                "found": False,
            }

    # ========== DECISION TOOL (terminates agent loop) ==========

    @tool(args_schema=SubmitDecisionInput)
    def submit_decision(
        status: Literal["VALIDATED", "INVALID", "NEEDS_REVIEW"],
        confidence: float,
        reasoning: str,
        evidence: list[str],
    ) -> dict:
        """
        Submit your final validation decision. THIS ENDS THE VALIDATION.

        Call this ONLY when you have gathered sufficient evidence.

        Args:
            status:
                - VALIDATED: The metadata is correct
                - INVALID: The metadata is incorrect
                - NEEDS_REVIEW: Uncertain, human review needed
            confidence: Your confidence (0.0 to 1.0)
            reasoning: Brief explanation of your decision
            evidence: List of key findings that support your decision

        Returns confirmation that the decision was recorded.
        """
        ctx: ReceiptContext = state["context"]

        # Store the decision
        state["decision"] = {
            "image_id": ctx.image_id if ctx else "unknown",
            "receipt_id": ctx.receipt_id if ctx else -1,
            "status": status,
            "confidence": confidence,
            "reasoning": reasoning,
            "evidence": evidence,
        }

        logger.info(
            f"Decision submitted: {status} ({confidence:.2%}) - {reasoning[:100]}..."
        )

        return {
            "success": True,
            "decision": state["decision"],
            "message": "Validation complete. Decision recorded.",
        }

    # Return all tools and state holder
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
        submit_decision,
    ]

    # Add Google Places tools if available
    if places_api is not None:
        from receipt_agent.tools.places import (
            FindBusinessesAtAddressInput,
            _format_place_result,
        )

        tools.append(verify_with_google_places)

        # Create a wrapper for find_businesses_at_address that has access to places_api
        @tool(args_schema=FindBusinessesAtAddressInput)
        def find_businesses_at_address_wrapper(address: str) -> dict:
            """
            Find businesses at a specific address using Google Places API.

            Use this when Google Places returns an address as the merchant name
            (e.g., "166 W Hillcrest Dr" instead of a business name). This tool
            searches for actual businesses at that address so you can identify
            the correct merchant.
            """
            if not address:
                return {"error": "Address is required"}

            try:
                logger.info(
                    "🔍 Places search for businesses at address: %s...",
                    address[:50],
                )

                # First, geocode the address to get lat/lng
                geocode_result = places_api.search_by_address(address)
                if not geocode_result:
                    return {
                        "found": False,
                        "businesses": [],
                        "message": f"Could not geocode address: {address}",
                    }

                # Extract location from geocode result
                geometry = geocode_result.get("geometry", {})
                location = geometry.get("location", {})
                lat = location.get("lat")
                lng = location.get("lng")

                if not lat or not lng:
                    return {
                        "found": False,
                        "businesses": [],
                        "message": f"Could not get coordinates for address: {address}",
                    }

                # Now search for nearby businesses (within 50 meters of the address)
                nearby_businesses = places_api.search_nearby(
                    lat=lat,
                    lng=lng,
                    radius=50,  # 50 meters - very close to the address
                )

                if not nearby_businesses:
                    return {
                        "found": False,
                        "businesses": [],
                        "address_searched": address,
                        "coordinates": {"lat": lat, "lng": lng},
                        "message": f"No businesses found within 50m of address",
                    }

                # Format results
                businesses = []
                for business in nearby_businesses[:10]:  # Limit to 10
                    formatted = _format_place_result(business)
                    businesses.append(formatted)

                return {
                    "found": True,
                    "businesses": businesses,
                    "address_searched": address,
                    "coordinates": {"lat": lat, "lng": lng},
                    "count": len(businesses),
                    "message": f"Found {len(businesses)} business(es) at address",
                }

            except Exception as e:
                logger.error("Error finding businesses at address: %s", e)
                return {"error": str(e)}

        tools.append(find_businesses_at_address_wrapper)

    return tools, state
