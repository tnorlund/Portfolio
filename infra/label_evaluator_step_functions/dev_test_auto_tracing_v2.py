#!/usr/bin/env python3
"""
Dev script v2: Diagnose why LANGCHAIN_TRACING_V2=true + manual RunTree roots
work locally but break in Lambda (missing roots, duplicate LLM traces).

Hypotheses to test:
  1. Deterministic UUID5 trace_id/run_id (used by production) vs auto-generated
     → Does RunTree.post() behave differently with pre-set UUIDs?
  2. Root RunTree NOT wrapped in tracing_context
     → Production creates the root, then wraps children in tracing_context.
       Does this cause auto-tracing to NOT see the root and create its own?
  3. child_trace's tracing_context(parent=child) with TRACING_V2=true
     → Does auto-tracing create a SECOND trace for LLM calls?
  4. Explicit project_name on RunTree
     → Does passing project_name/session_name help?

Run with:
    python infra/label_evaluator_step_functions/dev_test_auto_tracing_v2.py

Check results at: https://smith.langchain.com
    Project: dev-auto-trace-v2
"""

import asyncio
import os
import sys
import time
import types
import uuid

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

os.environ["LANGCHAIN_TRACING_V2"] = "true"
PROJECT_NAME = "dev-auto-trace-v2"
os.environ["LANGCHAIN_PROJECT"] = PROJECT_NAME

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from langsmith import tracing_context
from langsmith.run_trees import RunTree, get_cached_client

from tracing import TraceContext, child_trace

# Bypass receipt_agent.__init__
for mod_name, mod_path in [
    ("receipt_agent", "receipt_agent/receipt_agent"),
    ("receipt_agent.utils", "receipt_agent/receipt_agent/utils"),
]:
    stub = types.ModuleType(mod_name)
    stub.__path__ = [mod_path]
    sys.modules[mod_name] = stub

from receipt_agent.utils.llm_factory import create_llm, create_production_invoker

TEST_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4.1-nano")


def deterministic_uuid(seed: str) -> str:
    """Generate a deterministic UUID5, matching production's generate_receipt_trace_id."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))


print("=" * 70)
print("DEV TEST v2: Diagnosing TRACING_V2=true + Manual RunTree in Lambda")
print("=" * 70)
print(f"  LANGCHAIN_TRACING_V2 = {os.environ.get('LANGCHAIN_TRACING_V2')}")
print(f"  LANGCHAIN_PROJECT    = {PROJECT_NAME}")
print(f"  TEST_MODEL           = {TEST_MODEL}")


# ===================================================================
# TEST 1: Deterministic UUIDs (matches production create_receipt_trace)
# ===================================================================
print("\n" + "=" * 70)
print("TEST 1: Deterministic UUID5 trace_id + run_id (production pattern)")
print("  Question: Does RunTree.post() work with pre-set UUID5 IDs?")
print("  Production uses generate_receipt_trace_id() which returns UUID5")
print("=" * 70)

trace_id_1 = deterministic_uuid("test1-receipt-abc123-1")
root_run_id_1 = deterministic_uuid("test1-root-abc123-1")

root1 = RunTree(
    id=root_run_id_1,
    trace_id=trace_id_1,
    name="test_1_deterministic_ids",
    run_type="chain",
    inputs={"receipt": "abc123", "merchant": "Test Store"},
    extra={"metadata": {"image_id": "abc123", "receipt_id": 1}},
    tags=["test", "deterministic-ids"],
)
root1.post()
print(f"  trace_id:    {root1.trace_id}")
print(f"  run_id:      {root1.id}")
print(f"  session_name:{root1.session_name}")

try:
    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)

    with tracing_context(parent=root1):
        response = llm.invoke("What is the capital of France? One word.")

    content = response.content if hasattr(response, "content") else str(response)
    print(f"  LLM response: {content}")
    print("  CHECK: Does root appear? Is LLM a child of it?")

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback; traceback.print_exc()

root1.end()
root1.patch()
time.sleep(0.5)


# ===================================================================
# TEST 2: Root NOT in tracing_context, children ARE (production pattern)
# ===================================================================
print("\n" + "=" * 70)
print("TEST 2: Root outside tracing_context, child_trace inside (production)")
print("  Question: Does auto-tracing create separate root for LLM calls")
print("  when the root RunTree is NOT the active tracing_context parent?")
print("=" * 70)

trace_id_2 = deterministic_uuid("test2-receipt-def456-1")
root_run_id_2 = deterministic_uuid("test2-root-def456-1")

root2 = RunTree(
    id=root_run_id_2,
    trace_id=trace_id_2,
    name="test_2_root_outside_context",
    run_type="chain",
    inputs={"receipt": "def456", "merchant": "Sprouts"},
)
root2.post()
print(f"  trace_id: {root2.trace_id}")

# Create TraceContext (matching unified_receipt_evaluator.py line 464)
parent_ctx2 = TraceContext(
    run_tree=root2,
    headers=root2.to_headers(),
    trace_id=str(root2.trace_id),
    root_run_id=str(root2.id),
)

try:
    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)

    # child_trace wraps yield in tracing_context(parent=child)
    with child_trace(
        "phase2_financial_validation", parent_ctx2,
        metadata={"phase": "financial"},
    ) as ctx:
        print(f"  child id: {ctx.run_tree.id}")
        response = llm.invoke("Is $5.99 a valid price for bread? Yes or no.")
        content = response.content if hasattr(response, "content") else str(response)
        print(f"  LLM response: {content}")
        ctx.set_outputs({"response": content})

    # Second child (no LLM)
    with child_trace(
        "upload_results", parent_ctx2,
        metadata={"phase": "upload"},
    ) as ctx:
        ctx.set_outputs({"status": "uploaded"})

    print("  CHECK: Root visible? Both children visible? LLM under financial child?")

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback; traceback.print_exc()

root2.end()
root2.patch()
time.sleep(0.5)


# ===================================================================
# TEST 3: Root wrapped in tracing_context for its entire lifetime
# ===================================================================
print("\n" + "=" * 70)
print("TEST 3: Root wrapped in tracing_context for entire lifetime")
print("  Question: If we wrap the root in tracing_context before creating")
print("  children, does auto-tracing properly nest everything?")
print("=" * 70)

trace_id_3 = deterministic_uuid("test3-receipt-ghi789-1")
root_run_id_3 = deterministic_uuid("test3-root-ghi789-1")

root3 = RunTree(
    id=root_run_id_3,
    trace_id=trace_id_3,
    name="test_3_root_wrapped",
    run_type="chain",
    inputs={"receipt": "ghi789", "merchant": "Costco"},
)
root3.post()
print(f"  trace_id: {root3.trace_id}")

try:
    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)

    # Wrap the entire receipt processing in tracing_context(parent=root)
    with tracing_context(parent=root3):
        parent_ctx3 = TraceContext(
            run_tree=root3,
            headers=root3.to_headers(),
            trace_id=str(root3.trace_id),
            root_run_id=str(root3.id),
        )

        with child_trace(
            "phase2_financial_validation", parent_ctx3,
            metadata={"phase": "financial"},
        ) as ctx:
            print(f"  child id: {ctx.run_tree.id}")
            response = llm.invoke("Is $3.50 a valid price for milk? Yes or no.")
            content = response.content if hasattr(response, "content") else str(response)
            print(f"  LLM response: {content}")
            ctx.set_outputs({"response": content})

        with child_trace(
            "upload_results", parent_ctx3,
            metadata={"phase": "upload"},
        ) as ctx:
            ctx.set_outputs({"status": "uploaded"})

    print("  CHECK: Root visible? Children nested correctly? No duplicate LLM traces?")

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback; traceback.print_exc()

root3.end()
root3.patch()
time.sleep(0.5)


# ===================================================================
# TEST 4: Explicit project_name/session_name on RunTree
# ===================================================================
print("\n" + "=" * 70)
print("TEST 4: Explicit session_name on RunTree (force project)")
print("  Question: Does explicitly setting session_name on RunTree")
print("  ensure the root lands in the correct project?")
print("=" * 70)

trace_id_4 = deterministic_uuid("test4-receipt-jkl012-1")
root_run_id_4 = deterministic_uuid("test4-root-jkl012-1")

root4 = RunTree(
    id=root_run_id_4,
    trace_id=trace_id_4,
    name="test_4_explicit_project",
    run_type="chain",
    inputs={"receipt": "jkl012", "merchant": "Trader Joes"},
    session_name=PROJECT_NAME,  # Explicitly set project
)
root4.post()
print(f"  trace_id:     {root4.trace_id}")
print(f"  session_name: {root4.session_name}")

try:
    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)

    parent_ctx4 = TraceContext(
        run_tree=root4,
        headers=root4.to_headers(),
        trace_id=str(root4.trace_id),
        root_run_id=str(root4.id),
    )

    with child_trace(
        "phase2_financial_validation", parent_ctx4,
        metadata={"phase": "financial"},
    ) as ctx:
        response = llm.invoke("Is $2.99 a valid price for eggs? Yes or no.")
        content = response.content if hasattr(response, "content") else str(response)
        print(f"  LLM response: {content}")
        ctx.set_outputs({"response": content})

    print("  CHECK: Same as Test 2 but with explicit session_name")

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback; traceback.print_exc()

root4.end()
root4.patch()
time.sleep(0.5)


# ===================================================================
# TEST 5: Multiple children with concurrent LLM calls (production sim)
# ===================================================================
print("\n" + "=" * 70)
print("TEST 5: Full production simulation — root + 4 children + LLM calls")
print("  Simulates: create_receipt_trace → child_trace phases → LLM review")
print("=" * 70)

trace_id_5 = deterministic_uuid("test5-receipt-mno345-1")
root_run_id_5 = deterministic_uuid("test5-root-mno345-1")

root5 = RunTree(
    id=root_run_id_5,
    trace_id=trace_id_5,
    name="ReceiptEvaluation",
    run_type="chain",
    inputs={
        "merchant_name": "Sprouts",
        "word_count": 150,
        "label_count": 75,
    },
    extra={
        "metadata": {
            "image_id": "mno345",
            "receipt_id": 1,
            "merchant_name": "Sprouts",
        }
    },
    tags=["unified-evaluation", "llm", "per-receipt"],
    session_name=PROJECT_NAME,
)
root5.post()
print(f"  trace_id: {root5.trace_id}")

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

    # Phase 1: apply corrections (no LLM)
    with child_trace(
        "apply_phase1_corrections", parent_ctx5,
        metadata={"phase": "corrections"},
    ) as ctx:
        ctx.set_outputs({"corrections_applied": 5})

    # Phase 2: financial validation (with LLM)
    with child_trace(
        "phase2_financial_validation", parent_ctx5,
        metadata={"phase": "financial"},
    ) as ctx:
        response = llm.invoke("Is $12.99 a valid price for milk? Yes or no.")
        content = response.content if hasattr(response, "content") else str(response)
        print(f"  Financial LLM: {content}")
        ctx.set_outputs({"response": content})

    # Phase 3: LLM review with structured output
    with child_trace(
        "phase3_llm_review", parent_ctx5,
        metadata={"phase": "llm_review"},
    ) as ctx:
        result = structured_llm.invoke(
            "A word on a receipt says 'MILK' and is labeled LINE_TOTAL. "
            "Should we KEEP or CHANGE the label?"
        )
        print(f"  Review: decision={result.decision}, confidence={result.confidence}")
        ctx.set_outputs({"decision": result.decision})

    # Phase 4: upload results (no LLM)
    with child_trace(
        "upload_results", parent_ctx5,
        metadata={"phase": "upload"},
    ) as ctx:
        ctx.set_outputs({"status": "uploaded"})

    print("  CHECK: Root 'ReceiptEvaluation' with 4 children,")
    print("  LLM calls nested under financial + llm_review children")

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback; traceback.print_exc()

root5.end()
root5.patch()


# ===================================================================
# TEST 6: Same as Test 5 but with root in tracing_context
# ===================================================================
print("\n" + "=" * 70)
print("TEST 6: Full sim WITH root wrapped in tracing_context")
print("  Compare with Test 5 to see if wrapping root helps")
print("=" * 70)

trace_id_6 = deterministic_uuid("test6-receipt-pqr678-1")
root_run_id_6 = deterministic_uuid("test6-root-pqr678-1")

root6 = RunTree(
    id=root_run_id_6,
    trace_id=trace_id_6,
    name="ReceiptEvaluation",
    run_type="chain",
    inputs={
        "merchant_name": "Costco",
        "word_count": 200,
        "label_count": 100,
    },
    extra={
        "metadata": {
            "image_id": "pqr678",
            "receipt_id": 1,
            "merchant_name": "Costco",
        }
    },
    tags=["unified-evaluation", "llm", "per-receipt"],
    session_name=PROJECT_NAME,
)
root6.post()
print(f"  trace_id: {root6.trace_id}")

try:
    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)
    structured_llm = llm.with_structured_output(LabelReview)

    # KEY DIFFERENCE: Wrap everything in tracing_context(parent=root6)
    with tracing_context(parent=root6):
        parent_ctx6 = TraceContext(
            run_tree=root6,
            headers=root6.to_headers(),
            trace_id=str(root6.trace_id),
            root_run_id=str(root6.id),
        )

        with child_trace(
            "apply_phase1_corrections", parent_ctx6,
            metadata={"phase": "corrections"},
        ) as ctx:
            ctx.set_outputs({"corrections_applied": 3})

        with child_trace(
            "phase2_financial_validation", parent_ctx6,
            metadata={"phase": "financial"},
        ) as ctx:
            response = llm.invoke("Is $8.99 a valid price for cheese? Yes or no.")
            content = response.content if hasattr(response, "content") else str(response)
            print(f"  Financial LLM: {content}")
            ctx.set_outputs({"response": content})

        with child_trace(
            "phase3_llm_review", parent_ctx6,
            metadata={"phase": "llm_review"},
        ) as ctx:
            result = structured_llm.invoke(
                "A word on a receipt says 'CHEESE' and is labeled PRODUCT_NAME. "
                "Should we KEEP or CHANGE the label?"
            )
            print(f"  Review: decision={result.decision}, confidence={result.confidence}")
            ctx.set_outputs({"decision": result.decision})

        with child_trace(
            "upload_results", parent_ctx6,
            metadata={"phase": "upload"},
        ) as ctx:
            ctx.set_outputs({"status": "uploaded"})

    print("  CHECK: Compare with Test 5 — does wrapping root eliminate duplicates?")

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback; traceback.print_exc()

root6.end()
root6.patch()


# ===================================================================
# Flush and summary
# ===================================================================
print("\n--- Flushing traces ---")
client = get_cached_client()
client.flush()
time.sleep(3)

print("\n" + "=" * 70)
print("ALL TESTS COMPLETE")
print("=" * 70)
print(f"\nProject: {PROJECT_NAME}")
print(f"\nTrace IDs:")
print(f"  Test 1 (deterministic IDs):     {root1.trace_id}")
print(f"  Test 2 (root outside context):  {root2.trace_id}")
print(f"  Test 3 (root wrapped):          {root3.trace_id}")
print(f"  Test 4 (explicit project):      {root4.trace_id}")
print(f"  Test 5 (full sim, no wrap):     {root5.trace_id}")
print(f"  Test 6 (full sim, root wrap):   {root6.trace_id}")
print(f"\nKey comparisons:")
print(f"  Test 2 vs 3: Does wrapping root in tracing_context help?")
print(f"  Test 2 vs 4: Does explicit session_name help?")
print(f"  Test 5 vs 6: Full production sim — with vs without root wrap")
