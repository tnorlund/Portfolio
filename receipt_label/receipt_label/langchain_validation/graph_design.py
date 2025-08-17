"""
Optimized LangChain Graph Design - Minimal LLM Usage
====================================================

This version prepares all context OUTSIDE the graph, so the graph
only handles LLM validation. This minimizes Ollama API calls.
"""

from typing import TypedDict, List, Optional, Any, Dict
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, END

# Removed json import - using structured output only
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
    all_labels = []  # Labels would need separate query

    metadata = receipt_details  # Use receipt details as metadata source

    # Convert to dicts
    receipt_lines = [line.__dict__ for line in lines]
    receipt_words = [word.__dict__ for word in words]
    receipt_metadata = {
        "merchant_name": getattr(metadata, "merchant_name", "Unknown"),
        "image_id": image_id,
        "receipt_id": receipt_id,
    }
    all_labels_dict = [label.__dict__ for label in all_labels]

    # Build validation targets with similarity context
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

            targets.append(
                {
                    "id": f"IMAGE#{label['image_id']}#"
                    f"RECEIPT#{label['receipt_id']:05d}#"
                    f"LINE#{label['line_id']:05d}#"
                    f"WORD#{label['word_id']:05d}#"
                    f"LABEL#{label['label']}",
                    "text": word["text"],
                    "proposed_label": label["label"],
                    "similar_words": similar_words,  # NEW: Add similarity context
                }
            )

    # Format receipt text
    lines_obj = [ReceiptLine(**line) for line in receipt_lines]
    receipt_text = _format_receipt_lines(lines_obj)

    # Format targets for display with similarity context
    targets_text = "\n".join(
        [
            f"- ID: {target['id']}"
            f"\n  Text: '{target['text']}'"
            f"\n  Proposed Label: {target['proposed_label']}"
            f"\n  Similar Words: {format_similar_examples(target['similar_words'])}"
            for target in targets
        ]
    )

    # Create the validation prompt with similarity guidance
    prompt = f"""You are validating receipt labels with similarity context to help your decisions.

MERCHANT: {receipt_metadata['merchant_name']}

ALLOWED LABELS: {', '.join(CORE_LABELS.keys())}

TARGETS TO VALIDATE:
{targets_text}

RECEIPT TEXT:
{receipt_text}

VALIDATION GUIDANCE:
- Use the "Similar Words" to understand how comparable words are labeled across different receipts
- If similar words consistently use a certain label, consider that as supporting evidence
- Pay attention to merchant context - words may be labeled differently across merchants
- The similarity score shows how semantically related the words are (higher = more similar)

TASK: For each target, determine if the proposed_label is correct using both the receipt context and similarity patterns.
Return the results in the structured format. Each result must have:
- "id": the exact id from the target
- "is_valid": true if the label is correct, false otherwise
- "correct_label": (only if is_valid is false) the correct label from ALLOWED LABELS"""

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
# LLM Configuration
# ============================================================================


def get_ollama_llm() -> ChatOllama:
    """Get configured Ollama instance for structured output"""
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    api_key = os.getenv("OLLAMA_API_KEY")
    model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

    llm = ChatOllama(
        model=model,
        base_url=base_url,
        temperature=0,
        # No format="json" - using structured output only
    )

    if api_key:
        llm.auth = {"api_key": api_key}

    return llm


# ============================================================================
# Minimal Graph Nodes (only 2 nodes now!)
# ============================================================================


async def validate_with_ollama(
    state: MinimalValidationState,
) -> MinimalValidationState:
    """
    Node 1: Call Ollama to validate using structured output ONLY
    This is the ONLY node that uses the LLM
    """
    try:
        llm = get_ollama_llm()

        # Attempt 1: Try with_structured_output (cleanest approach)
        try:
            structured_llm = llm.with_structured_output(ValidationResponse)
            response = await structured_llm.ainvoke(state["formatted_prompt"])

            # Response is already a ValidationResponse object
            state["validation_response"] = response
            state["validation_results"] = [r.dict() for r in response.results]
            state["completed"] = True
            return state

        except (AttributeError, NotImplementedError):
            # Model doesn't support with_structured_output, try parser approach
            pass

        # Attempt 2: Try with output parser
        parser = PydanticOutputParser(pydantic_object=ValidationResponse)

        # Create prompt with format instructions
        prompt_template = PromptTemplate(
            template="{system_message}\n\n{query}\n\n{format_instructions}",
            input_variables=["system_message", "query"],
            partial_variables={
                "format_instructions": parser.get_format_instructions()
            },
        )

        formatted = prompt_template.format(
            system_message="You are a receipt validation assistant. Analyze the receipt labels and respond with the required structure.",
            query=state["formatted_prompt"],
        )

        response = await llm.ainvoke(formatted)
        parsed_response = parser.parse(response.content)

        state["validation_response"] = parsed_response
        state["validation_results"] = [
            r.dict() for r in parsed_response.results
        ]
        state["completed"] = True
        return state

    except Exception as e:
        state["error"] = f"Structured validation failed: {e}"
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
