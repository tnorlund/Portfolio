#!/usr/bin/env python3
"""
Dev script: Diagnose and fix orphaned LLM traces from start_child_trace().

Problem:
    Production uses start_child_trace() (not the context manager) for Phase 1
    currency + metadata evaluations, which run concurrently via asyncio.gather().
    Unlike child_trace() (the context manager), start_child_trace() does NOT set
    tracing_context(parent=child), so with LANGCHAIN_TRACING_V2=true the LLM
    calls create standalone root traces instead of nesting under the child span.

    Phase 2 (financial) and Phase 3 (review) use child_trace() and are fine.

Fix approach:
    Each async coroutine should wrap its LLM calls in
    tracing_context(parent=trace_ctx.run_tree) using the TraceContext it receives.
    This can be done either in the caller (unified_receipt_evaluator) or in the
    subagent functions themselves.

Tests:
    1. Broken baseline:   start_child_trace + asyncio.gather, NO tracing_context
    2. Fixed (caller):    wrap each coroutine in tracing_context before gather
    3. Fixed (callee):    subagent wraps its own LLM calls in tracing_context
    4. Full production:   3 concurrent children + sequential phases, all fixed

Run with:
    python infra/label_evaluator_step_functions/dev_test_start_child_trace.py
"""

import asyncio
import os
import sys
import time
import types
import uuid

# ---------------------------------------------------------------------------
# Path setup (same as other dev scripts)
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
PROJECT_NAME = "dev-start-child-trace-fix"
os.environ["LANGCHAIN_PROJECT"] = PROJECT_NAME

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from langsmith import tracing_context
from langsmith.run_trees import RunTree, get_cached_client

from tracing import TraceContext, child_trace, start_child_trace, end_child_trace

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


class CurrencyReview(BaseModel):
    """Simulates CurrencyEvaluationResponse."""
    decision: str = Field(description="KEEP or CHANGE")
    confidence: float = Field(description="0.0 to 1.0")
    reasoning: str = Field(description="Why this decision")


class MetadataReview(BaseModel):
    """Simulates MetadataEvaluationResponse."""
    decision: str = Field(description="KEEP or CHANGE")
    confidence: float = Field(description="0.0 to 1.0")
    reasoning: str = Field(description="Why this decision")


print("=" * 70)
print("DEV TEST: start_child_trace + asyncio.gather LLM nesting")
print("=" * 70)
print(f"  LANGCHAIN_TRACING_V2 = {os.environ.get('LANGCHAIN_TRACING_V2')}")
print(f"  LANGCHAIN_PROJECT    = {PROJECT_NAME}")
print()
print("  This script replicates the Phase 1 pattern from unified_receipt_evaluator:")
print("    start_child_trace('currency_evaluation', ...)")
print("    start_child_trace('metadata_evaluation', ...)")
print("    await asyncio.gather(currency_task, metadata_task)")
print("  and shows how to fix LLM calls appearing as standalone root traces.")


# ===================================================================
# TEST 1: Broken baseline — start_child_trace + asyncio.gather, no fix
# ===================================================================
async def test_1_broken_baseline():
    print("\n" + "=" * 70)
    print("TEST 1: BROKEN BASELINE — LLM calls should create orphan root traces")
    print("  start_child_trace + asyncio.gather, LLM uses config=llm_config only")
    print("=" * 70)

    trace_id = det_uuid("test1-receipt-broken-v2")

    root = RunTree(
        id=trace_id,
        trace_id=trace_id,
        name="ReceiptEvaluation",
        run_type="chain",
        inputs={"merchant": "Test Store 1", "test": "1_broken_baseline"},
        session_name=PROJECT_NAME,
    )
    root.post()
    print(f"  Root trace_id: {root.trace_id}")

    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)

    parent_ctx = TraceContext(
        run_tree=root,
        headers=root.to_headers(),
        trace_id=str(root.trace_id),
        root_run_id=str(root.id),
    )

    # Wrap root in tracing_context (same as production after our earlier fix)
    with tracing_context(parent=root):

        # Create child traces the same way production does (start_child_trace)
        currency_ctx = start_child_trace(
            "currency_evaluation", parent_ctx,
            metadata={"phase": "currency"},
            inputs={"merchant_name": "Test Store 1"},
        )
        metadata_ctx = start_child_trace(
            "metadata_evaluation", parent_ctx,
            metadata={"phase": "metadata"},
            inputs={"merchant_name": "Test Store 1"},
        )

        async def eval_currency(ctx: TraceContext):
            """Simulates evaluate_currency_labels_async WITHOUT tracing_context fix."""
            llm_config = ctx.get_langchain_config() if ctx else None
            structured = llm.with_structured_output(CurrencyReview)
            # This ainvoke has NO tracing_context(parent=child) → orphan trace
            result = await structured.ainvoke(
                "Word='12.99' label=LINE_TOTAL. Is this a valid currency value? KEEP or CHANGE?",
                config=llm_config,
            )
            return {"decision": result.decision, "confidence": result.confidence}

        async def eval_metadata(ctx: TraceContext):
            """Simulates evaluate_metadata_labels_async WITHOUT tracing_context fix."""
            llm_config = ctx.get_langchain_config() if ctx else None
            structured = llm.with_structured_output(MetadataReview)
            result = await structured.ainvoke(
                "Word='Sprouts' label=MERCHANT_NAME. Is this correct? KEEP or CHANGE?",
                config=llm_config,
            )
            return {"decision": result.decision, "confidence": result.confidence}

        # Run concurrently — same as production's asyncio.gather
        currency_res, metadata_res = await asyncio.gather(
            eval_currency(currency_ctx),
            eval_metadata(metadata_ctx),
        )

        end_child_trace(currency_ctx, outputs=currency_res)
        end_child_trace(metadata_ctx, outputs=metadata_res)

    print(f"  Currency: {currency_res}")
    print(f"  Metadata: {metadata_res}")
    print("  EXPECTED: LLM calls are NOT children of currency/metadata spans")

    root.end()
    root.patch()
    time.sleep(0.5)
    return root


# ===================================================================
# TEST 2: Fixed at caller — wrap each coroutine in tracing_context
# ===================================================================
async def test_2_caller_fix():
    print("\n" + "=" * 70)
    print("TEST 2: FIXED AT CALLER — wrap coroutine in tracing_context(parent=child)")
    print("  The caller (unified_receipt_evaluator) wraps each gather task")
    print("=" * 70)

    trace_id = det_uuid("test2-receipt-caller-fix-v2")

    root = RunTree(
        id=trace_id,
        trace_id=trace_id,
        name="ReceiptEvaluation",
        run_type="chain",
        inputs={"merchant": "Test Store 2", "test": "2_caller_fix"},
        session_name=PROJECT_NAME,
    )
    root.post()
    print(f"  Root trace_id: {root.trace_id}")

    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)

    parent_ctx = TraceContext(
        run_tree=root,
        headers=root.to_headers(),
        trace_id=str(root.trace_id),
        root_run_id=str(root.id),
    )

    with tracing_context(parent=root):

        currency_ctx = start_child_trace(
            "currency_evaluation", parent_ctx,
            metadata={"phase": "currency"},
            inputs={"merchant_name": "Test Store 2"},
        )
        metadata_ctx = start_child_trace(
            "metadata_evaluation", parent_ctx,
            metadata={"phase": "metadata"},
            inputs={"merchant_name": "Test Store 2"},
        )

        # Same subagent function as Test 1 — no changes to subagent code
        async def eval_currency(ctx: TraceContext):
            llm_config = ctx.get_langchain_config() if ctx else None
            structured = llm.with_structured_output(CurrencyReview)
            result = await structured.ainvoke(
                "Word='5.49' label=LINE_TOTAL. Is this valid? KEEP or CHANGE?",
                config=llm_config,
            )
            return {"decision": result.decision, "confidence": result.confidence}

        async def eval_metadata(ctx: TraceContext):
            llm_config = ctx.get_langchain_config() if ctx else None
            structured = llm.with_structured_output(MetadataReview)
            result = await structured.ainvoke(
                "Word='Costco' label=MERCHANT_NAME. Is this correct? KEEP or CHANGE?",
                config=llm_config,
            )
            return {"decision": result.decision, "confidence": result.confidence}

        # FIX: Wrapper that sets tracing_context for the duration of the coroutine
        async def with_trace_parent(coro, ctx: TraceContext):
            """Wrap an awaitable so LLM calls inside it are traced as children."""
            if ctx and ctx.run_tree:
                with tracing_context(parent=ctx.run_tree):
                    return await coro
            return await coro

        # Run concurrently, each wrapped in tracing_context
        currency_res, metadata_res = await asyncio.gather(
            with_trace_parent(eval_currency(currency_ctx), currency_ctx),
            with_trace_parent(eval_metadata(metadata_ctx), metadata_ctx),
        )

        end_child_trace(currency_ctx, outputs=currency_res)
        end_child_trace(metadata_ctx, outputs=metadata_res)

    print(f"  Currency: {currency_res}")
    print(f"  Metadata: {metadata_res}")
    print("  EXPECTED: LLM calls ARE children of currency/metadata spans")

    root.end()
    root.patch()
    time.sleep(0.5)
    return root


# ===================================================================
# TEST 3: Fixed at callee — subagent wraps its own LLM calls
# ===================================================================
async def test_3_callee_fix():
    print("\n" + "=" * 70)
    print("TEST 3: FIXED AT CALLEE — subagent wraps LLM calls in tracing_context")
    print("  The subagent function uses trace_ctx.run_tree as parent")
    print("=" * 70)

    trace_id = det_uuid("test3-receipt-callee-fix-v2")

    root = RunTree(
        id=trace_id,
        trace_id=trace_id,
        name="ReceiptEvaluation",
        run_type="chain",
        inputs={"merchant": "Test Store 3", "test": "3_callee_fix"},
        session_name=PROJECT_NAME,
    )
    root.post()
    print(f"  Root trace_id: {root.trace_id}")

    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)

    parent_ctx = TraceContext(
        run_tree=root,
        headers=root.to_headers(),
        trace_id=str(root.trace_id),
        root_run_id=str(root.id),
    )

    with tracing_context(parent=root):

        currency_ctx = start_child_trace(
            "currency_evaluation", parent_ctx,
            metadata={"phase": "currency"},
            inputs={"merchant_name": "Test Store 3"},
        )
        metadata_ctx = start_child_trace(
            "metadata_evaluation", parent_ctx,
            metadata={"phase": "metadata"},
            inputs={"merchant_name": "Test Store 3"},
        )

        # Subagent wraps its own LLM call in tracing_context
        async def eval_currency(ctx: TraceContext):
            structured = llm.with_structured_output(CurrencyReview)
            # FIX: Wrap ainvoke in tracing_context(parent=ctx.run_tree)
            if ctx and ctx.run_tree:
                with tracing_context(parent=ctx.run_tree):
                    result = await structured.ainvoke(
                        "Word='8.99' label=SUBTOTAL. Is this valid? KEEP or CHANGE?",
                    )
            else:
                result = await structured.ainvoke(
                    "Word='8.99' label=SUBTOTAL. Is this valid? KEEP or CHANGE?",
                )
            return {"decision": result.decision, "confidence": result.confidence}

        async def eval_metadata(ctx: TraceContext):
            structured = llm.with_structured_output(MetadataReview)
            if ctx and ctx.run_tree:
                with tracing_context(parent=ctx.run_tree):
                    result = await structured.ainvoke(
                        "Word='2024-01-15' label=DATE. Is this correct? KEEP or CHANGE?",
                    )
            else:
                result = await structured.ainvoke(
                    "Word='2024-01-15' label=DATE. Is this correct? KEEP or CHANGE?",
                )
            return {"decision": result.decision, "confidence": result.confidence}

        currency_res, metadata_res = await asyncio.gather(
            eval_currency(currency_ctx),
            eval_metadata(metadata_ctx),
        )

        end_child_trace(currency_ctx, outputs=currency_res)
        end_child_trace(metadata_ctx, outputs=metadata_res)

    print(f"  Currency: {currency_res}")
    print(f"  Metadata: {metadata_res}")
    print("  EXPECTED: LLM calls ARE children of currency/metadata spans")

    root.end()
    root.patch()
    time.sleep(0.5)
    return root


# ===================================================================
# TEST 4: Full production simulation
# ===================================================================
async def test_4_full_production():
    print("\n" + "=" * 70)
    print("TEST 4: FULL PRODUCTION SIM — all phases, fixed pattern")
    print("  Phase 1: currency + metadata via start_child_trace + asyncio.gather")
    print("  Phase 2: financial via child_trace (already works)")
    print("  Phase 3: LLM review via child_trace (already works)")
    print("=" * 70)

    trace_id = det_uuid("test4-receipt-full-sim-v2")

    root = RunTree(
        id=trace_id,
        trace_id=trace_id,
        name="ReceiptEvaluation",
        run_type="chain",
        inputs={
            "merchant_name": "Sprouts",
            "word_count": 150,
            "label_count": 75,
            "test": "4_full_production_sim",
        },
        extra={
            "metadata": {
                "image_id": "sim456",
                "receipt_id": 1,
                "merchant_name": "Sprouts",
            }
        },
        tags=["unified-evaluation", "llm", "per-receipt"],
        session_name=PROJECT_NAME,
    )
    root.post()
    print(f"  Root trace_id: {root.trace_id}")

    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)

    parent_ctx = TraceContext(
        run_tree=root,
        headers=root.to_headers(),
        trace_id=str(root.trace_id),
        root_run_id=str(root.id),
    )

    with tracing_context(parent=root):

        # --- Phase 1: Currency + Metadata (concurrent via start_child_trace) ---
        currency_ctx = start_child_trace(
            "currency_evaluation", parent_ctx,
            metadata={"phase": "currency"},
            inputs={"merchant_name": "Sprouts", "num_visual_lines": 25},
        )
        metadata_ctx = start_child_trace(
            "metadata_evaluation", parent_ctx,
            metadata={"phase": "metadata"},
            inputs={"merchant_name": "Sprouts", "has_place": True},
        )
        geometric_ctx = start_child_trace(
            "geometric_evaluation", parent_ctx,
            metadata={"phase": "geometric"},
            inputs={"num_words": 150, "num_labels": 75},
        )

        async def sim_currency(ctx: TraceContext):
            structured = llm.with_structured_output(CurrencyReview)
            with tracing_context(parent=ctx.run_tree):
                result = await structured.ainvoke(
                    "Word='12.99' label=LINE_TOTAL. Valid currency? KEEP or CHANGE?"
                )
            return {"decision": result.decision, "confidence": result.confidence}

        async def sim_metadata(ctx: TraceContext):
            structured = llm.with_structured_output(MetadataReview)
            with tracing_context(parent=ctx.run_tree):
                result = await structured.ainvoke(
                    "Word='Sprouts Farmers Market' label=MERCHANT_NAME. Correct? KEEP or CHANGE?"
                )
            return {"decision": result.decision, "confidence": result.confidence}

        async def sim_geometric(ctx: TraceContext):
            """Geometric eval has no LLM calls — just simulated computation."""
            await asyncio.sleep(0.1)
            return {"issues_found": 3}

        currency_res, metadata_res, geometric_res = await asyncio.gather(
            sim_currency(currency_ctx),
            sim_metadata(metadata_ctx),
            sim_geometric(geometric_ctx),
        )

        end_child_trace(currency_ctx, outputs=currency_res)
        end_child_trace(metadata_ctx, outputs=metadata_res)
        end_child_trace(geometric_ctx, outputs=geometric_res)

        print(f"  Phase 1 — Currency: {currency_res}")
        print(f"  Phase 1 — Metadata: {metadata_res}")
        print(f"  Phase 1 — Geometric: {geometric_res}")

        # --- Phase 1 corrections (no LLM) ---
        with child_trace("apply_phase1_corrections", parent_ctx) as ctx:
            ctx.set_outputs({"corrections_applied": 2, "dedup_removed": 1})

        # --- Phase 2: Financial validation (child_trace — already works) ---
        with child_trace(
            "phase2_financial_validation", parent_ctx,
            metadata={"phase": "financial"},
        ) as financial_ctx:
            resp = await llm.ainvoke(
                "Receipt total is $45.67 but line items sum to $42.50. "
                "Tax is $3.17. Is the math correct? Answer YES or NO with explanation."
            )
            print(f"  Phase 2 — Financial: {resp.content[:60]}...")
            financial_ctx.set_outputs({"response": resp.content[:100]})

        # --- Phase 3: LLM review (child_trace — already works) ---
        with child_trace(
            "phase3_llm_review", parent_ctx,
            metadata={"phase": "review"},
        ) as review_ctx:
            structured = llm.with_structured_output(CurrencyReview)
            with tracing_context(parent=review_ctx.run_tree):
                result = structured.invoke(
                    "Word='ORGANIC MILK' label=LINE_TOTAL. "
                    "This looks like a product name, not a price. KEEP or CHANGE?"
                )
            print(f"  Phase 3 — Review: {result.decision} ({result.confidence})")
            review_ctx.set_outputs({"decision": result.decision})

        # --- Upload results (no LLM) ---
        with child_trace("upload_results", parent_ctx) as ctx:
            ctx.set_outputs({"status": "uploaded", "corrections": 5})

    print()
    print("  EXPECTED TRACE HIERARCHY:")
    print("    ReceiptEvaluation")
    print("      +-- currency_evaluation")
    print("      |     +-- ChatOpenAI (structured, auto-traced)")
    print("      +-- metadata_evaluation")
    print("      |     +-- ChatOpenAI (structured, auto-traced)")
    print("      +-- geometric_evaluation (no LLM)")
    print("      +-- apply_phase1_corrections (no LLM)")
    print("      +-- phase2_financial_validation")
    print("      |     +-- ChatOpenAI (auto-traced)")
    print("      +-- phase3_llm_review")
    print("      |     +-- ChatOpenAI (structured, auto-traced)")
    print("      +-- upload_results (no LLM)")

    root.end()
    root.patch()
    return root


# ===================================================================
# Main: Run all tests sequentially
# ===================================================================
async def main():
    root_1 = await test_1_broken_baseline()
    root_2 = await test_2_caller_fix()
    root_3 = await test_3_callee_fix()
    root_4 = await test_4_full_production()

    # Flush and summary
    print("\n--- Flushing traces ---")
    client = get_cached_client()
    client.flush()
    time.sleep(3)

    print("\n" + "=" * 70)
    print("ALL TESTS COMPLETE")
    print("=" * 70)
    print(f"\nProject: {PROJECT_NAME}")
    print(f"\nTrace IDs:")
    print(f"  Test 1 (broken baseline):   {root_1.trace_id}")
    print(f"  Test 2 (caller fix):        {root_2.trace_id}")
    print(f"  Test 3 (callee fix):        {root_3.trace_id}")
    print(f"  Test 4 (full production):   {root_4.trace_id}")
    print()
    print("WHAT TO CHECK IN LANGSMITH:")
    print("  Test 1: LLM calls should be separate root traces (NOT under currency/metadata)")
    print("  Test 2: LLM calls should be UNDER currency/metadata spans")
    print("  Test 3: LLM calls should be UNDER currency/metadata spans")
    print("  Test 4: All LLM calls should be under their respective phase spans")
    print()
    print("If Test 2 and 3 both work, the simpler fix is Test 2 (caller-side wrapper)")
    print("because it requires no changes to the subagent code in receipt_agent/.")


asyncio.run(main())
