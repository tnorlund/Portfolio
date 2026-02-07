#!/usr/bin/env python3
"""
Dev script v3: Fix the dotted_order issue found in v2.

Root cause from v2:
    When RunTree is created with DIFFERENT trace_id and run_id (both UUID5),
    the dotted_order starts with run_id, but LangSmith validates that
    dotted_order starts with trace_id. This causes:
        "trace_id X does not match first part of dotted_order Y"

    This happens because production uses:
        trace_id  = uuid5("exec-img-rcpt")   → deterministic
        run_id    = uuid5("root-exec-img-rcpt")  → different deterministic

    When RunTree auto-generates IDs, trace_id == run_id for roots, so it works.

Solution options:
    A. Set trace_id == run_id (use same UUID for both)
    B. Don't set trace_id at all, let RunTree derive it from run_id
    C. Set run_id = trace_id (use trace_id for both)

This script tests all three approaches.

Run with:
    python infra/label_evaluator_step_functions/dev_test_auto_tracing_v3.py
"""

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
PROJECT_NAME = "dev-auto-trace-v3"
os.environ["LANGCHAIN_PROJECT"] = PROJECT_NAME

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from langsmith import tracing_context
from langsmith.run_trees import RunTree, get_cached_client

from tracing import TraceContext, child_trace

for mod_name, mod_path in [
    ("receipt_agent", "receipt_agent/receipt_agent"),
    ("receipt_agent.utils", "receipt_agent/receipt_agent/utils"),
]:
    stub = types.ModuleType(mod_name)
    stub.__path__ = [mod_path]
    sys.modules[mod_name] = stub

from pydantic import BaseModel, Field
from receipt_agent.utils.llm_factory import create_llm

TEST_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4.1-nano")


def det_uuid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))


class LabelReview(BaseModel):
    decision: str = Field(description="KEEP or CHANGE")
    confidence: float = Field(description="0.0 to 1.0")
    reasoning: str = Field(description="Why this decision")


print("=" * 70)
print("DEV TEST v3: Fix dotted_order — trace_id must match root run_id")
print("=" * 70)
print(f"  LANGCHAIN_TRACING_V2 = {os.environ.get('LANGCHAIN_TRACING_V2')}")
print(f"  LANGCHAIN_PROJECT    = {PROJECT_NAME}")


# ===================================================================
# TEST A: trace_id == run_id (use same UUID for both)
# ===================================================================
print("\n" + "=" * 70)
print("TEST A: trace_id == run_id (same deterministic UUID for both)")
print("  This means we lose the separate root_run_id concept")
print("=" * 70)

shared_id_a = det_uuid("testA-receipt-abc-1")

root_a = RunTree(
    id=shared_id_a,
    trace_id=shared_id_a,  # SAME as run_id
    name="ReceiptEvaluation",
    run_type="chain",
    inputs={"merchant": "Test Store A", "test": "A"},
    session_name=PROJECT_NAME,
)
root_a.post()
print(f"  trace_id == run_id: {root_a.trace_id}")
print(f"  dotted_order: {getattr(root_a, 'dotted_order', 'N/A')}")

try:
    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)

    parent_ctx_a = TraceContext(
        run_tree=root_a,
        headers=root_a.to_headers(),
        trace_id=str(root_a.trace_id),
        root_run_id=str(root_a.id),
    )

    with child_trace(
        "phase2_financial", parent_ctx_a,
        metadata={"phase": "financial"},
    ) as ctx:
        resp = llm.invoke("Is $5 a valid price? Yes or no.")
        print(f"  LLM response: {resp.content}")
        ctx.set_outputs({"response": resp.content})

    with child_trace(
        "phase3_llm_review", parent_ctx_a,
        metadata={"phase": "review"},
    ) as ctx:
        structured = llm.with_structured_output(LabelReview)
        result = structured.invoke("Word='MILK' label=LINE_TOTAL. KEEP or CHANGE?")
        print(f"  Review: {result.decision} ({result.confidence})")
        ctx.set_outputs({"decision": result.decision})

    with child_trace("upload_results", parent_ctx_a) as ctx:
        ctx.set_outputs({"status": "ok"})

    print("  DONE - check LangSmith")

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback; traceback.print_exc()

root_a.end()
root_a.patch()
time.sleep(0.5)


# ===================================================================
# TEST B: Don't set trace_id — let RunTree derive from run_id
# ===================================================================
print("\n" + "=" * 70)
print("TEST B: Only set run_id, let RunTree auto-derive trace_id")
print("=" * 70)

run_id_b = det_uuid("testB-receipt-def-1")

root_b = RunTree(
    id=run_id_b,
    # trace_id NOT set — RunTree should auto-set trace_id = run_id for roots
    name="ReceiptEvaluation",
    run_type="chain",
    inputs={"merchant": "Test Store B", "test": "B"},
    session_name=PROJECT_NAME,
)
root_b.post()
print(f"  run_id:      {root_b.id}")
print(f"  trace_id:    {root_b.trace_id}")
print(f"  match:       {str(root_b.id) == str(root_b.trace_id)}")

try:
    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)

    parent_ctx_b = TraceContext(
        run_tree=root_b,
        headers=root_b.to_headers(),
        trace_id=str(root_b.trace_id),
        root_run_id=str(root_b.id),
    )

    with child_trace(
        "phase2_financial", parent_ctx_b,
        metadata={"phase": "financial"},
    ) as ctx:
        resp = llm.invoke("Is $10 a valid price? Yes or no.")
        print(f"  LLM response: {resp.content}")
        ctx.set_outputs({"response": resp.content})

    with child_trace(
        "phase3_llm_review", parent_ctx_b,
        metadata={"phase": "review"},
    ) as ctx:
        structured = llm.with_structured_output(LabelReview)
        result = structured.invoke("Word='EGGS' label=PRODUCT_NAME. KEEP or CHANGE?")
        print(f"  Review: {result.decision} ({result.confidence})")
        ctx.set_outputs({"decision": result.decision})

    with child_trace("upload_results", parent_ctx_b) as ctx:
        ctx.set_outputs({"status": "ok"})

    print("  DONE - check LangSmith")

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback; traceback.print_exc()

root_b.end()
root_b.patch()
time.sleep(0.5)


# ===================================================================
# TEST C: Set run_id = trace_id (keep trace_id, derive run_id from it)
# ===================================================================
print("\n" + "=" * 70)
print("TEST C: Set run_id = trace_id (keep the trace_id, match run_id to it)")
print("=" * 70)

trace_id_c = det_uuid("testC-receipt-ghi-1")

root_c = RunTree(
    id=trace_id_c,      # run_id = trace_id
    trace_id=trace_id_c,
    name="ReceiptEvaluation",
    run_type="chain",
    inputs={"merchant": "Test Store C", "test": "C"},
    session_name=PROJECT_NAME,
)
root_c.post()
print(f"  trace_id == run_id: {root_c.trace_id}")

try:
    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)

    parent_ctx_c = TraceContext(
        run_tree=root_c,
        headers=root_c.to_headers(),
        trace_id=str(root_c.trace_id),
        root_run_id=str(root_c.id),
    )

    with child_trace(
        "phase2_financial", parent_ctx_c,
        metadata={"phase": "financial"},
    ) as ctx:
        resp = llm.invoke("Is $7 a valid price? Yes or no.")
        print(f"  LLM response: {resp.content}")
        ctx.set_outputs({"response": resp.content})

    with child_trace(
        "phase3_llm_review", parent_ctx_c,
        metadata={"phase": "review"},
    ) as ctx:
        structured = llm.with_structured_output(LabelReview)
        result = structured.invoke("Word='BREAD' label=MERCHANT_NAME. KEEP or CHANGE?")
        print(f"  Review: {result.decision} ({result.confidence})")
        ctx.set_outputs({"decision": result.decision})

    with child_trace("upload_results", parent_ctx_c) as ctx:
        ctx.set_outputs({"status": "ok"})

    print("  DONE - check LangSmith")

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback; traceback.print_exc()

root_c.end()
root_c.patch()
time.sleep(0.5)


# ===================================================================
# TEST D: Full production sim with trace_id==run_id + root wrapped
# ===================================================================
print("\n" + "=" * 70)
print("TEST D: Full production sim — trace_id==run_id + root in tracing_context")
print("  Best of both worlds: deterministic IDs + auto-tracing + correct hierarchy")
print("=" * 70)

shared_id_d = det_uuid("testD-receipt-jkl-1")

root_d = RunTree(
    id=shared_id_d,
    trace_id=shared_id_d,
    name="ReceiptEvaluation",
    run_type="chain",
    inputs={
        "merchant_name": "Sprouts",
        "word_count": 150,
        "label_count": 75,
    },
    extra={
        "metadata": {
            "image_id": "jkl456",
            "receipt_id": 1,
            "merchant_name": "Sprouts",
        }
    },
    tags=["unified-evaluation", "llm", "per-receipt"],
    session_name=PROJECT_NAME,
)
root_d.post()
print(f"  trace_id: {root_d.trace_id}")

try:
    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)
    structured_llm = llm.with_structured_output(LabelReview)

    # Wrap everything in tracing_context(parent=root) so auto-tracing
    # knows this is the active root
    with tracing_context(parent=root_d):
        parent_ctx_d = TraceContext(
            run_tree=root_d,
            headers=root_d.to_headers(),
            trace_id=str(root_d.trace_id),
            root_run_id=str(root_d.id),
        )

        # Phase 1: corrections (no LLM)
        with child_trace(
            "apply_phase1_corrections", parent_ctx_d,
            metadata={"phase": "corrections"},
        ) as ctx:
            ctx.set_outputs({"corrections_applied": 5})

        # Phase 2: financial validation (LLM)
        with child_trace(
            "phase2_financial_validation", parent_ctx_d,
            metadata={"phase": "financial"},
        ) as ctx:
            resp = llm.invoke("Is $12.99 a valid price for milk? Yes or no.")
            print(f"  Financial: {resp.content}")
            ctx.set_outputs({"response": resp.content})

        # Phase 3: LLM review (structured output)
        with child_trace(
            "phase3_llm_review", parent_ctx_d,
            metadata={"phase": "llm_review"},
        ) as ctx:
            result = structured_llm.invoke(
                "Word='MILK' labeled LINE_TOTAL. KEEP or CHANGE?"
            )
            print(f"  Review: {result.decision} ({result.confidence})")
            ctx.set_outputs({"decision": result.decision})

        # Phase 4: upload (no LLM)
        with child_trace(
            "upload_results", parent_ctx_d,
            metadata={"phase": "upload"},
        ) as ctx:
            ctx.set_outputs({"status": "uploaded"})

    print("  CHECK: Root + 4 children + LLM detail — the ideal trace")

except Exception as e:
    print(f"  ERROR: {e}")
    import traceback; traceback.print_exc()

root_d.end()
root_d.patch()


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
print(f"  Test A (trace_id == run_id):  {root_a.trace_id}")
print(f"  Test B (auto trace_id):       {root_b.trace_id}")
print(f"  Test C (run_id = trace_id):   {root_c.trace_id}")
print(f"  Test D (full sim + wrap):     {root_d.trace_id}")
print(f"\nAll tests use deterministic UUID5 IDs (matching production)")
print(f"Key question: Does trace_id==run_id fix the dotted_order error?")
