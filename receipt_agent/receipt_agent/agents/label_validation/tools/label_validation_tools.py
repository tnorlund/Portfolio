"""
Tools for label validation agent.

These tools allow the agent to gather all necessary context to validate
a label suggestion for a word on a receipt.
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator
from receipt_chroma.embedding.formatting.word_format import (
    format_word_context_embedding_input,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# Word Context - Injected at runtime
# ==============================================================================


@dataclass
class WordContext:
    """Context for the word being validated. Injected into tools at runtime."""

    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    word_text: str
    suggested_label_type: str
    merchant_name: Optional[str]
    original_reasoning: str


def _build_word_id(image_id: str, receipt_id: int, line_id: int, word_id: int) -> str:
    """Build ChromaDB document ID for a word."""
    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}"


# ==============================================================================
# Tool Input Schemas
# ==============================================================================


class SearchSimilarWordsInput(BaseModel):
    """Input for search_similar_words tool."""

    n_results: int = Field(
        default=10,
        ge=1,
        le=15,
        description="Number of results (max 15 to prevent 400 errors)",
    )


class SubmitDecisionInput(BaseModel):
    """Input for submit_decision tool."""

    decision: Literal["VALID", "INVALID", "NEEDS_REVIEW"] = Field(
        description="Validation decision: VALID (correct), INVALID (wrong), NEEDS_REVIEW (uncertain)"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score 0.0 to 1.0")
    reasoning: str = Field(description="Detailed explanation of the decision")
    evidence: list[str] = Field(
        description="Key findings that support the decision. Each item should be a string describing the evidence (e.g., 'Word appears in merchant name line', 'Merchant metadata matches')."
    )

    @field_validator("evidence", mode="before")
    @classmethod
    def normalize_evidence(cls, v):
        """Normalize evidence to list of strings.

        Accepts:
        - List of strings: ['evidence1', 'evidence2']
        - List of dicts: [{'detail': '...', 'type': '...'}, ...]
        - Mixed: ['string', {'detail': '...'}, ...]
        """
        if not isinstance(v, list):
            return v

        normalized = []
        for item in v:
            if isinstance(item, str):
                normalized.append(item)
            elif isinstance(item, dict):
                # Extract detail if present, otherwise stringify the dict
                detail = (
                    item.get("detail")
                    or item.get("description")
                    or item.get("evidence")
                )
                if detail:
                    normalized.append(str(detail))
                else:
                    # Fallback: create a string from the dict
                    evidence_type = item.get("type", "unknown")
                    normalized.append(f"[{evidence_type}] {item}")
            else:
                # Convert anything else to string
                normalized.append(str(item))

        return normalized


# ==============================================================================
# Tool Factory
# ==============================================================================


def create_label_validation_tools(
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
) -> tuple[list[Any], dict]:
    """
    Create tools for the label validation agent.

    Returns:
        (tools, state_holder) - tools list and a dict to hold runtime state
    """
    # State holder - will be populated before each validation
    state = {"context": None, "decision": None}

    # ========== CONTEXT TOOLS ==========

    @tool
    def get_word_context() -> dict:
        """
        Get full context for the word being validated.

        **NOTE**: Word context is already provided in the initial prompt above.
        Use this tool only if you need to re-fetch or verify the context.

        Returns:
        - word_text: The word text
        - line_text: The full line the word is on
        - surrounding_words: Words before and after (3 before, 3 after)
        - surrounding_lines: All lines from receipt (formatted with visual row grouping)
        - receipt_metadata: Basic receipt info (merchant_name, place_id, etc.)
        """
        ctx: WordContext = state["context"]
        if ctx is None:
            return {"error": "No word context set"}

        try:
            # Get the line
            line = dynamo_client.get_receipt_line(
                receipt_id=ctx.receipt_id,
                image_id=ctx.image_id,
                line_id=ctx.line_id,
            )
            line_text = line.text if line else None

            # Get surrounding words
            words_in_line = dynamo_client.list_receipt_words_from_line(
                receipt_id=ctx.receipt_id,
                image_id=ctx.image_id,
                line_id=ctx.line_id,
            )
            surrounding_words = None
            if words_in_line:
                words_in_line.sort(key=lambda w: w.word_id)
                word_texts = [w.text for w in words_in_line]
                try:
                    word_index = next(
                        i
                        for i, w in enumerate(words_in_line)
                        if w.word_id == ctx.word_id
                    )
                    start_idx = max(0, word_index - 3)
                    end_idx = min(len(word_texts), word_index + 4)
                    surrounding_words = word_texts[start_idx:end_idx]
                    # Mark the target word
                    target_idx = word_index - start_idx
                    if surrounding_words:
                        surrounding_words[target_idx] = (
                            f"[{surrounding_words[target_idx]}]"
                        )
                except StopIteration:
                    surrounding_words = word_texts

            # Get surrounding lines
            all_lines = dynamo_client.list_receipt_lines_from_receipt(
                image_id=ctx.image_id,
                receipt_id=ctx.receipt_id,
            )
            surrounding_lines = None
            if all_lines and line:
                sorted_lines = sorted(
                    all_lines, key=lambda l: l.calculate_centroid()[1]
                )
                target_idx = None
                for i, receipt_line in enumerate(sorted_lines):
                    if receipt_line.line_id == ctx.line_id:
                        target_idx = i
                        break

                if target_idx is not None:
                    start_idx = max(0, target_idx - 3)
                    end_idx = min(len(sorted_lines), target_idx + 4)
                    context_lines = []
                    for i in range(start_idx, end_idx):
                        line_text_item = sorted_lines[i].text
                        if i == target_idx:
                            context_lines.append(f">>> {line_text_item} <<<")
                        else:
                            context_lines.append(f"    {line_text_item}")
                    surrounding_lines = context_lines

            # Get receipt metadata
            metadata = dynamo_client.get_receipt_metadata(
                image_id=ctx.image_id,
                receipt_id=ctx.receipt_id,
            )
            receipt_metadata = None
            if metadata:
                receipt_metadata = {
                    "merchant_name": metadata.merchant_name,
                    "place_id": metadata.place_id,
                    "address": metadata.address,
                    "phone_number": metadata.phone_number,
                }

            return {
                "word_text": ctx.word_text,
                "line_text": line_text,
                "surrounding_words": surrounding_words,
                "surrounding_lines": surrounding_lines,
                "receipt_metadata": receipt_metadata,
            }

        except Exception as e:
            logger.error(f"Error getting word context: {e}")
            return {"error": str(e)}

    @tool
    def get_merchant_metadata() -> dict:
        """
        Get Google Places metadata for the receipt.

        **NOTE**: Merchant metadata is already provided in the initial prompt above.
        Use this tool only if you need to re-fetch or verify the metadata.

        Google Places data is mostly accurate.

        Returns:
        - merchant_name: Official merchant name from Google Places
        - address: Formatted address
        - phone_number: Phone number
        - place_id: Google Place ID
        """
        ctx: WordContext = state["context"]
        if ctx is None:
            return {"error": "No word context set"}

        try:
            metadata = dynamo_client.get_receipt_metadata(
                image_id=ctx.image_id,
                receipt_id=ctx.receipt_id,
            )

            if not metadata:
                return {"error": "No metadata found for this receipt"}

            return {
                "merchant_name": metadata.merchant_name,
                "address": metadata.address,
                "phone_number": metadata.phone_number,
                "place_id": metadata.place_id,
                "note": "This data comes from Google Places and is mostly accurate",
            }

        except Exception as e:
            logger.error(f"Error getting merchant metadata: {e}")
            return {"error": str(e)}

    @tool(args_schema=SearchSimilarWordsInput)
    def search_similar_words(n_results: int = 20) -> list[dict]:
        """
        Search ChromaDB for semantically similar words with their labels and full context.

        This is SUPPORTING evidence - use to see if other similar words have
        this label type. Similar words are supporting evidence only, not
        primary decision factor.

        Returns similar words with:
        - text: The word text (the labeled word)
        - similarity: Similarity score (0.0-1.0)
        - merchant_name: Merchant name from that receipt
        - valid_labels: List of VALID labels for this word
        - invalid_labels: List of INVALID labels for this word
        - label_status: Overall validation status
        - line_context: The full line the word appears on
        - surrounding_words: Words before and after on the same line (3 before, 3 after)
        - surrounding_lines: Lines before and after (±3 lines around the word's line)
        - receipt_metadata: Full receipt metadata (merchant_name, place_id, formatted_address, phone_number, website)
        - audit_trail: Complete label history for this word (all labels, validation status, reasoning)

        The audit_trail shows:
        - All labels that have been assigned to this word
        - Validation status for each (VALID, INVALID, NEEDS_REVIEW, etc.)
        - Reasoning behind each label
        - Label consolidation chain (which labels superseded which)
        - Timestamp for each label

        Use the audit_trail to understand:
        - Why this word was labeled a certain way
        - If there was cross-label confusion (e.g., MERCHANT_NAME vs PRODUCT_NAME)
        - Previous validation attempts and their outcomes

        Args:
            n_results: Number of results to return (max 15 to prevent 400 errors from large payloads)

        Note: Automatically excludes the current word from results.
        """
        ctx: WordContext = state["context"]
        if ctx is None:
            return [{"error": "No word context set"}]

        if not chroma_client or not embed_fn:
            return [{"error": "ChromaDB or embed function not available"}]

        try:
            # Build word ID for ChromaDB lookup
            chroma_id = _build_word_id(
                ctx.image_id, ctx.receipt_id, ctx.line_id, ctx.word_id
            )

            # Try to get stored embedding first (fast, free)
            # Since words were just re-embedded with new format, stored embeddings should match
            target_embedding = None
            try:
                results = chroma_client.get(
                    collection_name="words",
                    ids=[chroma_id],
                    include=["embeddings"],
                )

                if results and results.get("ids") and results["ids"]:
                    embeddings = results.get("embeddings")
                    if embeddings and len(embeddings) > 0 and embeddings[0] is not None:
                        target_embedding = embeddings[0]
                        # Convert to list if numpy array
                        if hasattr(target_embedding, "tolist"):
                            target_embedding = target_embedding.tolist()
                        elif not isinstance(target_embedding, list):
                            target_embedding = list(target_embedding)
                        logger.debug(
                            f"Using stored embedding for word '{ctx.word_text}'"
                        )
            except Exception as e:
                logger.debug(f"Could not get stored embedding: {e}")

            # Fallback to on-the-fly embedding if stored embedding not found
            # This ensures format consistency and works even if word doesn't exist in ChromaDB
            if target_embedding is None:
                logger.debug(
                    f"Embedding on-the-fly for word '{ctx.word_text}' (not found in ChromaDB or fallback)"
                )

                # Get the target word entity and all words in receipt for context
                target_word = dynamo_client.get_receipt_word(
                    receipt_id=ctx.receipt_id,
                    image_id=ctx.image_id,
                    line_id=ctx.line_id,
                    word_id=ctx.word_id,
                )

                if not target_word:
                    return [{"error": f"Word '{ctx.word_text}' not found in DynamoDB"}]

                # Get all words in the receipt for context (needed for new embedding format)
                all_words_in_receipt = dynamo_client.list_receipt_words_from_receipt(
                    image_id=ctx.image_id,
                    receipt_id=ctx.receipt_id,
                )

                if not all_words_in_receipt:
                    return [{"error": f"No words found for receipt {ctx.receipt_id}"}]

                # Format word using the new embedding format (context-only with 2 words on each side)
                # This matches the format used in the re-embedded ChromaDB
                formatted_text = format_word_context_embedding_input(
                    target_word=target_word,
                    all_words=all_words_in_receipt,
                    context_size=2,  # Default context size matches new format
                )

                # Embed the formatted text on-the-fly using the embed function
                target_embedding = embed_fn([formatted_text])[0]

                if not target_embedding:
                    return [
                        {
                            "error": f"Failed to generate embedding for word '{ctx.word_text}'"
                        }
                    ]

                # Ensure it's a list
                if not isinstance(target_embedding, list):
                    if hasattr(target_embedding, "tolist"):
                        target_embedding = target_embedding.tolist()
                    else:
                        target_embedding = list(target_embedding)

            # Query for similar words
            query_results = chroma_client.query(
                collection_name="words",
                query_embeddings=[target_embedding],
                n_results=n_results + 10,  # Get extra to filter out self
                where=None,  # No merchant filter - see all merchants
                include=["documents", "metadatas", "distances"],
            )

            if not query_results or not query_results.get("documents"):
                return [{"error": "No similar words found"}]

            output = []
            ids = query_results.get("ids", [[]])[0]
            documents = query_results.get("documents", [[]])[0]
            metadatas = query_results.get("metadatas", [[]])[0]
            distances = query_results.get("distances", [[]])[0]

            for _doc_id, doc, meta, dist in zip(ids, documents, metadatas, distances):
                # Skip if same word
                if (
                    meta.get("image_id") == ctx.image_id
                    and int(meta.get("receipt_id", -1)) == ctx.receipt_id
                    and int(meta.get("line_id", -1)) == ctx.line_id
                    and int(meta.get("word_id", -1)) == ctx.word_id
                ):
                    continue

                similarity = max(0.0, 1.0 - (dist / 2))

                # Parse valid_labels and invalid_labels
                valid_labels_str = meta.get("valid_labels", "")
                invalid_labels_str = meta.get("invalid_labels", "")
                valid_labels_list = [
                    label.strip()
                    for label in valid_labels_str.split(",")
                    if label.strip()
                ]
                invalid_labels_list = [
                    label.strip()
                    for label in invalid_labels_str.split(",")
                    if label.strip()
                ]

                # Get full context for this similar word (like harmonizer v2)
                similar_line_context = None
                similar_surrounding_lines = None
                similar_surrounding_words = None
                similar_audit_trail = None
                try:
                    similar_image_id = meta.get("image_id")
                    similar_receipt_id = int(meta.get("receipt_id", 0))
                    similar_line_id = int(meta.get("line_id", 0))
                    similar_word_id = int(meta.get("word_id", 0))

                    if similar_image_id and similar_receipt_id and similar_line_id:
                        # Get the line this word is on
                        similar_line = dynamo_client.get_receipt_line(
                            receipt_id=similar_receipt_id,
                            image_id=similar_image_id,
                            line_id=similar_line_id,
                        )
                        similar_line_context = (
                            similar_line.text if similar_line else None
                        )

                        # Get surrounding words on the same line (3 before, 3 after)
                        if similar_line:
                            words_in_line = dynamo_client.list_receipt_words_from_line(
                                receipt_id=similar_receipt_id,
                                image_id=similar_image_id,
                                line_id=similar_line_id,
                            )
                            if words_in_line:
                                words_in_line.sort(key=lambda w: w.word_id)
                                word_texts = [w.text for w in words_in_line]
                                try:
                                    word_index = next(
                                        i
                                        for i, w in enumerate(words_in_line)
                                        if w.word_id == similar_word_id
                                    )
                                    start_idx = max(0, word_index - 3)
                                    end_idx = min(len(word_texts), word_index + 4)
                                    surrounding_words_list = word_texts[
                                        start_idx:end_idx
                                    ]
                                    # Mark the target word
                                    target_idx = word_index - start_idx
                                    if surrounding_words_list:
                                        surrounding_words_list[target_idx] = (
                                            f"[{surrounding_words_list[target_idx]}]"
                                        )
                                    similar_surrounding_words = " ".join(
                                        surrounding_words_list
                                    )
                                except StopIteration:
                                    similar_surrounding_words = " ".join(word_texts)

                        # Get ±N lines around the target word's line (not the entire receipt)
                        # This matches what we do in the initial prompt and prevents 400 errors
                        N_LINES_CONTEXT = 3  # ±3 lines around the target word
                        all_similar_lines = (
                            dynamo_client.list_receipt_lines_from_receipt(
                                image_id=similar_image_id,
                                receipt_id=similar_receipt_id,
                            )
                        )
                        if all_similar_lines and similar_line:
                            # Sort lines by Y-coordinate (top to bottom)
                            sorted_lines = sorted(
                                all_similar_lines,
                                key=lambda l: l.calculate_centroid()[1],
                            )

                            # Find the index of the target line
                            try:
                                target_line_index = next(
                                    i
                                    for i, line in enumerate(sorted_lines)
                                    if line.line_id == similar_line_id
                                )
                            except StopIteration:
                                # Line not found, use all lines (fallback)
                                target_line_index = len(sorted_lines) // 2

                            # Get ±N lines around the target line
                            start_idx = max(0, target_line_index - N_LINES_CONTEXT)
                            end_idx = min(
                                len(sorted_lines),
                                target_line_index + N_LINES_CONTEXT + 1,
                            )
                            context_lines = sorted_lines[start_idx:end_idx]

                            # Format lines to show how they appear on the receipt
                            # Mark the specific word instance within the line using word_id (not regex)
                            # Note: Each line has multiple words, and the same word text can appear on different lines
                            # with different word_ids. We use similar_line_id to identify the line and similar_word_id
                            # to identify the specific word within that line.
                            similar_target_word_obj = None
                            similar_target_word_occurrence = None
                            if words_in_line:
                                # words_in_line already contains words from the specific line (similar_line_id)
                                words_in_line.sort(key=lambda w: w.word_id)
                                try:
                                    # Find the specific word by word_id within this line
                                    similar_target_word_index = next(
                                        i
                                        for i, w in enumerate(words_in_line)
                                        if w.word_id
                                        == similar_word_id  # Specific word_id identifies which word on this line
                                    )
                                    similar_target_word_obj = words_in_line[
                                        similar_target_word_index
                                    ]
                                    # Count how many times this word text appears before our target word ON THIS LINE
                                    # This ensures we mark the correct instance if the word appears multiple times on the same line
                                    similar_target_word_occurrence = sum(
                                        1
                                        for w in words_in_line[
                                            :similar_target_word_index
                                        ]
                                        if w.text == similar_target_word_obj.text
                                    )
                                except StopIteration:
                                    similar_target_word_obj = None

                            # Group visually contiguous lines (on same row) together using corners
                            formatted_lines = []
                            for i, receipt_line in enumerate(context_lines):
                                line_text = receipt_line.text

                                # If this is the target line (similar_line_id matches), mark the specific word by word_id
                                # This ensures we only mark the word on the correct line, even if the same word
                                # appears on other lines with different word_ids
                                if (
                                    receipt_line.line_id == similar_line_id
                                    and similar_target_word_obj
                                ):
                                    # Find and replace the Nth occurrence of this word text (where N = similar_target_word_occurrence + 1)
                                    word_text = similar_target_word_obj.text
                                    occurrence_count = 0
                                    word_start = 0
                                    while True:
                                        word_start = line_text.find(
                                            word_text, word_start
                                        )
                                        if word_start == -1:
                                            break
                                        # Check if it's a whole word (not part of another word)
                                        if (
                                            word_start == 0
                                            or not line_text[word_start - 1].isalnum()
                                        ) and (
                                            word_start + len(word_text)
                                            >= len(line_text)
                                            or not line_text[
                                                word_start + len(word_text)
                                            ].isalnum()
                                        ):
                                            if (
                                                occurrence_count
                                                == similar_target_word_occurrence
                                            ):
                                                # This is our target word instance - mark it
                                                line_text = (
                                                    line_text[:word_start]
                                                    + f"[{word_text}]"
                                                    + line_text[
                                                        word_start + len(word_text) :
                                                    ]
                                                )
                                                break
                                            occurrence_count += 1
                                        word_start += 1

                                # Check if this line is on the same visual row as the previous line
                                if i > 0:
                                    prev_line = context_lines[i - 1]
                                    curr_centroid = receipt_line.calculate_centroid()
                                    # Check if current line's centroid Y is within previous line's vertical span
                                    if (
                                        prev_line.bottom_left["y"]
                                        < curr_centroid[1]
                                        < prev_line.top_left["y"]
                                    ):
                                        # Same visual row - append to previous formatted line with space
                                        formatted_lines[-1] += f" {line_text}"
                                        continue

                                # New row - add as new line
                                formatted_lines.append(line_text)

                            similar_surrounding_lines = formatted_lines

                        # Get receipt metadata for this similar word (initialized to None)
                        similar_receipt_metadata = None
                        try:
                            similar_metadata = dynamo_client.get_receipt_metadata(
                                image_id=similar_image_id,
                                receipt_id=similar_receipt_id,
                            )
                            if similar_metadata:
                                similar_receipt_metadata = {
                                    "merchant_name": similar_metadata.merchant_name,
                                    "place_id": similar_metadata.place_id,
                                    "formatted_address": getattr(
                                        similar_metadata, "formatted_address", None
                                    )
                                    or getattr(similar_metadata, "address", None),
                                    "phone_number": getattr(
                                        similar_metadata, "phone_number", None
                                    ),
                                    "website": getattr(
                                        similar_metadata, "website", None
                                    ),
                                }
                        except Exception as e:
                            logger.debug(
                                f"Could not fetch receipt metadata for similar word: {e}"
                            )
                            # Continue without metadata - not critical

                        # Get audit trail for this similar word (all labels with reasoning)
                        similar_audit_trail = None
                        try:
                            all_similar_labels, _ = (
                                dynamo_client.list_receipt_word_labels_for_word(
                                    image_id=similar_image_id,
                                    receipt_id=similar_receipt_id,
                                    line_id=similar_line_id,
                                    word_id=similar_word_id,
                                )
                            )
                            if all_similar_labels:
                                # Sort by timestamp
                                all_similar_labels.sort(key=lambda l: l.timestamp_added)
                                similar_audit_trail = []
                                for label in all_similar_labels:
                                    similar_audit_trail.append(
                                        {
                                            "label": label.label,
                                            "validation_status": label.validation_status,
                                            "label_proposed_by": label.label_proposed_by,
                                            "label_consolidated_from": label.label_consolidated_from,
                                            "reasoning": label.reasoning or "",
                                            "timestamp_added": str(
                                                label.timestamp_added
                                            ),
                                        }
                                    )
                        except Exception as e:
                            logger.debug(
                                f"Could not fetch audit trail for similar word: {e}"
                            )
                            # Continue without audit trail - not critical

                except Exception as e:
                    logger.debug(f"Could not fetch context for similar word: {e}")
                    # Continue without context - not critical

                output.append(
                    {
                        "text": doc,  # The labeled word
                        "similarity": round(similarity, 3),
                        "merchant_name": meta.get("merchant_name", "Unknown"),
                        "is_same_merchant": meta.get("merchant_name", "")
                        .strip()
                        .title()
                        == (
                            ctx.merchant_name.strip().title()
                            if ctx.merchant_name
                            else ""
                        ),
                        "valid_labels": valid_labels_list,
                        "invalid_labels": invalid_labels_list,
                        "label_status": meta.get("label_status", "unvalidated"),
                        "has_suggested_label": ctx.suggested_label_type
                        in valid_labels_list,
                        "has_suggested_label_invalid": ctx.suggested_label_type
                        in invalid_labels_list,
                        "line_context": similar_line_context,
                        "surrounding_words": similar_surrounding_words,  # Surrounding words on same line
                        "surrounding_lines": similar_surrounding_lines,  # ±N lines around the word
                        "receipt_metadata": similar_receipt_metadata,  # Full receipt metadata (place_id, address, phone, website)
                        "audit_trail": similar_audit_trail,  # Complete label history with reasoning
                    }
                )

                if len(output) >= n_results:
                    break

            logger.info(f"search_similar_words returned {len(output)} results")
            return output

        except Exception as e:
            logger.error(f"Error searching similar words: {e}")
            return [{"error": str(e)}]

    @tool
    def get_all_labels_for_word() -> dict:
        """
        Get all labels that exist for this specific word (audit trail).

        This shows the complete history of labels for this word, including:
        - All labels (current and past)
        - Validation status for each
        - Consolidation chain (which labels superseded which)
        - Reasoning for each label

        Use this to understand if there's been cross-label confusion or
        previous validation attempts.
        """
        ctx: WordContext = state["context"]
        if ctx is None:
            return {"error": "No word context set"}

        try:
            # Get all labels for this word
            all_labels, _ = dynamo_client.list_receipt_word_labels_for_word(
                image_id=ctx.image_id,
                receipt_id=ctx.receipt_id,
                line_id=ctx.line_id,
                word_id=ctx.word_id,
            )

            if not all_labels:
                return {
                    "message": "No labels found for this word",
                    "word_text": ctx.word_text,
                }

            # Sort by timestamp
            all_labels.sort(key=lambda l: l.timestamp_added)

            # Build audit trail
            audit_trail = []
            for label in all_labels:
                is_current = (
                    label.label == ctx.suggested_label_type
                    or label.validation_status == "NEEDS_REVIEW"
                )
                audit_trail.append(
                    {
                        "label": label.label,
                        "validation_status": label.validation_status,
                        "label_proposed_by": label.label_proposed_by,
                        "label_consolidated_from": label.label_consolidated_from,
                        "reasoning": label.reasoning,
                        "timestamp_added": str(label.timestamp_added),
                        "is_current": is_current,
                    }
                )

            # Build consolidation chain
            consolidation_chain = []
            current_label = None
            for label in all_labels:
                if (
                    label.label == ctx.suggested_label_type
                    or label.validation_status == "NEEDS_REVIEW"
                ):
                    current_label = label
                    break

            if current_label:
                # Trace backwards through consolidation chain
                seen_labels = set()
                label_to_trace = current_label
                while label_to_trace and label_to_trace.label_consolidated_from:
                    if label_to_trace.label_consolidated_from in seen_labels:
                        break  # Prevent cycles
                    seen_labels.add(label_to_trace.label_consolidated_from)

                    # Find the label that was consolidated from
                    consolidated_from_label = None
                    for l in all_labels:
                        if l.label == label_to_trace.label_consolidated_from:
                            consolidated_from_label = l
                            break

                    if consolidated_from_label:
                        consolidation_chain.insert(
                            0,
                            {
                                "label": consolidated_from_label.label,
                                "validation_status": consolidated_from_label.validation_status,
                                "reasoning": consolidated_from_label.reasoning,
                                "timestamp": str(
                                    consolidated_from_label.timestamp_added
                                ),
                                "action": "was superseded by",
                            },
                        )
                        label_to_trace = consolidated_from_label
                    else:
                        break

                # Add current label
                consolidation_chain.append(
                    {
                        "label": current_label.label,
                        "validation_status": current_label.validation_status,
                        "reasoning": current_label.reasoning,
                        "timestamp": str(current_label.timestamp_added),
                        "action": (
                            "current (NEEDS_REVIEW)"
                            if current_label.validation_status == "NEEDS_REVIEW"
                            else "current"
                        ),
                    }
                )

            return {
                "word_text": ctx.word_text,
                "total_labels": len(all_labels),
                "audit_trail": audit_trail,
                "consolidation_chain": (
                    consolidation_chain if consolidation_chain else None
                ),
                "current_label": (
                    {
                        "label": current_label.label if current_label else None,
                        "validation_status": (
                            current_label.validation_status if current_label else None
                        ),
                    }
                    if current_label
                    else None
                ),
            }

        except Exception as e:
            logger.error(f"Error getting all labels for word: {e}")
            return {"error": str(e)}

    @tool
    def get_labels_on_receipt() -> dict:
        """
        Get all labels on the same receipt for context.

        Returns:
        - label_counts: Count of labels by type
        - labels_on_same_line: Labels on the same line as the word
        - total_labels: Total number of labels on receipt
        """
        ctx: WordContext = state["context"]
        if ctx is None:
            return {"error": "No word context set"}

        try:
            # Get all labels for this receipt
            all_labels, _ = dynamo_client.list_receipt_word_labels_for_receipt(
                image_id=ctx.image_id,
                receipt_id=ctx.receipt_id,
            )

            # Count by label type
            label_counts = {}
            labels_on_same_line = []
            for label in all_labels:
                label_type = label.label
                label_counts[label_type] = label_counts.get(label_type, 0) + 1

                if label.line_id == ctx.line_id:
                    labels_on_same_line.append(
                        {
                            "word_id": label.word_id,
                            "label": label.label,
                            "validation_status": label.validation_status,
                        }
                    )

            return {
                "total_labels": len(all_labels),
                "label_counts": label_counts,
                "labels_on_same_line": labels_on_same_line,
            }

        except Exception as e:
            logger.error(f"Error getting labels on receipt: {e}")
            return {"error": str(e)}

    # ========== DECISION TOOL ==========

    @tool(args_schema=SubmitDecisionInput)
    def submit_decision(
        decision: Literal["VALID", "INVALID", "NEEDS_REVIEW"],
        confidence: float,
        reasoning: str,
        evidence: list[str],
    ) -> dict:
        """
        Submit your final validation decision.

        This ends the workflow. You MUST call this after gathering all context.
        This can only be called once - subsequent calls will be ignored.

        Args:
            decision: VALID (correct), INVALID (wrong), or NEEDS_REVIEW (uncertain)
            confidence: Your confidence (0.0-1.0)
            reasoning: Detailed explanation
            evidence: List of key findings that support your decision
        """
        # BEST PRACTICE: Prevent multiple calls
        # If decision already exists, return early (idempotent)
        if state is not None and state.get("decision") is not None:
            existing = state["decision"]
            existing_decision = (
                existing.get("decision") if isinstance(existing, dict) else "unknown"
            )
            logger.warning(
                "submit_decision called multiple times. "
                "Returning existing decision: %s",
                existing_decision,
            )
            return {
                "status": "already_submitted",
                "message": f"Decision already submitted: {existing_decision}. This call was ignored.",
                "result": existing,
            }

        result = {
            "decision": decision,
            "confidence": confidence,
            "reasoning": reasoning,
            "evidence": evidence,
        }

        # Set decision in state_holder (this stops the workflow)
        state["decision"] = result
        logger.info(f"Decision submitted: {decision} (confidence={confidence:.2%})")

        return {
            "status": "submitted",
            "message": f"Decision submitted: {decision}. Workflow will now end.",
            "result": result,
        }

    # Return tools
    tools = [
        get_word_context,
        get_merchant_metadata,
        search_similar_words,
        get_all_labels_for_word,
        get_labels_on_receipt,
        submit_decision,
    ]

    return tools, state
