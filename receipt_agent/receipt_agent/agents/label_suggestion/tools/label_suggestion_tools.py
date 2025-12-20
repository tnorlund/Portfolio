"""
Tools for label suggestion agent.

These tools allow the agent to find unlabeled words and suggest labels
using ChromaDB similarity search, minimizing LLM calls.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from receipt_chroma.embedding.formatting.word_format import (
    format_word_context_embedding_input,
)

logger = logging.getLogger(__name__)


# =====================================================================
# Receipt Context - Injected at runtime
# =====================================================================

@dataclass
class ReceiptContext:
    """Context for the receipt being processed.
    Injected into tools at runtime.
    """
    image_id: str
    receipt_id: int
    merchant_name: Optional[str] = None
    merchant_receipt_count: Optional[int] = None
    # Number of receipts for this merchant


def _build_word_id(
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
) -> str:
    """Build ChromaDB document ID for a word."""
    return (
        f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#"
        f"{word_id:05d}"
    )


# =====================================================================
# Tool Input Schemas
# =====================================================================

class SearchLabelCandidatesInput(BaseModel):
    """Input for search_label_candidates tool."""
    word_id: int = Field(description="Word ID to find label candidates for")
    line_id: int = Field(description="Line ID containing the word")
    n_results: int = Field(
        default=50,
        ge=10,
        le=100,
        description="Number of ChromaDB results to fetch "
        "(more = better statistics)",
    )


class SubmitLabelSuggestionsInput(BaseModel):
    """Input for submit_label_suggestions tool."""
    suggestions: list[dict] = Field(
        description=(
            "List of label suggestions. Each dict should have: "
            "word_id, line_id, label_type, confidence, reasoning"
        )
    )


# =====================================================================
# Tool Factory
# =====================================================================

def create_label_suggestion_tools(
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
) -> tuple[list[Any], dict]:
    """
    Create tools for the label suggestion agent.

    Returns:
        (tools, state_holder) - tools list and a dict to hold runtime state
    """
    # State holder - will be populated before each suggestion run
    state = {"context": None}

    # ========== CONTEXT TOOLS ==========

    @tool
    def get_receipt_context() -> dict:
        """
        Get full context for the receipt being processed.

        Returns:
        - words: All words on the receipt (excluding noise)
        - existing_labels: All existing labels for this receipt
        - unlabeled_words: Words that don't have any labels yet
        - merchant_name: Merchant name from receipt metadata
        - merchant_receipt_count: Number of receipts for this merchant
          (if available)
        """
        ctx: ReceiptContext = state["context"]
        if ctx is None:
            return {"error": "No receipt context set"}

        try:
            # Get all words (excluding noise)
            all_words = dynamo_client.list_receipt_words_from_receipt(
                image_id=ctx.image_id,
                receipt_id=ctx.receipt_id,
            )
            meaningful_words = [
                w for w in all_words
                if not getattr(w, "is_noise", False)
            ]

            # Get existing labels
            existing_labels, _ = (
                dynamo_client.list_receipt_word_labels_for_receipt(
                    image_id=ctx.image_id,
                    receipt_id=ctx.receipt_id,
                )
            )

            # Find unlabeled words
            labeled_word_keys = {
                (l.line_id, l.word_id) for l in existing_labels
            }
            unlabeled_words = [
                {
                    "word_id": w.word_id,
                    "line_id": w.line_id,
                    "text": w.text,
                }
                for w in meaningful_words
                if (w.line_id, w.word_id) not in labeled_word_keys
            ]

            # Get merchant place data (may not exist)
            try:
                place = dynamo_client.get_receipt_place(
                    image_id=ctx.image_id,
                    receipt_id=ctx.receipt_id,
                )
                merchant_name = place.merchant_name
            except Exception:
                # Place data unavailable
                merchant_name = None

            return {
                "total_words": len(meaningful_words),
                "existing_labels_count": len(existing_labels),
                "unlabeled_words_count": len(unlabeled_words),
                "unlabeled_words": unlabeled_words[:50],
                # Limit to first 50 for display
                "merchant_name": merchant_name,
                "merchant_receipt_count": ctx.merchant_receipt_count,
            }

        except Exception as e:
            logger.error(f"Error getting receipt context: {e}")
            return {"error": str(e)}

    @tool(args_schema=SearchLabelCandidatesInput)
    def search_label_candidates(
        word_id: int,
        line_id: int,
        n_results: int = 50,
    ) -> dict:
        """
        Search ChromaDB for similar words with VALID labels to find label
        candidates.

        This is the core tool - it finds similar words that have been correctly
        labeled, and analyzes which label types appear most frequently.

        Returns:
        - word_text: The word being analyzed
        - candidates: List of label candidates with confidence scores
          Each candidate has: label_type, confidence, match_count,
          avg_similarity, examples
        - top_candidate: The highest confidence candidate (if any)
        - should_use_merchant_filter: Whether merchant filtering was used

        Args:
            word_id: Word ID to find candidates for
            line_id: Line ID containing the word
            n_results: Number of ChromaDB results to fetch (default: 50,
                more = better stats)
        """
        ctx: ReceiptContext = state["context"]
        if ctx is None:
            return {"error": "No receipt context set"}

        if not chroma_client or not embed_fn:
            return {"error": "ChromaDB or embed function not available"}

        try:
            # Get the word
            dynamo_get_start = time.time()
            word = dynamo_client.get_receipt_word(
                receipt_id=ctx.receipt_id,
                image_id=ctx.image_id,
                line_id=line_id,
                word_id=word_id,
            )
            dynamo_get_time = time.time() - dynamo_get_start

            if not word:
                return {"error": f"Word {word_id} not found"}

            # Get all receipt words for context (needed for embedding format)
            dynamo_list_start = time.time()
            all_words_in_receipt = (
                dynamo_client.list_receipt_words_from_receipt(
                    image_id=ctx.image_id,
                    receipt_id=ctx.receipt_id,
                )
            )
            dynamo_list_time = time.time() - dynamo_list_start

            if not all_words_in_receipt:
                return {
                    "error": f"No words found for receipt {ctx.receipt_id}"
                }

            # Build word ID for ChromaDB lookup
            chroma_id = _build_word_id(
                ctx.image_id,
                ctx.receipt_id,
                line_id,
                word_id,
            )

            # Try to get stored embedding first (fast, free)
            # Since words were just re-embedded with the new format,
            # stored embeddings should match
            target_embedding = None
            get_time = 0
            embed_time = 0

            # Try to get stored embedding first (faster than on-the-fly)
            get_start = time.time()
            try:
                results = chroma_client.get(
                    collection_name="words",
                    ids=[chroma_id],
                    include=["embeddings"],
                )

                if results and results.get("ids") and results["ids"]:
                    embeddings = results.get("embeddings")
                    if (
                        embeddings
                        and len(embeddings) > 0
                        and embeddings[0] is not None
                    ):
                        target_embedding = embeddings[0]
                        # Convert to list if numpy array
                        if hasattr(target_embedding, 'tolist'):
                            target_embedding = target_embedding.tolist()
                        elif not isinstance(target_embedding, list):
                            target_embedding = list(target_embedding)
                        logger.debug(
                            f"Using stored embedding for word '{word.text}'"
                        )
            except Exception as e:
                logger.debug(f"Could not get stored embedding: {e}")
            finally:
                get_time = time.time() - get_start

            # Fallback to on-the-fly embedding if stored embedding not found
            # This ensures format consistency and works even if the word
            # doesn't exist in ChromaDB
            if target_embedding is None:
                logger.debug(
                    f"Embedding on-the-fly for word '{word.text}' "
                    f"(not found in ChromaDB or fallback)"
                )

                embed_start = time.time()
                # Format word using the same format as validation agent
                formatted_text = format_word_context_embedding_input(
                    target_word=word,
                    all_words=all_words_in_receipt,
                    # Default context size matches ChromaDB format
                    context_size=2,
                )

                # Embed the formatted text on-the-fly using the embed function
                target_embedding = embed_fn([formatted_text])[0]

                if not target_embedding:
                    return {
                        "error": (
                            f"Failed to generate embedding for word "
                            f"'{word.text}'"
                        )
                    }

                # Ensure it's a list
                if not isinstance(target_embedding, list):
                    if hasattr(target_embedding, 'tolist'):
                        target_embedding = target_embedding.tolist()
                    else:
                        target_embedding = list(target_embedding)
                embed_time = time.time() - embed_start

            # Decide whether to use merchant filter
            # Only use if merchant has >= 10 receipts (increases accuracy)
            use_merchant_filter = False
            merchant_filter = None
            if (
                ctx.merchant_name
                and ctx.merchant_receipt_count
                and ctx.merchant_receipt_count >= 10
            ):
                use_merchant_filter = True
                merchant_filter = ctx.merchant_name.strip().title()
                where_clause = {"merchant_name": {"$eq": merchant_filter}}
            else:
                where_clause = None

            # Query ChromaDB for similar words
            # Get extra results to filter out self
            # (same pattern as validation agent)
            query_start = time.time()
            query_results = chroma_client.query(
                collection_name="words",
                query_embeddings=[target_embedding],
                n_results=n_results + 10,  # Get extra to filter out self
                where=where_clause,
                include=["documents", "metadatas", "distances"],
            )
            query_time = time.time() - query_start

            if not query_results or not query_results.get("documents"):
                logger.debug(f"No query results for word '{word.text}'")
            # Continue to process results even if empty

            # Log query results for debugging
            total_results = (
                len(query_results.get("ids", [[]])[0])
                if query_results.get("ids")
                else 0
            )
            logger.info(
                f"ChromaDB query for '{word.text}': "
                f"found {total_results} total results"
            )

            # Check a few sample results to see what we're getting
            if total_results > 0:
                sample_ids = query_results.get("ids", [[]])[0][:3]
                sample_docs = query_results.get("documents", [[]])[0][:3]
                sample_metas = query_results.get("metadatas", [[]])[0][:3]
                for sid, sdoc, smeta in zip(
                    sample_ids,
                    sample_docs,
                    sample_metas,
                ):
                    valid_lbls = smeta.get("valid_labels", "") if smeta else ""
                    logger.info(
                        f"  Sample result: '{sdoc}' (id: {sid[:50]}...) "
                        f"valid_labels: '{valid_lbls}'"
                    )

            # Analyze results to find label candidates
            ids = query_results.get("ids", [[]])[0]
            documents = query_results.get("documents", [[]])[0]
            metadatas = query_results.get("metadatas", [[]])[0]
            distances = query_results.get("distances", [[]])[0]

            # Build word ID to exclude self
            chroma_id = _build_word_id(
                ctx.image_id,
                ctx.receipt_id,
                line_id,
                word_id,
            )

            # Track label candidates
            label_candidates = {}
            # {label_type: {count, similarities, examples}}

            words_with_valid_labels = 0
            words_without_valid_labels = 0

            for doc_id, doc, meta, dist in zip(
                ids,
                documents,
                metadatas,
                distances,
            ):
                # Skip if same word
                if doc_id == chroma_id:
                    continue

                similarity = max(0.0, 1.0 - (dist / 2))

                # Parse valid_labels (comma-delimited string format:
                # ",LABEL1,LABEL2,")
                valid_labels_str = meta.get("valid_labels", "") if meta else ""
                # Check if valid_labels is non-empty
                # (after stripping delimiters)
                if not valid_labels_str or not valid_labels_str.strip(","):
                    words_without_valid_labels += 1
                    continue

                words_with_valid_labels += 1

                words_with_valid_labels += 1

                # Parse comma-delimited format: ",LABEL1,LABEL2,"
                valid_labels = [
                    label.strip()
                    for label in valid_labels_str.strip(",").split(",")
                    if label.strip()
                ]

                # Track each valid label
                for label_type in valid_labels:
                    if label_type not in label_candidates:
                        label_candidates[label_type] = {
                            "count": 0,
                            "similarities": [],
                            "examples": [],
                        }

                    label_candidates[label_type]["count"] += 1
                    label_candidates[label_type]["similarities"].append(
                        similarity
                    )

                    # Keep up to 3 examples
                    if len(label_candidates[label_type]["examples"]) < 3:
                        label_candidates[label_type]["examples"].append({
                            "word": doc,
                            "similarity": round(similarity, 3),
                            "merchant": meta.get("merchant_name", "Unknown"),
                        })

            # Calculate confidence scores for each candidate
            scored_candidates = []
            for label_type, data in label_candidates.items():
                if not data["similarities"]:
                    continue

                avg_sim = sum(data["similarities"]) / len(data["similarities"])
                count = data["count"]

                # Confidence formula: prioritizes similarity over count
                # High similarity is more reliable than high count
                # Weight: 70% similarity, 30% count
                # (suggestions should be similarity-driven)
                count_score = min(1.0, count / 5.0)
                # Normalize to 0-1 (5+ = 1.0, more permissive)
                similarity_score = avg_sim  # Already 0-1
                confidence = (similarity_score * 0.7) + (count_score * 0.3)

                scored_candidates.append({
                    "label_type": label_type,
                    "confidence": round(confidence, 3),
                    "match_count": count,
                    "avg_similarity": round(avg_sim, 3),
                    "min_similarity": round(min(data["similarities"]), 3),
                    "max_similarity": round(max(data["similarities"]), 3),
                    "examples": data["examples"],
                })

            # Sort by confidence
            scored_candidates.sort(key=lambda x: x["confidence"], reverse=True)

            logger.info(
                f"Word '{word.text}': {words_with_valid_labels} results "
                f"with valid_labels, {words_without_valid_labels} without, "
                f"{len(scored_candidates)} candidates"
            )

            # If we found similar words but none have valid_labels,
            # that's useful info
            if total_results > 0 and words_with_valid_labels == 0:
                logger.info(
                    f"  Note: Found {total_results} similar words "
                    f"in ChromaDB, but none have VALID labels yet. "
                    f"This word will be skipped until similar words "
                    f"are validated."
                )

            return {
                "word_text": word.text,
                "word_id": word_id,
                "line_id": line_id,
                "candidates": scored_candidates,
                "top_candidate": (
                    scored_candidates[0] if scored_candidates else None
                ),
                "should_use_merchant_filter": use_merchant_filter,
                "total_matches": len([d for d in documents if d]),
                "words_with_valid_labels": words_with_valid_labels,
                "words_without_valid_labels": words_without_valid_labels,
                "timing": {
                    "dynamo_get_word_seconds": dynamo_get_time,
                    "dynamo_list_words_seconds": dynamo_list_time,
                    "chroma_get_seconds": get_time,
                    "embedding_seconds": embed_time,
                    "chroma_query_seconds": query_time,
                    "total_seconds": (
                        dynamo_get_time
                        + dynamo_list_time
                        + get_time
                        + embed_time
                        + query_time
                    ),
                },
                "message": (
                    f"Found {total_results} similar words, "
                    f"but {words_with_valid_labels} have VALID labels. "
                    if total_results > 0 and words_with_valid_labels == 0
                    else None
                ),
            }

        except Exception as e:
            logger.error(
                f"Error searching label candidates: {e}",
                exc_info=True,
            )
            return {"error": str(e)}

    @tool(args_schema=SubmitLabelSuggestionsInput)
    def submit_label_suggestions(suggestions: list[dict]) -> dict:
        """
        Submit label suggestions for words on the receipt.

        This creates ReceiptWordLabel entities
        with validation_status="PENDING".
        The validator and harmonizer will refine these later.

        Args:
            suggestions: List of label suggestions. Each dict should have:
                - word_id: int
                - line_id: int
                - label_type: str (CORE_LABEL type)
                - confidence: float (0.0-1.0)
                - reasoning: str (explanation)

        Returns:
            Summary of created labels
        """
        ctx: ReceiptContext = state["context"]
        if ctx is None:
            return {"error": "No receipt context set"}

        try:
            from datetime import datetime
            from receipt_dynamo.entities import ReceiptWordLabel

            created_labels = []
            errors = []

            for suggestion in suggestions:
                try:
                    word_id = suggestion["word_id"]
                    line_id = suggestion["line_id"]
                    label_type = suggestion["label_type"]
                    confidence = suggestion.get("confidence", 0.5)
                    reasoning = suggestion.get(
                        "reasoning",
                        "Suggested by label suggestion agent",
                    )

                    # Create ReceiptWordLabel with PENDING status
                    label = ReceiptWordLabel(
                        image_id=ctx.image_id,
                        receipt_id=ctx.receipt_id,
                        line_id=line_id,
                        word_id=word_id,
                        label=label_type,
                        validation_status="PENDING",
                        reasoning=reasoning,
                        timestamp_added=datetime.utcnow(),
                        label_proposed_by="label-suggestion-agent",
                    )

                    # Save to DynamoDB
                    dynamo_client.create_receipt_word_label(label)
                    created_labels.append({
                        "word_id": word_id,
                        "line_id": line_id,
                        "label_type": label_type,
                        "confidence": confidence,
                    })

                except Exception as e:
                    errors.append({
                        "suggestion": suggestion,
                        "error": str(e),
                    })

            return {
                "status": "success",
                "created_count": len(created_labels),
                "error_count": len(errors),
                "created_labels": created_labels,
                "errors": errors,
            }

        except Exception as e:
            logger.error(
                f"Error submitting label suggestions: {e}",
                exc_info=True,
            )
            return {"error": str(e)}

    # Return tools
    tools = [
        get_receipt_context,
        search_label_candidates,
        submit_label_suggestions,
    ]

    return tools, state
