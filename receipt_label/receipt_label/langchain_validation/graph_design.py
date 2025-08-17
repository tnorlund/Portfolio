"""
Fixed LangChain Graph Design - Actually uses LangChain + Ollama!
================================================================

This version properly uses LangChain's ChatOllama with LangGraph
instead of making raw HTTP calls.
"""

from typing import TypedDict, List, Optional, Any, Dict
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, END
import json
import os


# ============================================================================
# State Definition (same as before)
# ============================================================================


class ValidationState(TypedDict):
    """State that flows through the validation graph"""

    # Input
    image_id: str
    receipt_id: int
    labels_to_validate: List[dict]

    # Context (fetched from DB)
    receipt_lines: List[dict]
    receipt_words: List[dict]
    receipt_metadata: dict
    all_labels: List[dict]

    # Processing
    formatted_prompt: str

    # Results
    validation_results: List[dict]

    # Status
    error: Optional[str]
    completed: bool


# ============================================================================
# The LLM Instance - Created ONCE and reused
# ============================================================================


def get_ollama_llm() -> ChatOllama:
    """
    Get the Ollama LLM instance configured from environment

    For Ollama Turbo:
        export OLLAMA_BASE_URL="https://api.ollama.com"
        export OLLAMA_API_KEY="your-api-key"
        export OLLAMA_MODEL="turbo"

    For local Ollama:
        export OLLAMA_BASE_URL="http://localhost:11434"
        export OLLAMA_MODEL="llama3.1:8b"
    """
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    api_key = os.getenv("OLLAMA_API_KEY")
    model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

    # Create the LangChain Ollama instance
    llm = ChatOllama(
        model=model,
        base_url=base_url,
        temperature=0,  # Deterministic for validation
        format="json",  # Request JSON output
    )

    # If using Ollama Turbo, set the API key
    if api_key:
        llm.auth = {"api_key": api_key}

    return llm


# ============================================================================
# Graph Node Functions
# ============================================================================


async def fetch_receipt_context(state: ValidationState) -> ValidationState:
    """
    Node 1: Fetch receipt data from DynamoDB
    """
    from receipt_label.utils import get_client_manager

    client_manager = get_client_manager()

    # Get receipt details
    _, lines, words, _, _, labels = client_manager.dynamo.getReceiptDetails(
        state["image_id"], state["receipt_id"]
    )

    metadata = client_manager.dynamo.getReceiptMetadata(
        state["image_id"], state["receipt_id"]
    )

    state["receipt_lines"] = [line.__dict__ for line in lines]
    state["receipt_words"] = [word.__dict__ for word in words]
    state["receipt_metadata"] = metadata.__dict__
    state["all_labels"] = [label.__dict__ for label in labels]

    return state


async def format_validation_prompt(state: ValidationState) -> ValidationState:
    """
    Node 2: Create the prompt for validation
    """
    from receipt_label.completion._format_prompt import (
        _format_receipt_lines,
        CORE_LABELS,
    )
    from receipt_dynamo.entities import ReceiptLine

    # Build targets to validate
    targets = []
    for label in state["labels_to_validate"]:
        word = next(
            w
            for w in state["receipt_words"]
            if w["line_id"] == label["line_id"]
            and w["word_id"] == label["word_id"]
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
            }
        )

    # Format receipt lines for context
    lines = [ReceiptLine(**line) for line in state["receipt_lines"]]
    receipt_text = _format_receipt_lines(lines)

    # Create the prompt
    prompt = f"""You are validating receipt labels. 

MERCHANT: {state['receipt_metadata']['merchant_name']}

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

    state["formatted_prompt"] = prompt
    return state


async def validate_with_ollama(state: ValidationState) -> ValidationState:
    """
    Node 3: Call Ollama using LangChain (properly!)
    """
    try:
        # Get the configured LLM
        llm = get_ollama_llm()

        # Create the messages
        messages = [
            SystemMessage(
                content="You are a receipt validation assistant. Always respond with valid JSON."
            ),
            HumanMessage(content=state["formatted_prompt"]),
        ]

        # Call the LLM using LangChain
        response = await llm.ainvoke(messages)

        # Parse the response
        try:
            # response.content contains the JSON string
            result = json.loads(response.content)
            state["validation_results"] = result.get("results", [])
        except json.JSONDecodeError as e:
            state["error"] = f"Failed to parse LLM response: {e}"
            state["validation_results"] = []

    except Exception as e:
        state["error"] = f"LLM call failed: {e}"
        state["validation_results"] = []

    return state


async def update_database(state: ValidationState) -> ValidationState:
    """
    Node 4: Update DynamoDB with validation results
    """
    from receipt_label.utils import get_client_manager
    from receipt_dynamo.constants import ValidationStatus

    if not state["validation_results"]:
        state["completed"] = False
        return state

    client_manager = get_client_manager()

    valid_labels = []
    invalid_labels = []

    for result in state["validation_results"]:
        # Parse the ID to find the original label
        parts = result["id"].split("#")
        kv = {parts[i]: parts[i + 1] for i in range(0, len(parts) - 1, 2)}

        # Find matching label
        label = next(
            (
                l
                for l in state["labels_to_validate"]
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

    # Update the database
    if valid_labels:
        client_manager.dynamo.updateReceiptWordLabels(valid_labels)
    if invalid_labels:
        client_manager.dynamo.updateReceiptWordLabels(invalid_labels)

    state["completed"] = True
    return state


# ============================================================================
# Build the Graph
# ============================================================================


def create_validation_graph() -> Any:
    """
    Create the LangGraph workflow for validation
    """
    # Create the graph with our state
    graph = StateGraph(ValidationState)

    # Add all the nodes
    graph.add_node("fetch_context", fetch_receipt_context)
    graph.add_node("format_prompt", format_validation_prompt)
    graph.add_node("validate", validate_with_ollama)
    graph.add_node("update_db", update_database)

    # Define the flow
    graph.add_edge("fetch_context", "format_prompt")
    graph.add_edge("format_prompt", "validate")
    graph.add_edge("validate", "update_db")
    graph.add_edge("update_db", END)

    # Set where to start
    graph.set_entry_point("fetch_context")

    # Compile and return
    return graph.compile()


# ============================================================================
# Simple Usage Function
# ============================================================================


async def validate_receipt_labels(
    image_id: str, receipt_id: int, labels: List[dict]
) -> Dict[str, Any]:
    """
    Simple function to validate receipt labels

    Args:
        image_id: The receipt image ID
        receipt_id: The receipt ID
        labels: List of labels to validate, each with:
            - line_id, word_id, label, validation_status

    Returns:
        Dictionary with:
            - success: bool
            - validation_results: list of results
            - error: optional error message
    """
    # Create the graph
    app = create_validation_graph()

    # Set initial state
    initial_state = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "labels_to_validate": labels,
        "completed": False,
    }

    # Run the graph
    final_state = await app.ainvoke(initial_state)

    return {
        "success": final_state.get("completed", False),
        "validation_results": final_state.get("validation_results", []),
        "error": final_state.get("error"),
    }


# ============================================================================
# Test Function
# ============================================================================


async def test_ollama_connection():
    """Test if Ollama is working with LangChain"""
    try:
        llm = get_ollama_llm()
        messages = [
            HumanMessage(content="Say 'Ollama is working!' in JSON format")
        ]
        response = await llm.ainvoke(messages)
        print(f"✅ Ollama test successful: {response.content}")
        return True
    except Exception as e:
        print(f"❌ Ollama test failed: {e}")
        return False


if __name__ == "__main__":
    import asyncio

    # Test the connection
    asyncio.run(test_ollama_connection())
