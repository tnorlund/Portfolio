#!/usr/bin/env python3
"""
Dev script to test LangSmith trace propagation locally.

Run with:
    python dev_test_tracing.py

Check results at: https://smith.langchain.com/o/your-org/projects/p/dev-trace-test
"""

import os
import sys

# Add paths for local imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lambdas", "utils"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lambdas"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# Set LangSmith env vars (API key must be set in environment)
if not os.environ.get("LANGCHAIN_API_KEY"):
    print("ERROR: LANGCHAIN_API_KEY environment variable must be set")
    print("Run: export LANGCHAIN_API_KEY=your_api_key_here")
    sys.exit(1)
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_PROJECT"] = "dev-trace-test"

from langsmith import tracing_context
from langsmith.run_trees import RunTree, get_cached_client

# Import our tracing utilities
from tracing import child_trace, state_trace, TraceContext

print("=" * 60)
print("DEV TRACE TEST - Using headers consistently")
print("=" * 60)

# Create root trace (simulates state_trace in Lambda)
print("\n1. Creating root trace...")
root = RunTree(
    name="test_root_trace",
    run_type="chain",
    inputs={"test": "input"},
)
root.post()
root_headers = root.to_headers()  # Use headers for propagation
print(f"   Root ID: {root.id}")
print(f"   Trace ID: {root.trace_id}")
print(f"   Headers: {list(root_headers.keys())}")

# Create parent context using headers (simulates what state_trace yields)
parent_ctx = TraceContext(
    run_tree=root,
    headers=root_headers,
    trace_id=str(root.trace_id),
    root_run_id=str(root.id),
)

# Test 1: child_trace with LangGraph
print("\n2. Testing child_trace with LangGraph...")
try:
    from langgraph.graph import StateGraph
    from typing_extensions import TypedDict

    class TestState(TypedDict):
        value: int

    def add_one(state: TestState) -> TestState:
        print(f"      [add_one node] value: {state['value']} -> {state['value'] + 1}")
        return {"value": state["value"] + 1}

    def multiply_two(state: TestState) -> TestState:
        print(f"      [multiply_two node] value: {state['value']} -> {state['value'] * 2}")
        return {"value": state["value"] * 2}

    workflow = StateGraph(TestState)
    workflow.add_node("add", add_one)
    workflow.add_node("multiply", multiply_two)
    workflow.add_edge("add", "multiply")
    workflow.set_entry_point("add")
    workflow.set_finish_point("multiply")
    graph = workflow.compile()

    with child_trace("test_langgraph_child", parent_ctx, metadata={"test": "langgraph"}) as ctx:
        print(f"   Child trace created, run_tree: {ctx.run_tree is not None}")
        result = graph.invoke({"value": 5})
        print(f"   Graph result: {result}")

    print("   SUCCESS: LangGraph ran inside child_trace")

except Exception as e:
    print(f"   ERROR: {e}")
    import traceback
    traceback.print_exc()

# Test 2: Nested child traces
print("\n3. Testing nested child traces...")
try:
    with child_trace("outer_child", parent_ctx) as outer_ctx:
        print("   Outer child created")
        with child_trace("inner_child", outer_ctx) as inner_ctx:
            print("   Inner child created")
            # Run graph in innermost child
            result = graph.invoke({"value": 10})
            print(f"   Inner graph result: {result}")
        print("   Back to outer")
    print("   SUCCESS: Nested child traces work")
except Exception as e:
    print(f"   ERROR: {e}")

# Test 3: Simulate LLM call (without actual LLM)
print("\n4. Testing simulated LLM child trace...")
try:
    with child_trace("llm_call_simulation", parent_ctx, run_type="llm") as llm_ctx:
        print("   LLM trace created")
        # In real code, this would be: llm.invoke(messages)
        # The tracing_context should make LangChain calls children of this
        print("   (Simulated LLM call)")
    print("   SUCCESS: LLM child trace works")
except Exception as e:
    print(f"   ERROR: {e}")

# End root trace
root.end()
root.patch()

# Flush traces
print("\n5. Flushing traces to LangSmith...")
client = get_cached_client()
client.flush()

print("\n" + "=" * 60)
print("TEST COMPLETE")
print("=" * 60)
print("\nCheck traces at: https://smith.langchain.com")
print("Project: dev-trace-test")
print(f"Look for trace: {root.trace_id}")
print("\nExpected hierarchy:")
print("  test_root_trace")
print("    ├── test_langgraph_child")
print("    │     └── LangGraph (auto-traced)")
print("    │           ├── add")
print("    │           └── multiply")
print("    ├── outer_child")
print("    │     └── inner_child")
print("    │           └── LangGraph (auto-traced)")
print("    └── llm_call_simulation")
