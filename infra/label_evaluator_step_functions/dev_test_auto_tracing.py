#!/usr/bin/env python3
"""
Dev script to test LANGCHAIN_TRACING_V2=true with manual per-receipt traces.

Question:
    Can we get full auto-tracing (LLM inputs/outputs, structured outputs,
    token details — like QA traces) while ALSO maintaining per-receipt
    trace hierarchies via manual RunTree?

The concern:
    Previously, LANGCHAIN_TRACING_V2=true + manual RunTree caused "duplicate
    dotted_order" errors. But tracing_context(parent=run_tree) might solve
    this by giving LangChain a clear parent to attach auto-traced runs to.

Tests:
    1. TRACING_V2=true + manual RunTree root + tracing_context
       → Does the LLM call get FULL detail (inputs, outputs, tokens)?
       → Does it land under the correct manual parent?

    2. Structured output (with_structured_output) under manual trace
       → Do we get the Pydantic schema and parsed result in the trace?

    3. Two separate "receipt" roots with concurrent LLM calls
       → Do calls land under the CORRECT receipt root (no cross-talk)?

    4. Nested child_trace → LLM call with TRACING_V2=true
       → Does the existing child_trace code (which now uses tracing_context
         internally) work correctly when TRACING_V2=true too?
       → Or does auto-tracing create DUPLICATE runs?

Run with:
    python infra/label_evaluator_step_functions/dev_test_auto_tracing.py

Check results at: https://smith.langchain.com
    Project: dev-auto-trace-test
"""

import asyncio
import os
import sys
import time
import types

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lambdas", "utils"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lambdas"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
if not os.environ.get("LANGCHAIN_API_KEY"):
    print("ERROR: LANGCHAIN_API_KEY environment variable must be set")
    sys.exit(1)

if not os.environ.get("OPENROUTER_API_KEY"):
    print("ERROR: OPENROUTER_API_KEY environment variable must be set")
    sys.exit(1)

# KEY DIFFERENCE: Enable auto-tracing (like QA step function uses)
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_PROJECT"] = "dev-auto-trace-test"

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from langsmith import tracing_context
from langsmith.run_trees import RunTree, get_cached_client

from tracing import TraceContext, child_trace

# Bypass receipt_agent.__init__ (chromadb/pydantic v1 issue on Python 3.14)
for mod_name, mod_path in [
    ("receipt_agent", "receipt_agent/receipt_agent"),
    ("receipt_agent.utils", "receipt_agent/receipt_agent/utils"),
]:
    stub = types.ModuleType(mod_name)
    stub.__path__ = [mod_path]
    sys.modules[mod_name] = stub

from receipt_agent.utils.llm_factory import create_llm, create_production_invoker

TEST_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4.1-nano")

print("=" * 70)
print("DEV TEST: LANGCHAIN_TRACING_V2=true + Manual Per-Receipt Traces")
print("=" * 70)
print(f"  LANGCHAIN_TRACING_V2 = {os.environ.get('LANGCHAIN_TRACING_V2')}")
print(f"  LANGCHAIN_PROJECT    = {os.environ.get('LANGCHAIN_PROJECT')}")
print(f"  TEST_MODEL           = {TEST_MODEL}")


# ===================================================================
# TEST 1: Full LLM detail under manual RunTree
# ===================================================================
print("\n" + "=" * 70)
print("TEST 1: TRACING_V2=true + manual RunTree + tracing_context")
print("  Goal: LLM call has FULL detail (inputs, outputs, token counts)")
print("  Goal: LLM call is a child of the manual RunTree")
print("=" * 70)

root1 = RunTree(
    name="test_1_receipt_root",
    run_type="chain",
    inputs={"receipt": "test_receipt_1", "merchant": "Test Store"},
)
root1.post()
print(f"  Root trace_id: {root1.trace_id}")

try:
    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)

    with tracing_context(parent=root1):
        response = llm.invoke("What is the capital of France? Reply in one word.")

    content = response.content if hasattr(response, "content") else str(response)
    print(f"  LLM response: {content}")
    print("  Check LangSmith: should see full inputs/outputs on the ChatOpenAI run")

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback; traceback.print_exc()

root1.end()
root1.patch()
time.sleep(0.5)


# ===================================================================
# TEST 2: Structured output under manual RunTree
# ===================================================================
print("\n" + "=" * 70)
print("TEST 2: Structured output (with_structured_output) under manual trace")
print("  Goal: Pydantic schema + parsed result visible in LangSmith")
print("=" * 70)

root2 = RunTree(
    name="test_2_structured_output",
    run_type="chain",
    inputs={"receipt": "test_receipt_2", "test": "structured_output"},
)
root2.post()
print(f"  Root trace_id: {root2.trace_id}")

try:
    from pydantic import BaseModel, Field

    class MathAnswer(BaseModel):
        """A simple math answer."""
        answer: int = Field(description="The numeric answer")
        explanation: str = Field(description="Brief explanation")

    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)
    structured_llm = llm.with_structured_output(MathAnswer)

    with tracing_context(parent=root2):
        result = structured_llm.invoke("What is 7 * 8?")

    print(f"  Structured result: {result}")
    print(f"  Type: {type(result).__name__}")
    if hasattr(result, "answer"):
        print(f"  answer={result.answer}, explanation={result.explanation}")
    print("  Check LangSmith: should see schema + parsed Pydantic output")

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback; traceback.print_exc()

root2.end()
root2.patch()
time.sleep(0.5)


# ===================================================================
# TEST 3: Two concurrent "receipt" roots — no cross-talk
# ===================================================================
print("\n" + "=" * 70)
print("TEST 3: Two concurrent receipt roots — verify no cross-talk")
print("  Goal: Each LLM call lands under its own receipt trace")
print("=" * 70)

root3a = RunTree(
    name="test_3_receipt_A_costco",
    run_type="chain",
    inputs={"merchant": "Costco", "receipt_id": 1},
)
root3a.post()

root3b = RunTree(
    name="test_3_receipt_B_trader_joes",
    run_type="chain",
    inputs={"merchant": "Trader Joes", "receipt_id": 2},
)
root3b.post()

print(f"  Receipt A trace_id: {root3a.trace_id}")
print(f"  Receipt B trace_id: {root3b.trace_id}")

try:
    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)

    async def process_receipt_a():
        with tracing_context(parent=root3a):
            resp = await llm.ainvoke("Name a product sold at Costco. One word.")
        return resp.content if hasattr(resp, "content") else str(resp)

    async def process_receipt_b():
        with tracing_context(parent=root3b):
            resp = await llm.ainvoke("Name a product sold at Trader Joes. One word.")
        return resp.content if hasattr(resp, "content") else str(resp)

    async def run_concurrent():
        results = await asyncio.gather(process_receipt_a(), process_receipt_b())
        return results

    results = asyncio.run(run_concurrent())
    print(f"  Receipt A answer: {results[0]}")
    print(f"  Receipt B answer: {results[1]}")
    print("  Check LangSmith: each LLM call should be under its OWN receipt root")

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback; traceback.print_exc()

root3a.end()
root3a.patch()
root3b.end()
root3b.patch()
time.sleep(0.5)


# ===================================================================
# TEST 4: child_trace (which uses tracing_context internally) + TRACING_V2=true
#   Does the existing child_trace code produce DUPLICATES?
# ===================================================================
print("\n" + "=" * 70)
print("TEST 4: child_trace + TRACING_V2=true — check for duplicates")
print("  Goal: LLM call appears ONCE (not duplicated by auto-tracing)")
print("  Goal: child_trace span also appears correctly")
print("=" * 70)

root4 = RunTree(
    name="test_4_child_trace_with_auto",
    run_type="chain",
    inputs={"test": "child_trace_plus_auto_tracing"},
)
root4.post()
print(f"  Root trace_id: {root4.trace_id}")

parent_ctx4 = TraceContext(
    run_tree=root4,
    headers=root4.to_headers(),
    trace_id=str(root4.trace_id),
    root_run_id=str(root4.id),
)

try:
    invoker = create_production_invoker(
        model=TEST_MODEL, temperature=0.0, timeout=30, max_jitter_seconds=0
    )

    # child_trace now internally does tracing_context(parent=child) in the
    # yield block. With TRACING_V2=true, auto-tracing is ALSO active.
    # Will this cause duplicates?
    with child_trace(
        "currency_evaluation", parent_ctx4,
        metadata={"subagent": "currency"},
    ) as ctx:
        print(f"  child_trace id: {ctx.run_tree.id}")
        response = invoker.invoke(
            "Is $12.99 a valid price for milk? Reply yes or no."
        )
        content = response.content if hasattr(response, "content") else str(response)
        print(f"  LLM response: {content}")
        ctx.set_outputs({"response": content})

    stats = invoker.get_stats()
    print(f"  Invoker stats: calls={stats['call_count']}, cost=${stats['total_cost']:.6f}")
    print("  Check LangSmith: should see child_trace span with ONE LLM call child")
    print("  WARNING: If you see 2 LLM calls, auto-tracing is duplicating!")

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback; traceback.print_exc()

root4.end()
root4.patch()
time.sleep(0.5)


# ===================================================================
# TEST 5: Nested child_trace with structured output + TRACING_V2=true
#   Simulates the real pattern: receipt root → eval span → LLM review
# ===================================================================
print("\n" + "=" * 70)
print("TEST 5: Nested child_trace + structured output (full pipeline sim)")
print("  Goal: Receipt root → eval child → LLM review with structured output")
print("  Goal: All 3 levels visible, structured output details present")
print("=" * 70)

root5 = RunTree(
    name="test_5_full_pipeline",
    run_type="chain",
    inputs={"merchant": "Sprouts", "image_id": "abc123", "receipt_id": 1},
)
root5.post()
print(f"  Root trace_id: {root5.trace_id}")

parent_ctx5 = TraceContext(
    run_tree=root5,
    headers=root5.to_headers(),
    trace_id=str(root5.trace_id),
    root_run_id=str(root5.id),
)

try:
    from pydantic import BaseModel, Field

    class LabelReview(BaseModel):
        """Review of a receipt label."""
        decision: str = Field(description="KEEP or CHANGE")
        confidence: float = Field(description="0.0 to 1.0")
        reasoning: str = Field(description="Why this decision")

    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)
    structured_llm = llm.with_structured_output(LabelReview)

    # Level 1: evaluation child
    with child_trace(
        "evaluate_labels", parent_ctx5,
        metadata={"phase": "evaluation"},
    ) as eval_ctx:
        print(f"  eval child id: {eval_ctx.run_tree.id}")

        # Level 2: LLM review nested inside evaluation
        with child_trace(
            "llm_review", eval_ctx,
            metadata={"phase": "llm_review"},
        ) as review_ctx:
            print(f"  review child id: {review_ctx.run_tree.id}")
            result = structured_llm.invoke(
                "A word on a receipt says 'MILK' and is labeled LINE_TOTAL. "
                "Should we KEEP or CHANGE the label?"
            )
            print(f"  Review: decision={result.decision}, confidence={result.confidence}")
            print(f"  Reasoning: {result.reasoning}")
            review_ctx.set_outputs({"decision": result.decision})

        eval_ctx.set_outputs({"status": "reviewed"})

    print("  Check LangSmith: 3-level hierarchy with structured output details")

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback; traceback.print_exc()

root5.end()
root5.patch()


# ===================================================================
# Flush and summary
# ===================================================================
print("\n--- Flushing traces ---")
client = get_cached_client()
client.flush()
time.sleep(2)

print("\n" + "=" * 70)
print("ALL TESTS COMPLETE")
print("=" * 70)
print(f"\nProject: dev-auto-trace-test")
print(f"\nTrace IDs:")
print(f"  Test 1 (basic):        {root1.trace_id}")
print(f"  Test 2 (structured):   {root2.trace_id}")
print(f"  Test 3a (concurrent):  {root3a.trace_id}")
print(f"  Test 3b (concurrent):  {root3b.trace_id}")
print(f"  Test 4 (child_trace):  {root4.trace_id}")
print(f"  Test 5 (full pipeline):{root5.trace_id}")
print(f"\nKey things to verify in LangSmith:")
print(f"  1. LLM runs have FULL inputs/outputs (not just cost)")
print(f"  2. Structured output shows Pydantic schema + parsed result")
print(f"  3. Concurrent receipts don't cross-talk (check trace_ids)")
print(f"  4. child_trace + TRACING_V2=true doesn't create DUPLICATE LLM runs")
print(f"  5. Nested child_trace hierarchy is correct (3 levels)")
