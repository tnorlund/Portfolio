"""
LangChain Graph Design for Real-Time Receipt Validation
"""
from typing import TypedDict, List, Dict, Optional, Annotated
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field


# ============================================================================
# State Definition
# ============================================================================

class ValidationState(TypedDict):
    """State for the validation graph"""
    # Input
    image_id: str
    receipt_id: int
    labels_to_validate: List[dict]  # ReceiptWordLabel objects
    
    # Context (fetched)
    receipt_lines: List[dict]
    receipt_words: List[dict]
    receipt_metadata: dict
    all_labels: List[dict]  # For second-pass validation
    
    # Processing
    first_pass_labels: List[dict]
    second_pass_labels: List[dict]
    formatted_prompt: str
    
    # Results
    validation_results: List[dict]
    messages: List[BaseMessage]
    
    # Status
    error: Optional[str]
    completed: bool


# ============================================================================
# Tool Definitions for LangChain
# ============================================================================

class LabelValidationResult(BaseModel):
    """Single label validation result"""
    id: str = Field(description="The original label identifier")
    is_valid: bool = Field(description="True if the proposed label is correct")
    correct_label: Optional[str] = Field(
        description="If invalid, the suggested correct label",
        default=None
    )

class ValidateLabelsInput(BaseModel):
    """Input for the validate_labels tool"""
    results: List[LabelValidationResult] = Field(
        description="Array of validation results for receipt labels"
    )

def validate_labels_tool(results: List[LabelValidationResult]) -> dict:
    """
    Tool for validating multiple receipt-word labels in one batch.
    This replaces the OpenAI function calling pattern.
    """
    return {
        "validated_count": len(results),
        "results": [r.dict() for r in results]
    }


# ============================================================================
# Graph Node Functions
# ============================================================================

async def fetch_receipt_context(state: ValidationState) -> ValidationState:
    """
    Node 1: Fetch all receipt context from DynamoDB
    Equivalent to get_receipt_details() in current implementation
    """
    from receipt_label.utils import get_client_manager
    
    client_manager = get_client_manager()
    
    # Fetch receipt details
    (
        _,
        lines,
        words,
        _,
        _,
        labels,
    ) = client_manager.dynamo.getReceiptDetails(
        state["image_id"], 
        state["receipt_id"]
    )
    
    metadata = client_manager.dynamo.getReceiptMetadata(
        state["image_id"], 
        state["receipt_id"]
    )
    
    state["receipt_lines"] = [line.__dict__ for line in lines]
    state["receipt_words"] = [word.__dict__ for word in words]
    state["receipt_metadata"] = metadata.__dict__
    state["all_labels"] = [label.__dict__ for label in labels]
    
    return state


async def split_validation_passes(state: ValidationState) -> ValidationState:
    """
    Node 2: Split labels into first-pass and second-pass
    Based on whether they have previous validation attempts
    """
    from receipt_dynamo.constants import ValidationStatus
    
    first_pass = []
    second_pass = []
    
    for label in state["labels_to_validate"]:
        # Check if this word has any invalid labels
        other_labels = [
            l for l in state["all_labels"]
            if l["word_id"] == label["word_id"] 
            and l["line_id"] == label["line_id"]
        ]
        
        has_invalid = any(
            l["validation_status"] == ValidationStatus.INVALID.value
            for l in other_labels
        )
        
        if has_invalid:
            # Add invalid history for context
            label["invalid_labels"] = [
                l["label"] for l in other_labels
                if l["validation_status"] == ValidationStatus.INVALID.value
            ]
            second_pass.append(label)
        else:
            first_pass.append(label)
    
    state["first_pass_labels"] = first_pass
    state["second_pass_labels"] = second_pass
    
    return state


async def format_validation_prompt(state: ValidationState) -> ValidationState:
    """
    Node 3: Format the validation prompt
    Adapts _format_prompt() for streaming context
    """
    from receipt_label.completion._format_prompt import (
        _format_receipt_lines,
        CORE_LABELS
    )
    import json
    
    # Build targets array
    targets = []
    for label in state["first_pass_labels"] + state["second_pass_labels"]:
        word = next(
            w for w in state["receipt_words"]
            if w["line_id"] == label["line_id"] 
            and w["word_id"] == label["word_id"]
        )
        
        target = {
            "id": (
                f"IMAGE#{label['image_id']}#"
                f"RECEIPT#{label['receipt_id']:05d}#"
                f"LINE#{label['line_id']:05d}#"
                f"WORD#{label['word_id']:05d}#"
                f"LABEL#{label['label']}#"
                f"VALIDATION_STATUS#{label['validation_status']}"
            ),
            "text": word["text"],
            "line_id": label["line_id"],
            "proposed_label": label["label"]
        }
        
        if "invalid_labels" in label:
            target["invalid_labels"] = label["invalid_labels"]
            
        targets.append(target)
    
    # Format the prompt
    prompt_lines = []
    prompt_lines.append(f"Validate labels for {state['receipt_metadata']['merchant_name']} receipt.")
    prompt_lines.append("\nTargets to validate:")
    prompt_lines.append(json.dumps(targets, indent=2))
    prompt_lines.append("\nAllowed labels:")
    prompt_lines.append(", ".join(CORE_LABELS.keys()))
    prompt_lines.append("\nReceipt context:")
    
    # Format receipt lines
    from receipt_dynamo.entities import ReceiptLine
    lines = [ReceiptLine(**line) for line in state["receipt_lines"]]
    prompt_lines.append(_format_receipt_lines(lines))
    
    prompt_lines.append("\nUse the validate_labels tool to provide validation results.")
    
    state["formatted_prompt"] = "\n".join(prompt_lines)
    
    return state


async def call_llm_with_tools(state: ValidationState) -> ValidationState:
    """
    Node 4: Call LLM with tool calling capability
    Replaces OpenAI batch API call
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, AIMessage
    
    # Initialize LLM with tools
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0
    ).bind_tools([ValidateLabelsInput])
    
    # Create message
    message = HumanMessage(content=state["formatted_prompt"])
    state["messages"] = [message]
    
    # Get response with tool call
    response = await llm.ainvoke([message])
    state["messages"].append(response)
    
    # Extract tool calls
    if response.tool_calls:
        tool_call = response.tool_calls[0]
        if tool_call["name"] == "ValidateLabelsInput":
            state["validation_results"] = tool_call["args"]["results"]
    
    return state


async def update_validation_results(state: ValidationState) -> ValidationState:
    """
    Node 5: Update DynamoDB with validation results
    Replaces update_valid_labels() and update_invalid_labels()
    """
    from receipt_label.utils import get_client_manager
    from receipt_dynamo.constants import ValidationStatus
    
    client_manager = get_client_manager()
    
    valid_labels = []
    invalid_labels = []
    
    for result in state["validation_results"]:
        # Parse the ID to get label coordinates
        parts = result["id"].split("#")
        kv = {parts[i]: parts[i+1] for i in range(0, len(parts)-1, 2)}
        
        # Find the original label
        label = next(
            l for l in state["labels_to_validate"]
            if l["image_id"] == kv["IMAGE"]
            and l["receipt_id"] == int(kv["RECEIPT"])
            and l["line_id"] == int(kv["LINE"])
            and l["word_id"] == int(kv["WORD"])
            and l["label"] == kv["LABEL"]
        )
        
        if result["is_valid"]:
            label["validation_status"] = ValidationStatus.VALID.value
            valid_labels.append(label)
        else:
            label["validation_status"] = ValidationStatus.INVALID.value
            if result.get("correct_label"):
                label["suggested_label"] = result["correct_label"]
            invalid_labels.append(label)
    
    # Update in DynamoDB
    if valid_labels:
        client_manager.dynamo.updateReceiptWordLabels(valid_labels)
    if invalid_labels:
        client_manager.dynamo.updateReceiptWordLabels(invalid_labels)
    
    state["completed"] = True
    return state


async def handle_error(state: ValidationState) -> ValidationState:
    """
    Error handling node
    """
    import traceback
    state["error"] = traceback.format_exc()
    state["completed"] = False
    return state


# ============================================================================
# Graph Construction
# ============================================================================

def create_validation_graph() -> StateGraph:
    """
    Create the LangChain graph for real-time validation
    """
    # Initialize graph
    graph = StateGraph(ValidationState)
    
    # Add nodes
    graph.add_node("fetch_context", fetch_receipt_context)
    graph.add_node("split_passes", split_validation_passes)
    graph.add_node("format_prompt", format_validation_prompt)
    graph.add_node("call_llm", call_llm_with_tools)
    graph.add_node("update_results", update_validation_results)
    graph.add_node("handle_error", handle_error)
    
    # Add edges
    graph.add_edge("fetch_context", "split_passes")
    graph.add_edge("split_passes", "format_prompt")
    graph.add_edge("format_prompt", "call_llm")
    graph.add_edge("call_llm", "update_results")
    graph.add_edge("update_results", END)
    
    # Set entry point
    graph.set_entry_point("fetch_context")
    
    # Add error handling
    graph.add_conditional_edges(
        "fetch_context",
        lambda x: "handle_error" if x.get("error") else "split_passes",
        {
            "handle_error": "handle_error",
            "split_passes": "split_passes"
        }
    )
    
    return graph.compile()


# ============================================================================
# Usage Example
# ============================================================================

async def validate_receipt_labels(
    image_id: str,
    receipt_id: int,
    labels: List[dict]
) -> dict:
    """
    Main entry point for real-time validation
    """
    # Create graph
    app = create_validation_graph()
    
    # Initialize state
    initial_state = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "labels_to_validate": labels,
        "completed": False
    }
    
    # Run graph
    result = await app.ainvoke(initial_state)
    
    return {
        "success": result["completed"],
        "validation_results": result.get("validation_results", []),
        "error": result.get("error")
    }