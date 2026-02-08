#!/usr/bin/env python3
"""
Dev script to test LLM call visibility inside manual RunTree traces.

Problem:
    The label evaluator uses manual RunTree traces (LANGCHAIN_TRACING_V2=false)
    because auto-tracing conflicts with the manual hierarchy. This means LLM
    calls made by currency/metadata/financial subagents are INVISIBLE in
    LangSmith — costs, tokens, and structured outputs don't appear.

Root cause:
    1. child_trace doesn't set tracing_context, so LangChain can't find a parent.
    2. get_langchain_config() returns empty callbacks, and CostTrackingCallback
       calls get_current_run_tree() which returns None.

Solution:
    Use langsmith.tracing_context(parent=child_run_tree) to temporarily set the
    manual RunTree as the active parent for LLM calls. This makes LangChain
    auto-instrument the call as a child AND makes get_current_run_tree() work.

Run with:
    python infra/label_evaluator_step_functions/dev_test_llm_tracing.py

Check results at: https://smith.langchain.com
    Project: dev-llm-trace-test
"""

import asyncio
import os
import sys
import time

# ---------------------------------------------------------------------------
# Path setup (same as dev_test_tracing.py)
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lambdas", "utils"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lambdas"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# ---------------------------------------------------------------------------
# Environment — MUST be set before any langsmith / langchain imports
# ---------------------------------------------------------------------------
if not os.environ.get("LANGCHAIN_API_KEY"):
    print("ERROR: LANGCHAIN_API_KEY environment variable must be set")
    print("Run: export LANGCHAIN_API_KEY=your_api_key_here")
    sys.exit(1)

if not os.environ.get("OPENROUTER_API_KEY"):
    print("ERROR: OPENROUTER_API_KEY environment variable must be set")
    print("Run: export OPENROUTER_API_KEY=your_api_key_here")
    sys.exit(1)

# Disable auto-tracing — production now uses LANGCHAIN_TRACING_V2=true,
# but this test intentionally disables it to verify manual tracing behavior.
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGCHAIN_PROJECT"] = "dev-llm-trace-test"

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from langsmith import tracing_context
from langsmith.run_trees import RunTree, get_cached_client

from tracing import TraceContext, child_trace

# Import llm_factory directly, bypassing receipt_agent.__init__ which pulls in
# chromadb (broken on Python 3.14 with pydantic v1 errors).
import importlib
import types

for mod_name, mod_path in [
    ("receipt_agent", "receipt_agent/receipt_agent"),
    ("receipt_agent.utils", "receipt_agent/receipt_agent/utils"),
]:
    stub = types.ModuleType(mod_name)
    stub.__path__ = [mod_path]
    sys.modules[mod_name] = stub

from receipt_agent.utils.llm_factory import create_llm, create_production_invoker

# Use a cheap, fast model for testing
TEST_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4.1-nano")
TEST_PROMPT = "What is 2 + 2? Reply with just the number."

print("=" * 70)
print("DEV TEST: LLM Call Tracing Inside Manual RunTree Spans")
print("=" * 70)
print(f"  LANGCHAIN_TRACING_V2 = {os.environ.get('LANGCHAIN_TRACING_V2')}")
print(f"  LANGCHAIN_PROJECT    = {os.environ.get('LANGCHAIN_PROJECT')}")
print(f"  TEST_MODEL           = {TEST_MODEL}")

# ---------------------------------------------------------------------------
# Create root trace (simulates the per-receipt root created by the pipeline)
# ---------------------------------------------------------------------------
print("\n--- Creating root trace ---")
root = RunTree(
    name="dev_llm_trace_root",
    run_type="chain",
    inputs={"test": "llm_tracing_visibility"},
)
root.post()
print(f"  Root ID:    {root.id}")
print(f"  Trace ID:   {root.trace_id}")

parent_ctx = TraceContext(
    run_tree=root,
    headers=root.to_headers(),
    trace_id=str(root.trace_id),
    root_run_id=str(root.id),
)

# ---------------------------------------------------------------------------
# Test 1 — Baseline (broken): LLM call WITHOUT tracing_context
# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
print("TEST 1: Baseline — LLM call inside child_trace WITHOUT tracing_context")
print("  Expected: LLM call does NOT appear as child in LangSmith")
print("=" * 70)

try:
    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)

    with child_trace(
        "test_1_baseline", parent_ctx, metadata={"test": "baseline_broken"}
    ) as ctx:
        print(f"  child_trace run_tree id: {ctx.run_tree.id}")
        # Call LLM without wrapping in tracing_context
        response = llm.invoke(TEST_PROMPT)
        content = response.content if hasattr(response, "content") else str(response)
        print(f"  LLM response: {content}")
        ctx.set_outputs({"response": content, "note": "LLM call NOT linked"})

    print("  DONE — LLM call should be MISSING from this child in LangSmith")

except Exception as e:
    print(f"  ERROR: {e}")

# Small delay between tests
time.sleep(0.5)

# ---------------------------------------------------------------------------
# Test 2 — Fixed: LLM call WITH tracing_context(parent=run_tree)
# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
print("TEST 2: Fixed — LLM call inside tracing_context(parent=run_tree)")
print("  Expected: LLM call DOES appear as child in LangSmith")
print("=" * 70)

try:
    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)

    with child_trace(
        "test_2_with_tracing_context",
        parent_ctx,
        metadata={"test": "tracing_context_fix"},
    ) as ctx:
        print(f"  child_trace run_tree id: {ctx.run_tree.id}")
        # Wrap the LLM call in tracing_context with the manual RunTree as parent
        with tracing_context(parent=ctx.run_tree):
            response = llm.invoke(TEST_PROMPT)
        content = response.content if hasattr(response, "content") else str(response)
        print(f"  LLM response: {content}")
        ctx.set_outputs({"response": content, "note": "LLM call IS linked"})

    print("  DONE — LLM call should APPEAR as child of this span in LangSmith")

except Exception as e:
    print(f"  ERROR: {e}")

time.sleep(0.5)

# ---------------------------------------------------------------------------
# Test 3 — Async: ainvoke inside tracing_context (matches subagent pattern)
# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
print("TEST 3: Async — ainvoke inside tracing_context(parent=run_tree)")
print("  Expected: LLM call DOES appear as child in LangSmith")
print("=" * 70)

try:
    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)

    async def run_async_test():
        with child_trace(
            "test_3_async_tracing_context",
            parent_ctx,
            metadata={"test": "async_tracing_context"},
        ) as ctx:
            print(f"  child_trace run_tree id: {ctx.run_tree.id}")
            with tracing_context(parent=ctx.run_tree):
                response = await llm.ainvoke(TEST_PROMPT)
            content = (
                response.content if hasattr(response, "content") else str(response)
            )
            print(f"  LLM response: {content}")
            ctx.set_outputs({"response": content, "note": "async LLM call IS linked"})
        return content

    asyncio.run(run_async_test())
    print("  DONE — async LLM call should APPEAR as child in LangSmith")

except Exception as e:
    print(f"  ERROR: {e}")

time.sleep(0.5)

# ---------------------------------------------------------------------------
# Test 4 — LLMInvoker integration: create_production_invoker + tracing_context
# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
print("TEST 4: LLMInvoker — create_production_invoker inside tracing_context")
print("  Expected: LLM call appears as child + invoker.get_stats() has costs")
print("=" * 70)

try:
    invoker = create_production_invoker(
        model=TEST_MODEL, temperature=0.0, timeout=30, max_jitter_seconds=0
    )
    print(f"  Stats before: {invoker.get_stats()}")

    with child_trace(
        "test_4_llm_invoker",
        parent_ctx,
        metadata={"test": "llm_invoker_integration"},
    ) as ctx:
        print(f"  child_trace run_tree id: {ctx.run_tree.id}")
        with tracing_context(parent=ctx.run_tree):
            response = invoker.invoke(TEST_PROMPT)
        content = response.content if hasattr(response, "content") else str(response)
        print(f"  LLM response: {content}")
        ctx.set_outputs({"response": content, "note": "invoker LLM call IS linked"})

    stats = invoker.get_stats()
    print(f"  Stats after:  {stats}")
    print(f"  Cost tracked: ${stats.get('total_cost', 0):.6f}")
    print(f"  Tokens used:  {stats.get('total_tokens', 0)}")
    print("  DONE — LLM call should APPEAR as child with cost metadata")

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback

    traceback.print_exc()

# ---------------------------------------------------------------------------
# End root trace and flush
# ---------------------------------------------------------------------------
print("\n--- Ending root trace and flushing ---")
root.end()
root.patch()

client = get_cached_client()
client.flush()
time.sleep(1)  # Give the flush a moment

print("\n" + "=" * 70)
print("ALL TESTS COMPLETE")
print("=" * 70)
print("\nCheck traces at: https://smith.langchain.com")
print("Project: dev-llm-trace-test")
print(f"Trace ID: {root.trace_id}")
print("\nExpected hierarchy:")
print("  dev_llm_trace_root")
print("    ├── test_1_baseline (child_trace)")
print("    │     └── (LLM call — MISSING, expected)")
print("    ├── test_2_with_tracing_context (child_trace)")
print("    │     └── ChatOpenAI (auto-traced LLM call — PRESENT)")
print("    ├── test_3_async_tracing_context (child_trace)")
print("    │     └── ChatOpenAI (auto-traced LLM call — PRESENT)")
print("    └── test_4_llm_invoker (child_trace)")
print("          └── ChatOpenAI (auto-traced LLM call — PRESENT, with costs)")
