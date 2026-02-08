#!/usr/bin/env python3
"""
Dev script: Prove @traceable replaces all manual trace plumbing.

Goal: Show that the merchant-level Lambda and receipt-level Lambda patterns
can use @traceable instead of start_child_trace/end_child_trace/child_trace,
while keeping deterministic root RunTree IDs for dedup.

Architecture being simulated:
    Step Function Execution
      ├── Merchant Lambda: PatternComputation (per merchant)
      │     ├── discover_geometric_patterns  (@traceable)
      │     └── discover_line_item_patterns  (@traceable)
      └── Receipt Lambda: ReceiptEvaluation (per receipt, parallel)
            ├── currency_evaluation   (@traceable, has LLM)
            ├── metadata_evaluation   (@traceable, has LLM)
            ├── geometric_evaluation  (@traceable, no LLM)
            ├── financial_validation  (@traceable, has LLM)
            ├── llm_review            (@traceable, has LLM + structured output)
            └── upload_results        (@traceable, no LLM)

Key things to prove:
    1. Manual root RunTree + tracing_context → @traceable children auto-nest
    2. asyncio.gather with @traceable functions → each gets its own child span
    3. LLM calls inside @traceable → auto-nest under the @traceable span
    4. Separate receipt traces are fully independent (no cross-talk)
    5. Merchant trace completes, then receipt traces run — separate root traces

Run with:
    python infra/label_evaluator_step_functions/dev_test_traceable.py
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
PROJECT_NAME = "dev-traceable-proof-v2"
os.environ["LANGCHAIN_PROJECT"] = PROJECT_NAME

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from langsmith import traceable, tracing_context
from langsmith.run_trees import RunTree, get_cached_client

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

TRACE_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def det_uuid(seed: str) -> str:
    """Same deterministic UUID5 scheme as production."""
    return str(uuid.uuid5(TRACE_NAMESPACE, seed))


# ---------------------------------------------------------------------------
# Pydantic models (simulating structured outputs)
# ---------------------------------------------------------------------------
class CurrencyReview(BaseModel):
    decision: str = Field(description="KEEP or CHANGE")
    confidence: float = Field(description="0.0 to 1.0")
    reasoning: str = Field(description="Why this decision")


class MetadataReview(BaseModel):
    decision: str = Field(description="KEEP or CHANGE")
    confidence: float = Field(description="0.0 to 1.0")
    reasoning: str = Field(description="Why this decision")


class FinancialReview(BaseModel):
    valid: bool = Field(description="Whether the math checks out")
    explanation: str = Field(description="What was checked")


# ---------------------------------------------------------------------------
# @traceable functions — these replace start_child_trace/end_child_trace
# ---------------------------------------------------------------------------

@traceable(name="discover_geometric_patterns", run_type="chain")
def discover_geometric_patterns(words: list[str], merchant_name: str) -> dict:
    """Simulates geometric pattern discovery (no LLM, just computation)."""
    time.sleep(0.1)  # simulate work
    return {
        "pattern_count": 5,
        "merchant_name": merchant_name,
        "word_count": len(words),
    }


@traceable(name="discover_line_item_patterns", run_type="chain")
def discover_line_item_patterns(words: list[str], merchant_name: str) -> dict:
    """Simulates line item pattern discovery (no LLM, just computation)."""
    time.sleep(0.1)  # simulate work
    return {
        "line_item_patterns": 3,
        "merchant_name": merchant_name,
    }


@traceable(name="currency_evaluation", run_type="chain")
async def evaluate_currency(llm, merchant_name: str) -> dict:
    """Simulates currency subagent — structured output LLM call."""
    structured = llm.with_structured_output(CurrencyReview)
    result = await structured.ainvoke(
        f"Word='12.99' label=LINE_TOTAL on receipt from {merchant_name}. "
        "Is this a valid currency value? KEEP or CHANGE?"
    )
    return {"decision": result.decision, "confidence": result.confidence}


@traceable(name="metadata_evaluation", run_type="chain")
async def evaluate_metadata(llm, merchant_name: str) -> dict:
    """Simulates metadata subagent — structured output LLM call."""
    structured = llm.with_structured_output(MetadataReview)
    result = await structured.ainvoke(
        f"Word='{merchant_name}' label=MERCHANT_NAME. Is this correct? KEEP or CHANGE?"
    )
    return {"decision": result.decision, "confidence": result.confidence}


@traceable(name="geometric_evaluation", run_type="chain")
async def evaluate_geometric(words: list[str]) -> dict:
    """Simulates geometric evaluation (no LLM)."""
    await asyncio.sleep(0.05)
    return {"issues_found": 2, "word_count": len(words)}


@traceable(name="financial_validation", run_type="chain")
async def validate_financial(llm, merchant_name: str) -> dict:
    """Simulates financial subagent — plain LLM call."""
    resp = await llm.ainvoke(
        f"Receipt from {merchant_name}: total $45.67, line items sum $42.50, "
        "tax $3.17. Is the math correct? YES or NO with explanation."
    )
    return {"response": resp.content[:100], "valid": "YES" in resp.content.upper()}


@traceable(name="llm_review", run_type="chain")
async def llm_review(llm, merchant_name: str) -> dict:
    """Simulates LLM review of geometric issues — structured output."""
    structured = llm.with_structured_output(CurrencyReview)
    result = await structured.ainvoke(
        f"Word='ORGANIC MILK' label=LINE_TOTAL on receipt from {merchant_name}. "
        "This looks like a product name, not a price. KEEP or CHANGE?"
    )
    return {"decision": result.decision, "confidence": result.confidence}


@traceable(name="upload_results", run_type="chain")
async def upload_results(receipt_id: str) -> dict:
    """Simulates uploading results to DynamoDB/S3."""
    await asyncio.sleep(0.05)
    return {"status": "uploaded", "receipt_id": receipt_id}


# ---------------------------------------------------------------------------
# Simulated Lambda handlers
# ---------------------------------------------------------------------------

async def merchant_lambda(execution_arn: str, merchant_name: str) -> RunTree:
    """Simulates the merchant-level Lambda (DiscoverPatterns / UnifiedPatternBuilder).

    Creates a manual root RunTree with deterministic ID, then uses @traceable
    for all child operations.
    """
    trace_id = det_uuid(f"merchant:{execution_arn}:{merchant_name}")

    root = RunTree(
        id=trace_id,
        trace_id=trace_id,
        name="PatternComputation",
        run_type="chain",
        inputs={"merchant_name": merchant_name, "receipt_count": 5},
        extra={"metadata": {"merchant_name": merchant_name, "phase": "pattern_computation"}},
        tags=["step-function", "label-evaluator", "per-merchant"],
        session_name=PROJECT_NAME,
    )
    root.post()
    print(f"  Merchant root trace_id: {trace_id[:12]}...")

    # All @traceable calls inside this block auto-nest under root
    with tracing_context(parent=root):
        geo = discover_geometric_patterns(
            words=["MILK", "12.99", "EGGS", "3.49"],
            merchant_name=merchant_name,
        )
        line = discover_line_item_patterns(
            words=["MILK", "12.99", "EGGS", "3.49"],
            merchant_name=merchant_name,
        )

    print(f"    Geometric patterns: {geo}")
    print(f"    Line item patterns: {line}")

    root.outputs = {"geometric": geo, "line_items": line}
    root.end()
    root.patch()
    return root


async def receipt_lambda(
    execution_arn: str,
    image_id: str,
    receipt_id: int,
    merchant_name: str,
    llm,
) -> RunTree:
    """Simulates the receipt-level Lambda (UnifiedReceiptEvaluator).

    Creates a manual root RunTree with deterministic ID, then uses @traceable
    for all child operations including concurrent phases.
    """
    trace_id = det_uuid(f"receipt:{execution_arn}:{image_id}:{receipt_id}")

    root = RunTree(
        id=trace_id,
        trace_id=trace_id,
        name="ReceiptEvaluation",
        run_type="chain",
        inputs={
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
        },
        extra={"metadata": {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
        }},
        tags=["unified-evaluation", "llm", "per-receipt"],
        session_name=PROJECT_NAME,
    )
    root.post()
    print(f"  Receipt root trace_id: {trace_id[:12]}... ({image_id}:{receipt_id})")

    with tracing_context(parent=root):
        # Phase 1: currency + metadata + geometric run concurrently
        currency_res, metadata_res, geometric_res = await asyncio.gather(
            evaluate_currency(llm, merchant_name),
            evaluate_metadata(llm, merchant_name),
            evaluate_geometric(["MILK", "12.99", "EGGS", "3.49", "TAX", "1.05"]),
        )
        print(f"    Phase 1 — currency={currency_res['decision']}, "
              f"metadata={metadata_res['decision']}, "
              f"geometric={geometric_res['issues_found']} issues")

        # Phase 2: financial validation (sequential, needs Phase 1 results)
        financial_res = await validate_financial(llm, merchant_name)
        print(f"    Phase 2 — financial: {financial_res['valid']}")

        # Phase 3: LLM review (sequential, needs geometric issues)
        if geometric_res["issues_found"] > 0:
            review_res = await llm_review(llm, merchant_name)
            print(f"    Phase 3 — review: {review_res['decision']}")
        else:
            review_res = {"decision": "SKIP", "confidence": 1.0}

        # Upload
        upload_res = await upload_results(f"{image_id}:{receipt_id}")
        print(f"    Upload: {upload_res['status']}")

    root.outputs = {
        "currency": currency_res,
        "metadata": metadata_res,
        "geometric": geometric_res,
        "financial": financial_res,
        "review": review_res,
    }
    root.end()
    root.patch()
    return root


# ---------------------------------------------------------------------------
# Main: simulate the full step function flow
# ---------------------------------------------------------------------------
async def main():
    print("=" * 70)
    print("DEV TEST: @traceable proof-of-concept")
    print("=" * 70)
    print(f"  LANGCHAIN_TRACING_V2 = {os.environ.get('LANGCHAIN_TRACING_V2')}")
    print(f"  LANGCHAIN_PROJECT    = {PROJECT_NAME}")
    print()

    execution_arn = "arn:aws:states:us-east-1:123456789012:execution:test:dev-traceable-proof-v2"
    llm = create_llm(model=TEST_MODEL, temperature=0.0, timeout=30)

    # --- Step 1: Merchant Lambda (pattern computation) ---
    print("STEP 1: Merchant Lambda — PatternComputation")
    print("-" * 50)
    merchant_root = await merchant_lambda(execution_arn, "Sprouts Farmers Market")
    print()

    # --- Step 2: Receipt Lambdas (one per receipt, run concurrently) ---
    print("STEP 2: Receipt Lambdas — ReceiptEvaluation (3 receipts)")
    print("-" * 50)

    receipts = [
        ("img-abc123", 0, "Sprouts Farmers Market"),
        ("img-abc123", 1, "Sprouts Farmers Market"),
        ("img-def456", 0, "Sprouts Farmers Market"),
    ]

    # Run receipt evaluations concurrently (simulating Map state)
    receipt_roots = await asyncio.gather(*[
        receipt_lambda(execution_arn, img_id, rcpt_id, merchant, llm)
        for img_id, rcpt_id, merchant in receipts
    ])

    # --- Flush ---
    print("\n--- Flushing traces ---")
    client = get_cached_client()
    client.flush()
    time.sleep(3)

    # --- Summary ---
    print("\n" + "=" * 70)
    print("ALL DONE")
    print("=" * 70)
    print(f"\nProject: {PROJECT_NAME}")
    print(f"\nTraces created:")
    print(f"  Merchant:  {str(merchant_root.trace_id)[:12]}... (PatternComputation)")
    for i, root in enumerate(receipt_roots):
        img_id, rcpt_id, _ = receipts[i]
        print(f"  Receipt {i}: {str(root.trace_id)[:12]}... (ReceiptEvaluation {img_id}:{rcpt_id})")
    print()
    print("WHAT TO VERIFY IN LANGSMITH:")
    print("  1. Merchant trace: PatternComputation root with 2 children (no LLM)")
    print("  2. Each receipt trace: ReceiptEvaluation root with 6 children")
    print("     - currency_evaluation → RunnableSequence → ChatOpenAI")
    print("     - metadata_evaluation → RunnableSequence → ChatOpenAI")
    print("     - geometric_evaluation (no LLM)")
    print("     - financial_validation → ChatOpenAI")
    print("     - llm_review → RunnableSequence → ChatOpenAI")
    print("     - upload_results (no LLM)")
    print("  3. No orphan/standalone LLM traces")
    print("  4. No cross-talk between receipt traces")
    print("  5. All 4 traces are independent roots in the same project")


asyncio.run(main())
