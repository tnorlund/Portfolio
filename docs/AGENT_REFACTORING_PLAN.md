# Agent Code Refactoring Plan

## Current Issues

### 1. Code Duplication
- **Retry logic**: Duplicated in 3+ workflows (harmonizer, metadata finder, CoVe)
- **LLM creation**: Duplicated in 9+ workflows
- **Receipt fetching**: Fallback logic duplicated
- **Address sanitization**: In multiple places (agent tool, Lambda handler)

### 2. Large Files
- **harmonizer_workflow.py**: 2,312 lines (tools + graph + runner)
- **receipt_metadata_finder_workflow.py**: 696 lines
- **cove_text_consistency_workflow.py**: 1,437 lines

### 3. Mixed Concerns
- Tools, graph creation, and runner functions all in one file
- No clear separation of concerns

### 4. No Shared Utilities
- Common patterns not extracted to utilities
- Each workflow reinvents the wheel

## Refactoring Strategy

### Phase 1: Extract Shared Utilities

#### 1.1 Create `receipt_agent/utils/agent_common.py`

**Purpose**: Shared utilities for all agent workflows

```python
"""
Common utilities for agent workflows.
"""

import logging
import random
import time
from typing import Any, Callable, Optional

from langchain_ollama import ChatOllama

from receipt_agent.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def create_ollama_llm(
    settings: Optional[Settings] = None,
    temperature: float = 0.0,
    timeout: int = 120,
) -> ChatOllama:
    """
    Create a ChatOllama LLM instance with standard configuration.

    Args:
        settings: Optional settings (uses get_settings() if None)
        temperature: LLM temperature (default 0.0 for deterministic)
        timeout: Request timeout in seconds

    Returns:
        Configured ChatOllama instance
    """
    if settings is None:
        settings = get_settings()

    api_key = settings.ollama_api_key.get_secret_value()

    return ChatOllama(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        client_kwargs={
            "headers": ({"Authorization": f"Bearer {api_key}"} if api_key else {}),
            "timeout": timeout,
        },
        temperature=temperature,
    )


def create_agent_node_with_retry(
    llm: ChatOllama,
    agent_name: str = "agent",
    max_retries: int = 5,
    base_delay: float = 2.0,
    max_wait: float = 30.0,
) -> Callable:
    """
    Create an agent node function with retry logic.

    This handles:
    - Rate limit errors (429) - fail fast
    - Connection errors - retry with exponential backoff
    - Server errors (5xx) - retry with exponential backoff
    - Timeout errors - retry with exponential backoff

    Args:
        llm: The LLM to invoke
        agent_name: Name for logging (e.g., "harmonizer", "metadata_finder")
        max_retries: Maximum number of retries
        base_delay: Base delay in seconds for exponential backoff
        max_wait: Maximum wait time in seconds (caps exponential backoff)

    Returns:
        Agent node function that takes state and returns dict
    """
    def agent_node(state: Any) -> dict:
        """Call the LLM to decide next action with retry logic."""
        messages = state.messages

        last_error = None
        for attempt in range(max_retries):
            try:
                response = llm.invoke(messages)

                if hasattr(response, "tool_calls") and response.tool_calls:
                    logger.debug(
                        f"{agent_name} tool calls: "
                        f"{[tc['name'] for tc in response.tool_calls]}"
                    )

                return {"messages": [response]}

            except Exception as e:
                last_error = e
                error_str = str(e)

                # Check if this is a rate limit (429) - fail fast, don't retry
                is_rate_limit = (
                    "429" in error_str
                    or "rate limit" in error_str.lower()
                    or "rate_limit" in error_str.lower()
                    or "too many concurrent requests" in error_str.lower()
                    or "too many requests" in error_str.lower()
                    or "OllamaRateLimitError" in error_str
                )

                if is_rate_limit:
                    logger.warning(
                        f"Rate limit detected in {agent_name} (attempt {attempt + 1}): "
                        f"{error_str[:200]}. Failing immediately to trigger circuit breaker."
                    )
                    raise RuntimeError(
                        f"Rate limit error in {agent_name}: {error_str}"
                    ) from e

                # Check for connection errors
                is_connection_error = (
                    "status code: -1" in error_str
                    or ("status_code" in error_str and "-1" in error_str)
                    or (
                        "connection" in error_str.lower()
                        and (
                            "refused" in error_str.lower()
                            or "reset" in error_str.lower()
                            or "failed" in error_str.lower()
                            or "error" in error_str.lower()
                        )
                    )
                    or "dns" in error_str.lower()
                    or "name resolution" in error_str.lower()
                    or (
                        "network" in error_str.lower()
                        and "unreachable" in error_str.lower()
                    )
                )

                # For other retryable errors, use exponential backoff
                is_retryable = (
                    is_connection_error
                    or "500" in error_str
                    or "502" in error_str
                    or "503" in error_str
                    or "504" in error_str
                    or "Internal Server Error" in error_str
                    or "internal server error" in error_str.lower()
                    or "service unavailable" in error_str.lower()
                    or "timeout" in error_str.lower()
                    or "timed out" in error_str.lower()
                )

                if is_retryable and attempt < max_retries - 1:
                    # Exponential backoff with jitter
                    jitter = random.uniform(0, base_delay)
                    wait_time = (base_delay * (2 ** attempt)) + jitter
                    wait_time = min(wait_time, max_wait)

                    error_type = "connection" if is_connection_error else "server"
                    logger.warning(
                        f"Ollama {error_type} error in {agent_name} "
                        f"(attempt {attempt + 1}/{max_retries}): {error_str[:200]}. "
                        f"Retrying in {wait_time:.1f}s..."
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    # Not retryable or max retries reached
                    if attempt >= max_retries - 1:
                        logger.error(
                            f"Ollama LLM call failed after {max_retries} attempts "
                            f"in {agent_name}: {error_str}"
                        )
                    raise RuntimeError(
                        f"Failed to get LLM response in {agent_name}: {error_str}"
                    ) from e

        # Should never reach here
        raise RuntimeError(
            f"Unexpected error: Failed to get LLM response in {agent_name}"
        ) from last_error

    return agent_node
```

**Benefits**:
- Eliminates ~100 lines of duplication per workflow
- Consistent retry behavior across all agents
- Easier to update retry logic in one place

#### 1.2 Create `receipt_agent/utils/receipt_fetching.py`

**Purpose**: Shared utilities for fetching receipt data with fallbacks

```python
"""
Utilities for fetching receipt data with fallback methods.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def fetch_receipt_details_with_fallback(
    dynamo_client: Any,
    image_id: str,
    receipt_id: int,
) -> Optional[Any]:
    """
    Fetch receipt details using primary method, with fallback methods.

    Handles:
    - Trailing characters in image_id (sanitizes)
    - Missing receipt entity (tries direct line/word fetch)
    - Multiple image_id variants

    Args:
        dynamo_client: DynamoDB client
        image_id: Image ID (may have trailing characters like '?')
        receipt_id: Receipt ID

    Returns:
        ReceiptDetails if successful, None otherwise
    """
    # Implementation from harmonizer_workflow.py:215-360
    # ... (extract the fallback logic)
    pass
```

**Benefits**:
- Eliminates ~150 lines of duplication
- Consistent error handling across workflows
- Easier to improve fallback logic

#### 1.3 Create `receipt_agent/utils/address_validation.py`

**Purpose**: Shared utilities for address validation and sanitization

```python
"""
Utilities for address validation and sanitization.
"""

import re
from typing import Optional


def is_address_like(name: Optional[str]) -> bool:
    """
    Check if a name looks like an address rather than a business name.

    Args:
        name: Name to check

    Returns:
        True if name looks like an address
    """
    if not name:
        return False

    name_lower = name.lower().strip()

    # Check if it starts with a number
    if name_lower and name_lower[0].isdigit():
        street_indicators = [
            "st", "street", "ave", "avenue", "blvd", "boulevard",
            "rd", "road", "dr", "drive", "ln", "lane", "way",
            "ct", "court", "pl", "place", "cir", "circle",
        ]
        if any(indicator in name_lower for indicator in street_indicators):
            return True

    return False


def sanitize_address(
    address: Optional[str],
    previous_value: Optional[str] = None,
) -> Optional[str]:
    """
    Sanitize an address to remove reasoning text and fix malformed ZIPs.

    Args:
        address: Address to sanitize
        previous_value: Previous address value (returned if sanitization fails)

    Returns:
        Sanitized address, previous_value, or None
    """
    if not address:
        return previous_value

    cleaned = address.strip().strip('"')
    lower = cleaned.lower()

    # Reject addresses that clearly contain reasoning/commentary
    bad_markers = ["?", "actually need", "lind0"]  # observed typo
    if any(marker in lower for marker in bad_markers):
        return previous_value

    # Fix malformed ZIPs like 913001 → 91301 (truncate extra trailing digits)
    # Pattern: find 5 digits followed by 1-2 extra digits at the end
    cleaned = re.sub(r"(\d{5})(\d{1,2})(?=\D*$)", r"\1", cleaned)

    return cleaned


def is_clean_address(address: str) -> bool:
    """
    Check if an address is clean (no reasoning/commentary, no malformed ZIPs).

    Args:
        address: Address to check

    Returns:
        True if address is clean
    """
    if not address:
        return True

    lower = address.lower()

    # Reject if contains reasoning/commentary
    if "?" in address or "actually need" in lower or "lind0" in lower:
        return False

    # Reject obviously malformed ZIPs (6+ consecutive digits)
    if re.search(r"\d{6,}", address.replace(" ", "")):
        return False

    return True
```

**Benefits**:
- Eliminates duplication between agent tool and Lambda handler
- Consistent address validation across codebase
- Easier to add new validation rules

### Phase 2: Extract Tools to Separate Modules

#### 2.1 Create `receipt_agent/tools/harmonizer_tools.py`

**Purpose**: Extract all harmonizer tools from workflow file

**Structure**:
```python
"""
Tools for the harmonizer agent.
"""

from typing import Any, Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from receipt_agent.utils.receipt_fetching import fetch_receipt_details_with_fallback
from receipt_agent.utils.address_validation import is_address_like, is_clean_address
from receipt_agent.utils.receipt_text import format_receipt_text_receipt_space


def create_harmonizer_tools(
    dynamo_client: Any,
    places_api: Optional[Any] = None,
    group_data: Optional[dict] = None,
    chroma_client: Optional[Any] = None,
    embed_fn: Optional[Callable] = None,
    chromadb_bucket: Optional[str] = None,
) -> tuple[list[Any], dict]:
    """
    Create tools for the harmonizer agent.

    Returns:
        (tools, state_holder)
    """
    # Move all @tool functions here from harmonizer_workflow.py
    # This reduces harmonizer_workflow.py from 2,312 lines to ~500 lines
    pass
```

**Benefits**:
- Separates tools (business logic) from workflow (orchestration)
- Easier to test tools independently
- Reduces workflow file size by ~1,500 lines

#### 2.2 Create `receipt_agent/tools/metadata_finder_tools.py`

**Purpose**: Extract metadata finder tools (if any unique ones)

**Note**: Most tools come from `agentic.py`, but submission tool could be extracted.

### Phase 3: Refactor Workflow Files

#### 3.1 Simplified `harmonizer_workflow.py`

**After refactoring**:
```python
"""
Agentic workflow for harmonizing receipt metadata within a place_id group.
"""

from typing import Any, Optional
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from receipt_agent.config.settings import get_settings
from receipt_agent.utils.agent_common import (
    create_ollama_llm,
    create_agent_node_with_retry,
)
from receipt_agent.tools.harmonizer_tools import create_harmonizer_tools
from receipt_agent.state.models import HarmonizerAgentState  # Move to state/models.py


HARMONIZER_PROMPT = """..."""  # Keep prompt here or move to prompts/


def create_harmonizer_graph(
    dynamo_client: Any,
    places_api: Optional[Any] = None,
    settings: Optional[Any] = None,
    chroma_client: Optional[Any] = None,
    embed_fn: Optional[Callable] = None,
    chromadb_bucket: Optional[str] = None,
) -> tuple[Any, dict]:
    """Create the harmonizer agent workflow."""
    if settings is None:
        settings = get_settings()

    # Create tools
    tools, state_holder = create_harmonizer_tools(
        dynamo_client=dynamo_client,
        places_api=places_api,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        chromadb_bucket=chromadb_bucket,
    )

    # Create LLM
    llm = create_ollama_llm(settings=settings, temperature=0.0)
    llm = llm.bind_tools(tools)

    # Create agent node with retry logic
    agent_node = create_agent_node_with_retry(
        llm=llm,
        agent_name="harmonizer",
    )

    # Build graph
    workflow = StateGraph(HarmonizerAgentState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", ToolNode(tools))
    workflow.set_entry_point("agent")

    # Add conditional edges
    def should_continue(state: HarmonizerAgentState) -> str:
        if state_holder.get("result") is not None:
            return "end"
        if state.messages:
            last_message = state.messages[-1]
            if isinstance(last_message, AIMessage) and last_message.tool_calls:
                return "tools"
        return "end"

    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "end": END},
    )
    workflow.add_edge("tools", "agent")

    return workflow.compile(), state_holder


async def run_harmonizer_agent(
    graph: Any,
    state_holder: dict,
    place_id: str,
    receipts: list[dict],
    places_api: Optional[Any] = None,
) -> dict:
    """Run the harmonizer agent for a place_id group."""
    # Implementation (keep this, it's workflow-specific)
    pass
```

**Size reduction**: ~2,312 lines → ~300 lines (87% reduction)

### Phase 4: Organize State Models

#### 4.1 Move State Models to `receipt_agent/state/models.py`

**Current**: State models are in workflow files
**Proposed**: Centralize in `state/models.py`

```python
"""
State models for agent workflows.
"""

from typing import Annotated, Any
from pydantic import BaseModel, Field
from langgraph.graph.message import add_messages


class HarmonizerAgentState(BaseModel):
    """State for the harmonizer agent workflow."""
    place_id: str = Field(description="Google Place ID being harmonized")
    receipts: list[dict] = Field(default_factory=list)
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True


class ReceiptMetadataFinderState(BaseModel):
    """State for the receipt metadata finder workflow."""
    image_id: str = Field(description="Image ID to find metadata for")
    receipt_id: int = Field(description="Receipt ID to find metadata for")
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True


# ... other state models
```

**Benefits**:
- Single source of truth for state models
- Easier to see all state structures
- Better type checking

### Phase 5: Extract Prompts (Optional)

#### 5.1 Create `receipt_agent/prompts/` directory

**Purpose**: Separate prompts from code for easier editing

```
receipt_agent/prompts/
├── harmonizer.txt
├── metadata_finder.txt
├── cove_consistency.txt
└── __init__.py
```

**Benefits**:
- Easier to edit prompts without touching code
- Can version prompts separately
- Easier to A/B test prompts

## Implementation Order

1. **Phase 1.1**: Create `agent_common.py` (highest impact, eliminates most duplication)
2. **Phase 1.2**: Create `receipt_fetching.py`
3. **Phase 1.3**: Create `address_validation.py`
4. **Phase 2.1**: Extract harmonizer tools
5. **Phase 3.1**: Refactor harmonizer workflow
6. **Phase 4.1**: Move state models
7. **Phase 5.1**: Extract prompts (optional)

## Expected Results

### Before Refactoring
- `harmonizer_workflow.py`: 2,312 lines
- `receipt_metadata_finder_workflow.py`: 696 lines
- `cove_text_consistency_workflow.py`: 1,437 lines
- **Total**: ~4,445 lines
- **Duplication**: ~300 lines of retry logic, ~200 lines of LLM creation

### After Refactoring
- `harmonizer_workflow.py`: ~300 lines (87% reduction)
- `receipt_metadata_finder_workflow.py`: ~200 lines (71% reduction)
- `cove_text_consistency_workflow.py`: ~400 lines (72% reduction)
- `utils/agent_common.py`: ~200 lines (new, shared)
- `utils/receipt_fetching.py`: ~150 lines (new, shared)
- `utils/address_validation.py`: ~100 lines (new, shared)
- `tools/harmonizer_tools.py`: ~1,500 lines (extracted)
- **Total**: ~2,850 lines (36% reduction)
- **Duplication**: Eliminated

## Best Practices Applied

1. ✅ **DRY (Don't Repeat Yourself)**: Extract common patterns
2. ✅ **Separation of Concerns**: Tools separate from workflow
3. ✅ **Single Responsibility**: Each module has one clear purpose
4. ✅ **Reusability**: Shared utilities can be used across workflows
5. ✅ **Testability**: Tools can be tested independently
6. ✅ **Maintainability**: Changes in one place affect all workflows

## Migration Strategy

1. **Create utilities first** (Phase 1) - no breaking changes
2. **Update one workflow at a time** - test after each
3. **Keep old code commented** - easy rollback if needed
4. **Update tests** - ensure nothing breaks
5. **Remove old code** - after all workflows migrated

## Testing Strategy

1. **Unit tests** for utilities (retry logic, address validation)
2. **Integration tests** for workflows (ensure behavior unchanged)
3. **Regression tests** - compare outputs before/after refactoring
