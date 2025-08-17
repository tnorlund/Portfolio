"""
Optimized LangChain Graph Design - Minimal LLM Usage
====================================================

This version prepares all context OUTSIDE the graph, so the graph
only handles LLM validation. This minimizes Ollama API calls.
"""

from typing import TypedDict, List, Optional, Any, Dict
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, END

import os

from .models import ValidationResult, ValidationResponse


# ============================================================================
# Context Preparation (OUTSIDE the graph - no LLM usage)
# ============================================================================


def get_quick_similar_words(
    word_text: str, word_embedding_id: str = None, n_results: int = 3
) -> List[dict]:
    """
    Quick similarity lookup for a single word using the working similarity analysis approach.
    Returns top N similar words with their labels/context.

    Args:
        word_text: The word text to find similarities for
        word_embedding_id: Optional ChromaDB ID if available
        n_results: Number of similar words to return (default 3 for speed)

    Returns:
        List of similar words with context
    """
    try:
        from receipt_label.utils.chroma_client import ChromaDBClient
        import os

        # Setup ChromaDB client - reuse working setup from our analysis script
        word_bucket = "embedding-infra-chromadb-buckets-vectors-fc18686"
        os.environ.setdefault("CHROMADB_BUCKET", word_bucket)

        chroma_client = ChromaDBClient(
            persist_directory="./chroma_words_local_words_specific",
            collection_prefix="",
            mode="read",
        )
        collection = chroma_client.client.get_collection("receipt_words")

        # If we have the embedding ID, use it directly
        if not word_embedding_id:
            return []  # Skip if no ID provided

        # Get the word's embedding using safe approach from working script
        word_result = collection.get(
            ids=[word_embedding_id], include=["embeddings"]
        )

        if (
            word_result.get("ids") is None
            or len(word_result.get("ids", [])) == 0
        ):
            return []  # Word not found

        if (
            word_result.get("embeddings") is None
            or len(word_result.get("embeddings", [])) == 0
        ):
            return []  # No embedding found

        embedding = word_result["embeddings"][0]

        # Find similar words using safe approach
        similar_results = collection.query(
            query_embeddings=[embedding],
            n_results=n_results + 1,  # +1 to account for self-match
            include=["metadatas", "documents", "distances"],
        )

        # Use safe processing approach from working script
        similar_words = []

        # Extract results safely
        ids = (
            similar_results.get("ids", [[]])[0]
            if similar_results.get("ids")
            else []
        )
        docs = (
            similar_results.get("documents", [[]])[0]
            if similar_results.get("documents")
            else []
        )
        distances = (
            similar_results.get("distances", [[]])[0]
            if similar_results.get("distances")
            else []
        )
        metadata_list = (
            similar_results.get("metadatas", [[]])[0]
            if similar_results.get("metadatas")
            else []
        )

        # Pad metadata if needed
        if len(metadata_list) < len(ids):
            metadata_list.extend([{}] * (len(ids) - len(metadata_list)))

        for i in range(min(len(ids), len(docs), len(distances))):
            distance = distances[i]

            # Skip the original word (distance ≈ 0)
            if distance < 0.0001:
                continue

            sim_doc = docs[i]
            sim_metadata = metadata_list[i] if i < len(metadata_list) else {}

            # Extract target word from document
            target_word = sim_doc
            if "<TARGET>" in sim_doc and "</TARGET>" in sim_doc:
                start = sim_doc.find("<TARGET>") + 8
                end = sim_doc.find("</TARGET>")
                target_word = sim_doc[start:end] if start < end else sim_doc

            similar_word = {
                "text": target_word,
                "distance": round(float(distance), 4),
                "merchant": sim_metadata.get("merchant_name", "Unknown"),
                "labels": sim_metadata.get("validated_labels", ""),
            }

            similar_words.append(similar_word)

            # Stop once we have enough results
            if len(similar_words) >= n_results:
                break

        return similar_words

    except Exception as e:
        # Don't break validation if similarity fails
        print(
            f"Warning: Quick similarity lookup failed for '{word_text}': {e}"
        )
        import traceback

        traceback.print_exc()
        return []


def format_similar_examples(similar_words: List[dict]) -> str:
    """Format similar words for inclusion in validation prompt."""
    if not similar_words:
        return "No similar examples found."

    examples = []
    for sim in similar_words:
        example = f"- '{sim['text']}' (similarity: {1-sim['distance']:.2f}) from {sim['merchant']}"
        if sim["labels"]:
            example += f" [Labels: {sim['labels']}]"
        examples.append(example)

    return "\n".join(examples)


def prepare_validation_context(
    image_id: str, receipt_id: int, labels_to_validate: List[dict]
) -> Dict[str, Any]:
    """
    Prepare all context BEFORE calling the graph.
    This doesn't use the LLM at all - just data fetching and formatting.

    Returns:
        Dictionary with all the context needed for validation
    """
    from receipt_label.utils import get_client_manager
    from receipt_label.completion._format_prompt import (
        _format_receipt_lines,
        CORE_LABELS,
    )
    from receipt_dynamo.entities import ReceiptLine

    # Fetch from database
    client_manager = get_client_manager()

    # Get receipt details using correct method name
    receipt_details = client_manager.dynamo.get_receipt_details(
        image_id, receipt_id
    )
    lines = receipt_details.lines
    words = receipt_details.words
    all_labels = (
        receipt_details.labels if hasattr(receipt_details, "labels") else []
    )

    # Try to extract merchant name from labels
    merchant_name = "Unknown"
    merchant_words = []

    # Collect all words labeled as MERCHANT_NAME or BUSINESS_NAME
    for label in all_labels:
        if label.label in ["MERCHANT_NAME", "BUSINESS_NAME"]:
            for word in words:
                if (
                    word.line_id == label.line_id
                    and word.word_id == label.word_id
                ):
                    merchant_words.append(word.text)
                    break

    # Combine merchant words intelligently
    if merchant_words:
        # Look for common patterns like "CVS" and "Pharmacy"
        cvs_found = any("CVS" in w.upper() for w in merchant_words)
        pharmacy_found = any("PHARMACY" in w.upper() for w in merchant_words)

        if cvs_found and pharmacy_found:
            merchant_name = "CVS Pharmacy"
        elif cvs_found:
            merchant_name = "CVS"
        elif merchant_words:
            # Use the first non-generic word, or combine related words
            merchant_name = " ".join(set(merchant_words))

    # If still unknown, look for merchant indicators in reasoning
    if merchant_name == "Unknown":
        for label in all_labels:
            if hasattr(label, "reasoning") and label.reasoning:
                if "CVS" in label.reasoning:
                    merchant_name = "CVS"
                    break
                elif "Walmart" in label.reasoning:
                    merchant_name = "Walmart"
                    break
                elif "Target" in label.reasoning:
                    merchant_name = "Target"
                    break

    # Convert to dicts
    receipt_lines = [line.__dict__ for line in lines]
    receipt_words = [word.__dict__ for word in words]
    receipt_metadata = {
        "merchant_name": merchant_name,
        "image_id": image_id,
        "receipt_id": receipt_id,
    }
    all_labels_dict = [label.__dict__ for label in all_labels]

    # Build validation targets with similarity context and label history
    targets = []
    for label in labels_to_validate:
        # Find the word text
        word = next(
            (
                w
                for w in receipt_words
                if w["line_id"] == label["line_id"]
                and w["word_id"] == label["word_id"]
            ),
            None,
        )

        if word:
            # Generate the ChromaDB ID for similarity lookup
            chromadb_id = (
                f"IMAGE#{label['image_id']}#"
                f"RECEIPT#{label['receipt_id']:05d}#"
                f"LINE#{label['line_id']:05d}#"
                f"WORD#{label['word_id']:05d}"
            )

            # Get quick similarity context (top 3 similar words)
            similar_words = get_quick_similar_words(
                word["text"], chromadb_id, n_results=3
            )

            # Count invalid labels for this word
            invalid_label_count = 0
            previous_invalid_labels = []
            for existing_label in all_labels:
                if (
                    existing_label.line_id == label["line_id"]
                    and existing_label.word_id == label["word_id"]
                ):
                    if hasattr(existing_label, "validation_status"):
                        if existing_label.validation_status in [
                            "INVALID",
                            "NEEDS_REVIEW",
                        ]:
                            invalid_label_count += 1
                            if (
                                existing_label.label
                                not in previous_invalid_labels
                            ):
                                previous_invalid_labels.append(
                                    existing_label.label
                                )

            targets.append(
                {
                    "id": f"IMAGE#{label['image_id']}#"
                    f"RECEIPT#{label['receipt_id']:05d}#"
                    f"LINE#{label['line_id']:05d}#"
                    f"WORD#{label['word_id']:05d}#"
                    f"LABEL#{label['label']}",
                    "text": word["text"],
                    "proposed_label": label["label"],
                    "similar_words": similar_words,
                    "invalid_attempts": invalid_label_count,  # NEW: Track failed attempts
                    "previous_invalid_labels": previous_invalid_labels,  # NEW: What labels failed before
                }
            )

    # Format receipt text
    lines_obj = [ReceiptLine(**line) for line in receipt_lines]
    receipt_text = _format_receipt_lines(lines_obj)

    # Format targets for display with similarity context and history
    targets_text = "\n".join(
        [
            f"- ID: {target['id']}"
            f"\n  Text: '{target['text']}'"
            f"\n  Proposed Label: {target['proposed_label']}"
            + (
                f"\n  Previous Invalid Attempts: {target['invalid_attempts']} "
                f"({', '.join(target['previous_invalid_labels'])})"
                if target["invalid_attempts"] > 0
                else ""
            )
            + f"\n  Similar Words: {format_similar_examples(target['similar_words'])}"
            for target in targets
        ]
    )

    # Format label definitions for the prompt
    label_definitions = "\n".join(
        [
            f"- {label}: {definition}"
            for label, definition in CORE_LABELS.items()
        ]
    )

    # Create the validation prompt with similarity guidance and label definitions
    prompt = f"""You are validating receipt labels. For each word, determine if the proposed label is correct based on the label definitions.

MERCHANT: {receipt_metadata['merchant_name']}

LABEL DEFINITIONS:
{label_definitions}

WORDS TO VALIDATE:
{targets_text}

RECEIPT TEXT:
{receipt_text}

VALIDATION RULES:
- Use the label definitions above to determine if a word matches its proposed label
- Consider the context of the word within the receipt
- Use similar words (if provided) as guidance but not as strict rules
- Some words may not need any label (e.g., punctuation, separators)
- IMPORTANT: If a word has 5+ previous invalid label attempts, it likely doesn't fit any CORE_LABELS category and shouldn't be labeled
- Words that are generic/noise (like single punctuation, random characters) shouldn't be labeled

For each word, return one of these responses:
1. Label is CORRECT: {{"id": "<id>", "is_valid": true}}
2. Label is WRONG, here's the correct one: {{"id": "<id>", "is_valid": false, "correct_label": "<CORRECT_LABEL>"}}
3. Word shouldn't be labeled: {{"id": "<id>", "is_valid": false}}

Return ONLY a JSON array with your responses:
[
  {{"id": "...", "is_valid": true/false, "correct_label": "..."}}
]"""

    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "labels_to_validate": labels_to_validate,
        "validation_targets": targets,
        "formatted_prompt": prompt,
        "receipt_context": {
            "lines": receipt_lines,
            "words": receipt_words,
            "metadata": receipt_metadata,
            "all_labels": all_labels_dict,
        },
    }


# ============================================================================
# Minimal Graph State (only what's needed for LLM)
# ============================================================================


class MinimalValidationState(TypedDict):
    """Minimal state for the validation graph"""

    # Input (already prepared)
    formatted_prompt: str
    validation_targets: List[dict]

    # Results from LLM
    validation_results: List[dict]
    validation_response: Optional[ValidationResponse]  # Structured response

    # Status
    error: Optional[str]
    completed: bool


# ============================================================================
# Response Parsing Utilities
# ============================================================================


def parse_llm_response(
    content: str, targets: List[dict]
) -> Optional[ValidationResponse]:
    """
    Parse LLM response and convert to Pydantic models.

    The LLM returns JSON, but we use Pydantic for validation and type safety.
    This function bridges the gap between raw LLM output and our structured models.

    Args:
        content: Raw response content from LLM (JSON string)
        targets: The validation targets we sent (for context)

    Returns:
        ValidationResponse object with validated data, or None if parsing fails
    """
    import json
    import re
    from pydantic import ValidationError

    try:
        # Clean up the content to extract JSON
        content = content.strip()

        # Remove markdown code blocks if present
        if "```" in content:
            content = re.sub(r"```(?:json)?\s*", "", content)

        # Extract JSON from the content
        json_match = re.search(r"[\[{].*[\]}]", content, re.DOTALL)
        if json_match:
            json_str = json_match.group()
        else:
            json_str = content

        # Parse JSON to Python objects
        data = json.loads(json_str)

        # Convert to list of result dictionaries
        if isinstance(data, list):
            results_data = data
        elif isinstance(data, dict):
            if "results" in data:
                results_data = data["results"]
            elif "id" in data:  # Single result
                results_data = [data]
            else:
                return None
        else:
            return None

        # Use Pydantic to validate and create model instances
        validated_results = []
        for result_data in results_data:
            try:
                # Clean up the data before validation
                # Remove null correct_label if present
                if (
                    "correct_label" in result_data
                    and result_data["correct_label"] is None
                ):
                    del result_data["correct_label"]

                # Pydantic will validate the data according to our model
                result = ValidationResult(**result_data)
                validated_results.append(result)
            except ValidationError as e:
                # Skip invalid results but log the error
                print(f"Skipping invalid result: {e}")
                continue

        if not validated_results:
            return None

        # Create the ValidationResponse using Pydantic
        return ValidationResponse(results=validated_results)

    except (json.JSONDecodeError, ValidationError) as e:
        # Parsing or validation failed
        return None
    except Exception:
        # Unexpected error
        return None


# ============================================================================
# LLM Configuration
# ============================================================================


def get_ollama_llm() -> ChatOllama:
    """Get configured Ollama instance for structured output"""
    base_url = os.getenv("OLLAMA_BASE_URL", "https://ollama.com")
    api_key = os.getenv("OLLAMA_API_KEY")
    model = os.getenv("OLLAMA_MODEL", "gpt-oss:120b")

    # Configure headers for Ollama Turbo API authentication
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    llm = ChatOllama(
        model=model,
        base_url=base_url,
        temperature=0,
        # Configure client kwargs for authentication headers
        client_kwargs={"headers": headers} if headers else {},
        # No format="json" - using structured output only
    )

    return llm


# ============================================================================
# Minimal Graph Nodes (only 2 nodes now!)
# ============================================================================


async def validate_with_ollama(
    state: MinimalValidationState,
) -> MinimalValidationState:
    """
    Node 1: Call Ollama to validate labels.

    This is the ONLY node that uses the LLM. It sends the prompt and
    converts the JSON response to Pydantic models for type safety.
    """
    try:
        llm = get_ollama_llm()

        # Try with_structured_output if available (future-proofing)
        # But catch parsing errors too since models may not return the expected format
        try:
            structured_llm = llm.with_structured_output(ValidationResponse)
            response = await structured_llm.ainvoke(state["formatted_prompt"])

            # Response is already a ValidationResponse Pydantic object
            state["validation_response"] = response
            state["validation_results"] = [
                r.model_dump() for r in response.results
            ]
            state["completed"] = True
            return state

        except Exception:
            # Either model doesn't support with_structured_output,
            # or it does but returns incompatible format
            # Fall through to manual parsing
            pass

        # Direct invocation - the prompt asks for JSON, we parse to Pydantic
        try:
            response = await llm.ainvoke(state["formatted_prompt"])
        except Exception as llm_error:
            # Log the actual LLM error
            state["error"] = f"LLM invocation failed: {str(llm_error)}"
            state["validation_results"] = []
            state["completed"] = False
            return state

        # Convert JSON response to Pydantic models
        validation_response = parse_llm_response(
            response.content, state["validation_targets"]
        )

        if validation_response:
            # Store the Pydantic model
            state["validation_response"] = validation_response
            # Convert to dicts for compatibility
            state["validation_results"] = [
                r.model_dump() for r in validation_response.results
            ]
            state["completed"] = True
        else:
            state["error"] = f"Failed to parse response to Pydantic models"
            state["validation_results"] = []
            state["completed"] = False

        return state

    except Exception as e:
        state["error"] = f"Validation failed: {str(e)}"
        state["validation_results"] = []
        state["completed"] = False
        return state


async def process_results(
    state: MinimalValidationState,
) -> MinimalValidationState:
    """
    Node 2: Process validation results
    This just formats the results - no LLM usage
    """
    if not state.get("validation_results"):
        return state

    # If we have a structured response, we can access the results directly
    if state.get("validation_response"):
        response = state["validation_response"]
        # Could add logging here if needed
        pass

    return state


# ============================================================================
# Minimal Graph Construction
# ============================================================================


def create_minimal_validation_graph() -> Any:
    """
    Create a minimal graph with only 2 nodes:
    1. validate_with_ollama - The ONLY LLM call
    2. process_results - Post-processing (no LLM)
    """
    graph = StateGraph(MinimalValidationState)

    # Only 2 nodes now!
    graph.add_node("validate", validate_with_ollama)
    graph.add_node("process", process_results)

    # Simple flow
    graph.add_edge("validate", "process")
    graph.add_edge("process", END)

    graph.set_entry_point("validate")

    return graph.compile()


# ============================================================================
# Database Update (OUTSIDE the graph - no LLM usage)
# ============================================================================


def update_database_with_results(
    context: Dict[str, Any], validation_results: List[dict]
) -> Dict[str, Any]:
    """
    Update database with validation results.
    This happens OUTSIDE the graph - no LLM usage.
    """
    from receipt_label.utils import get_client_manager
    from receipt_dynamo.constants import ValidationStatus

    if not validation_results:
        return {"success": False, "error": "No validation results"}

    client_manager = get_client_manager()

    valid_labels = []
    invalid_labels = []

    labels_to_validate = context["labels_to_validate"]

    for result in validation_results:
        # Parse the ID
        parts = result["id"].split("#")
        kv = {parts[i]: parts[i + 1] for i in range(0, len(parts) - 1, 2)}

        # Find matching label
        label = next(
            (
                l
                for l in labels_to_validate
                if l["image_id"] == kv["IMAGE"]
                and l["receipt_id"] == int(kv["RECEIPT"])
                and l["line_id"] == int(kv["LINE"])
                and l["word_id"] == int(kv["WORD"])
                and l["label"] == kv["LABEL"]
            ),
            None,
        )

        if label:
            if result["is_valid"]:
                label["validation_status"] = ValidationStatus.VALID.value
                valid_labels.append(label)
            else:
                label["validation_status"] = ValidationStatus.INVALID.value
                if result.get("correct_label"):
                    label["suggested_label"] = result["correct_label"]
                invalid_labels.append(label)

    # Update database
    if valid_labels:
        client_manager.dynamo.updateReceiptWordLabels(valid_labels)
    if invalid_labels:
        client_manager.dynamo.updateReceiptWordLabels(invalid_labels)

    return {
        "success": True,
        "valid_count": len(valid_labels),
        "invalid_count": len(invalid_labels),
    }


# ============================================================================
# Main Validation Function (Orchestrates everything)
# ============================================================================


async def validate_receipt_labels_optimized(
    image_id: str,
    receipt_id: int,
    labels: List[dict],
    skip_database_update: bool = False,
) -> Dict[str, Any]:
    """
    Optimized validation that minimizes LLM usage.

    Process:
    1. Prepare context (no LLM)
    2. Run minimal graph (1 LLM call)
    3. Update database (no LLM)

    Args:
        image_id: Receipt image ID
        receipt_id: Receipt ID
        labels: Labels to validate
        skip_database_update: If True, don't update database (for testing)

    Returns:
        Validation results with metadata
    """
    # Step 1: Prepare context (NO LLM USAGE)
    context = prepare_validation_context(image_id, receipt_id, labels)

    # Step 2: Run minimal graph (ONLY 1 LLM CALL)
    graph = create_minimal_validation_graph()

    initial_state = {
        "formatted_prompt": context["formatted_prompt"],
        "validation_targets": context["validation_targets"],
        "completed": False,
    }

    final_state = await graph.ainvoke(initial_state)

    # Step 3: Update database if needed (NO LLM USAGE)
    db_result = None
    if not skip_database_update and final_state.get("completed"):
        db_result = update_database_with_results(
            context, final_state.get("validation_results", [])
        )

    return {
        "success": final_state.get("completed", False),
        "validation_results": final_state.get("validation_results", []),
        "error": final_state.get("error"),
        "database_updated": db_result is not None,
        "database_result": db_result,
        "context_prepared": True,
        "llm_calls": 1 if final_state.get("completed") else 0,
    }


# ============================================================================
# Context Caching for Even Better Efficiency
# ============================================================================


class CachedValidator:
    """
    Validator with context caching to minimize database calls too
    """

    def __init__(self, cache_ttl_seconds: int = 300):
        self.context_cache: Dict[str, Dict[str, Any]] = {}
        self.cache_ttl = cache_ttl_seconds
        self.graph = create_minimal_validation_graph()

    async def validate_with_cache(
        self, image_id: str, receipt_id: int, labels: List[dict]
    ) -> Dict[str, Any]:
        """
        Validate with context caching
        """
        import time

        cache_key = f"{image_id}:{receipt_id}"

        # Check cache for context
        if cache_key in self.context_cache:
            cached = self.context_cache[cache_key]
            if time.time() - cached["timestamp"] < self.cache_ttl:
                context = cached["context"]
                print(f"Using cached context for {cache_key}")
            else:
                # Cache expired
                context = prepare_validation_context(
                    image_id, receipt_id, labels
                )
                self.context_cache[cache_key] = {
                    "context": context,
                    "timestamp": time.time(),
                }
        else:
            # Not in cache
            context = prepare_validation_context(image_id, receipt_id, labels)
            self.context_cache[cache_key] = {
                "context": context,
                "timestamp": time.time(),
            }

        # Run validation with prepared context
        initial_state = {
            "formatted_prompt": context["formatted_prompt"],
            "validation_targets": context["validation_targets"],
            "completed": False,
        }

        final_state = await self.graph.ainvoke(initial_state)

        return {
            "success": final_state.get("completed", False),
            "validation_results": final_state.get("validation_results", []),
            "error": final_state.get("error"),
            "used_cache": cache_key in self.context_cache,
        }


if __name__ == "__main__":
    import asyncio

    async def test():
        print("Testing optimized validation...")

        # Test data
        test_labels = [
            {
                "image_id": "TEST_001",
                "receipt_id": 12345,
                "line_id": 1,
                "word_id": 1,
                "label": "MERCHANT_NAME",
                "validation_status": "NONE",
            }
        ]

        # This will only make 1 LLM call!
        result = await validate_receipt_labels_optimized(
            "TEST_001",
            12345,
            test_labels,
            skip_database_update=True,  # For testing
        )

        print(f"Result: {result}")
        print(f"LLM calls made: {result.get('llm_calls', 0)}")

    asyncio.run(test())
