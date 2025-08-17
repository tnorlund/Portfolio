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
import json
import os


# ============================================================================
# Context Preparation (OUTSIDE the graph - no LLM usage)
# ============================================================================


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

    _, lines, words, _, _, all_labels = (
        client_manager.dynamo.getReceiptDetails(image_id, receipt_id)
    )

    metadata = client_manager.dynamo.getReceiptMetadata(image_id, receipt_id)

    # Convert to dicts
    receipt_lines = [line.__dict__ for line in lines]
    receipt_words = [word.__dict__ for word in words]
    receipt_metadata = metadata.__dict__
    all_labels_dict = [label.__dict__ for label in all_labels]

    # Build validation targets
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
            targets.append(
                {
                    "id": f"IMAGE#{label['image_id']}#"
                    f"RECEIPT#{label['receipt_id']:05d}#"
                    f"LINE#{label['line_id']:05d}#"
                    f"WORD#{label['word_id']:05d}#"
                    f"LABEL#{label['label']}",
                    "text": word["text"],
                    "proposed_label": label["label"],
                }
            )

    # Format receipt text
    lines_obj = [ReceiptLine(**line) for line in receipt_lines]
    receipt_text = _format_receipt_lines(lines_obj)

    # Create the validation prompt
    prompt = f"""You are validating receipt labels. 

MERCHANT: {receipt_metadata['merchant_name']}

ALLOWED LABELS: {', '.join(CORE_LABELS.keys())}

TARGETS TO VALIDATE:
{json.dumps(targets, indent=2)}

RECEIPT TEXT:
{receipt_text}

TASK: For each target, determine if the proposed_label is correct.
Return a JSON object with a "results" array. Each result must have:
- "id": the exact id from the target
- "is_valid": true if the label is correct, false otherwise
- "correct_label": (only if is_valid is false) the correct label from ALLOWED LABELS

Example response:
{{"results": [
    {{"id": "IMAGE#abc#RECEIPT#00001#...", "is_valid": true}},
    {{"id": "IMAGE#xyz#RECEIPT#00002#...", "is_valid": false, "correct_label": "TOTAL"}}
]}}"""

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

    # Status
    error: Optional[str]
    completed: bool


# ============================================================================
# LLM Configuration
# ============================================================================


def get_ollama_llm() -> ChatOllama:
    """Get configured Ollama instance"""
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    api_key = os.getenv("OLLAMA_API_KEY")
    model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

    llm = ChatOllama(
        model=model,
        base_url=base_url,
        temperature=0,
        format="json",
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
    Node 1: Call Ollama to validate
    This is the ONLY node that uses the LLM
    """
    try:
        llm = get_ollama_llm()

        messages = [
            SystemMessage(
                content="You are a receipt validation assistant. Always respond with valid JSON."
            ),
            HumanMessage(content=state["formatted_prompt"]),
        ]

        # The ONLY LLM call in the entire system
        response = await llm.ainvoke(messages)

        try:
            result = json.loads(response.content)
            state["validation_results"] = result.get("results", [])
            state["completed"] = True
        except json.JSONDecodeError as e:
            state["error"] = f"Failed to parse LLM response: {e}"
            state["validation_results"] = []
            state["completed"] = False

    except Exception as e:
        state["error"] = f"LLM call failed: {e}"
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

    # Add any post-processing here if needed
    # For now, just pass through

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
