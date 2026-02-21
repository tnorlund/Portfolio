"""
Dev script: Full two-tier financial validation pipeline.

Runs the EXACT same logic as the step function's Phase 2:
  Tier 1: extract → filter junk → detect math issues (incl. GT_DIRECT)
  Tier 2: structured-output LLM call (single call, Pydantic schema)

This lets you iterate locally before deploying.

Usage:
    cd /Users/tnorlund/portfolio_copy_a/Portfolio
    .venv/bin/python dev_two_tier_test.py                     # all receipts
    .venv/bin/python dev_two_tier_test.py --limit 50          # first 50 images
    .venv/bin/python dev_two_tier_test.py --merchant Sprouts  # filter by merchant
    .venv/bin/python dev_two_tier_test.py --receipt ce0da565 2  # single receipt
    .venv/bin/python dev_two_tier_test.py --verbose           # show per-receipt detail
    .venv/bin/python dev_two_tier_test.py --tier2-only        # skip fast path, always LLM
"""

import argparse
import logging
import time
from collections import Counter

from receipt_chroma.data.chroma_client import ChromaClient
from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env, load_secrets

from receipt_agent.agents.label_evaluator.financial_structured import (
    TwoTierFinancialResult,
    run_two_tier_financial_validation,
)
from receipt_agent.agents.label_evaluator.word_context import (
    assemble_visual_lines,
    build_word_contexts,
)
from receipt_agent.utils.llm_factory import create_llm

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

_env = load_env("dev")
_secrets = load_secrets("dev")
TABLE_NAME = _env.get("dynamodb_table_name", "")
CHROMA_CLOUD_API_KEY = _secrets.get("portfolio:CHROMA_CLOUD_API_KEY", "")
CHROMA_CLOUD_TENANT = _secrets.get("portfolio:CHROMA_CLOUD_TENANT", "")
CHROMA_CLOUD_DATABASE = _secrets.get("portfolio:CHROMA_CLOUD_DATABASE", "")
OPENROUTER_API_KEY = _secrets.get("portfolio:OPENROUTER_API_KEY", "")

FINANCIAL_LABELS = {
    "GRAND_TOTAL", "SUBTOTAL", "TAX", "TIP",
    "LINE_TOTAL", "DISCOUNT", "UNIT_PRICE", "QUANTITY",
    "CASH_BACK",
}


# ---------------------------------------------------------------------------
# Two-tier runner (mirrors unified_receipt_evaluator.py Phase 2)
# ---------------------------------------------------------------------------


def run_two_tier(
    words, labels, visual_lines, chroma_client, merchant_name,
    image_id, receipt_id, llm, *, skip_fast_path=False,
) -> dict:
    """Run the full two-tier pipeline on one receipt.

    Thin wrapper around run_two_tier_financial_validation() that returns the
    verbose dict format the display code expects.
    """
    result: TwoTierFinancialResult = run_two_tier_financial_validation(
        llm=llm,
        words=words,
        labels=labels,
        visual_lines=visual_lines,
        chroma_client=chroma_client,
        image_id=image_id,
        receipt_id=receipt_id,
        merchant_name=merchant_name,
        skip_fast_path=skip_fast_path,
    )

    # Build the verbose dict that print_receipt_result() expects
    fp_detail = {
        "values": {},
        "issues": [],
        "filtered": True,
        "text_scan_used": result.text_scan_used,
        "label_types": result.label_type_count,
    }

    llm_detail = None
    if result.tier == "llm":
        valid_count = sum(
            1 for d in result.decisions
            if d.get("llm_review", {}).get("decision") == "VALID"
        )
        needs_review_count = sum(
            1 for d in result.decisions
            if d.get("llm_review", {}).get("decision") == "NEEDS_REVIEW"
        )
        llm_detail = {
            "decisions_count": len(result.decisions),
            "valid_count": valid_count,
            "needs_review_count": needs_review_count,
            "duration_s": round(result.duration_seconds, 2),
            "fast_path_summary": "",
        }

    return {
        "tier": result.tier,
        "status": result.status,
        "decisions": result.decisions,
        "fast_path": fp_detail,
        "llm": llm_detail,
        "duration_s": result.duration_seconds,
    }


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def print_receipt_result(result: dict, merchant: str, image_id: str,
                         receipt_id: int, verbose: bool = False):
    """Print a single receipt's result."""
    tier = result["tier"]
    status = result["status"]
    dur = result["duration_s"]

    # Tier/status badge
    if tier == "fast_path":
        badge = "FAST PATH"
    elif tier == "llm":
        badge = "LLM"
    else:
        badge = "NO DATA"

    status_icon = {
        "balanced": "BALANCED",
        "issues": "ISSUES",
        "no_equation": "NO EQ",
        "no_data": "NO DATA",
        "error": "ERROR",
        "single_label": "1-LABEL",
    }.get(status, status.upper())

    print(
        f"  {merchant[:30]:<30s} {image_id[:8]} r={receipt_id}  "
        f"[{badge:>9s}] {status_icon:<10s} {dur:.2f}s"
    )

    if not verbose:
        return

    # Fast path detail
    fp = result["fast_path"]
    if fp.get("text_scan_used"):
        print(f"    TEXT SCAN: supplemented extraction → {fp.get('label_types', '?')} label types")
    if fp["values"]:
        for label, vals in fp["values"].items():
            val_str = ", ".join(f"{v['val']:.2f}" for v in vals)
            print(f"    {label}: [{val_str}]")
    if fp["issues"]:
        for iss in fp["issues"]:
            desc = iss["desc"]
            if len(desc) > 100:
                desc = desc[:97] + "..."
            print(f"    ISSUE: {iss['type']}: {desc}")

    # LLM detail
    llm = result.get("llm")
    if llm:
        print(
            f"    LLM: {llm['decisions_count']} decisions "
            f"({llm['valid_count']} VALID, {llm['needs_review_count']} NEEDS_REVIEW) "
            f"in {llm['duration_s']}s"
        )

    # Decisions detail
    for d in result["decisions"][:10]:
        issue = d.get("issue", {})
        review = d.get("llm_review", {})
        lid = issue.get("line_id", "?")
        wid = issue.get("word_id", "?")
        text = issue.get("word_text", "?")
        label = issue.get("current_label", "?")
        decision = review.get("decision", "?")
        reasoning = review.get("reasoning", "")
        if len(reasoning) > 60:
            reasoning = reasoning[:57] + "..."
        print(
            f"      L{lid} W{wid} {text:>10s} {label:<14s} -> {decision}  {reasoning}"
        )
    remaining = len(result["decisions"]) - 10
    if remaining > 0:
        print(f"      ... +{remaining} more decisions")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Two-tier financial validation — full pipeline"
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Max images to process (0=all)",
    )
    parser.add_argument(
        "--merchant", type=str, default="",
        help="Filter by merchant name (substring)",
    )
    parser.add_argument(
        "--receipt", nargs=2, metavar=("IMAGE_ID_PREFIX", "RECEIPT_ID"),
        help="Run on a single receipt",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show per-receipt detail (values, issues, decisions)",
    )
    parser.add_argument(
        "--tier2-only", action="store_true",
        help="Skip fast path — always run LLM (useful for testing LLM quality)",
    )
    args = parser.parse_args()

    dynamo_client = DynamoClient(table_name=TABLE_NAME)
    chroma_client = ChromaClient(
        cloud_api_key=CHROMA_CLOUD_API_KEY,
        cloud_tenant=CHROMA_CLOUD_TENANT,
        cloud_database=CHROMA_CLOUD_DATABASE,
    )
    llm = create_llm(
        model="x-ai/grok-4.1-fast",
        api_key=OPENROUTER_API_KEY,
        temperature=0.0,
        timeout=120,
    )

    # Build receipt list
    if args.receipt:
        prefix = args.receipt[0].lower()
        rid = int(args.receipt[1])
        # Find the matching image
        all_images = []
        last_key = None
        while True:
            images, last_key = dynamo_client.list_images(
                limit=500, last_evaluated_key=last_key,
            )
            all_images.extend(images)
            if last_key is None:
                break
        matches = [
            img for img in all_images
            if img.image_id.lower().startswith(prefix)
        ]
        if not matches:
            print(f"No image found matching prefix '{prefix}'")
            return
        receipt_list = [
            (getattr(m, "merchant_name", None) or "Unknown",
             m.image_id, rid)
            for m in matches
        ]
    else:
        # All receipts
        all_images = []
        last_key = None
        while True:
            images, last_key = dynamo_client.list_images(
                limit=500, last_evaluated_key=last_key,
            )
            all_images.extend(images)
            if last_key is None:
                break

        if args.limit:
            all_images = all_images[:args.limit]

        receipt_list = []
        for img in all_images:
            merchant = getattr(img, "merchant_name", None) or "Unknown"
            if args.merchant and args.merchant.lower() not in merchant.lower():
                continue
            receipt_count = getattr(img, "receipt_count", 1) or 1
            for rid in range(1, receipt_count + 1):
                receipt_list.append((merchant, img.image_id, rid))

    print("=" * 80)
    print("TWO-TIER FINANCIAL VALIDATION — FULL PIPELINE")
    print("=" * 80)
    print(f"  Table:     {TABLE_NAME}")
    print(f"  Receipts:  {len(receipt_list)}")
    print(f"  Mode:      {'TIER2-ONLY (always LLM)' if args.tier2_only else 'NORMAL (fast path + LLM fallback)'}")
    print()

    # Run
    results = []
    tier_counts = Counter()
    status_counts = Counter()
    llm_calls = 0
    total_llm_time = 0.0

    for idx, (merchant, image_id, receipt_id) in enumerate(receipt_list, 1):
        try:
            details = dynamo_client.get_receipt_details(image_id, receipt_id)
            words = details.words
            labels = details.labels
        except Exception:
            results.append({
                "merchant": merchant, "image_id": image_id,
                "receipt_id": receipt_id,
                "tier": "error", "status": "error",
                "decisions": [], "fast_path": {}, "llm": None,
                "duration_s": 0,
            })
            tier_counts["error"] += 1
            status_counts["error"] += 1
            continue

        if words:
            word_contexts = build_word_contexts(words, labels)
            visual_lines = assemble_visual_lines(word_contexts)
        else:
            visual_lines = []

        result = run_two_tier(
            words, labels, visual_lines, chroma_client, merchant,
            image_id, receipt_id, llm,
            skip_fast_path=args.tier2_only,
        )
        result["merchant"] = merchant
        result["image_id"] = image_id
        result["receipt_id"] = receipt_id
        results.append(result)

        tier_counts[result["tier"]] += 1
        status_counts[result["status"]] += 1

        if result["tier"] == "llm":
            llm_calls += 1
            if result.get("llm"):
                total_llm_time += result["llm"]["duration_s"]

        # Progress
        if not args.verbose and idx % 25 == 0:
            print(f"  ... processed {idx}/{len(receipt_list)}")

        if args.verbose or args.receipt:
            print_receipt_result(
                result, merchant, image_id, receipt_id, verbose=True,
            )
        elif result["tier"] == "llm":
            # Always show LLM receipts even in non-verbose mode
            print_receipt_result(
                result, merchant, image_id, receipt_id, verbose=False,
            )

    # ==================================================================
    # Summary
    # ==================================================================
    total = len(results)

    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print("=" * 80)

    # Tier breakdown
    fp_balanced = sum(
        1 for r in results
        if r["tier"] == "fast_path" and r["status"] == "balanced"
    )
    fp_single = status_counts.get("single_label", 0)
    fp_count = tier_counts.get("fast_path", 0)
    llm_count = tier_counts.get("llm", 0)
    no_data = tier_counts.get("no_data", 0)
    errors = tier_counts.get("error", 0)
    checkable = fp_balanced + llm_count  # single_label receipts are skipped, not "checkable"

    # Text scan stats
    text_scan_count = sum(
        1 for r in results if r.get("fast_path", {}).get("text_scan_used")
    )
    text_scan_promoted = sum(
        1 for r in results
        if r.get("fast_path", {}).get("text_scan_used")
        and r["status"] == "balanced"
    )

    print(f"\n  Tier Breakdown:")
    if total:
        print(f"    Fast path:  {fp_balanced:>5d}  ({fp_balanced/total*100:.1f}%)  balanced")
        if fp_single:
            print(f"    Single-lbl: {fp_single:>5d}  ({fp_single/total*100:.1f}%)  skipped (no cross-check)")
        print(f"    LLM:        {llm_count:>5d}  ({llm_count/total*100:.1f}%)")
    if no_data:
        print(f"    No data:    {no_data:>5d}")
    if errors:
        print(f"    Errors:     {errors:>5d}")
    print(f"    Total:      {total:>5d}")

    if checkable:
        print(f"\n    Fast path success rate:  {fp_balanced}/{checkable} = {fp_balanced/checkable*100:.1f}%")

    if text_scan_count:
        print(f"\n  Text Scan Supplement:")
        print(f"    Receipts supplemented: {text_scan_count}")
        print(f"    Promoted to balanced:  {text_scan_promoted}")

    # Status breakdown
    print(f"\n  Status Breakdown:")
    for status in ["balanced", "single_label", "issues", "no_equation", "no_data", "error"]:
        count = status_counts.get(status, 0)
        if count:
            print(f"    {status:<14s} {count:>5d}")

    # LLM stats
    if llm_calls:
        llm_balanced = sum(
            1 for r in results
            if r["tier"] == "llm" and r["status"] == "balanced"
        )
        llm_issues = sum(
            1 for r in results
            if r["tier"] == "llm" and r["status"] == "issues"
        )
        llm_no_eq = sum(
            1 for r in results
            if r["tier"] == "llm" and r["status"] == "no_equation"
        )
        print(f"\n  LLM Performance ({llm_calls} calls, {total_llm_time:.1f}s total):")
        print(f"    Balanced:    {llm_balanced:>5d}")
        print(f"    Issues:      {llm_issues:>5d}")
        print(f"    No equation: {llm_no_eq:>5d}")
        if llm_calls:
            print(f"    Avg time:    {total_llm_time/llm_calls:.2f}s per call")

    # Overall resolution rate
    balanced_total = status_counts.get("balanced", 0)
    if checkable:
        print(f"\n  Overall Resolution:")
        print(f"    Balanced (any tier): {balanced_total}/{checkable} = {balanced_total/checkable*100:.1f}%")
        print(f"    Unresolved:          {checkable - balanced_total}/{checkable}")

    # Show unresolved receipts (exclude single_label — intentionally skipped)
    unresolved = [
        r for r in results
        if r["tier"] in ("llm", "fast_path")
        and r["status"] not in ("balanced", "single_label")
    ]
    if unresolved and len(unresolved) <= 30:
        print(f"\n  Unresolved Receipts ({len(unresolved)}):")
        for r in unresolved:
            llm_info = ""
            if r.get("llm"):
                llm_info = f"  LLM: {r['llm']['decisions_count']}d/{r['llm']['valid_count']}v"
            fp_issues = ""
            if r["fast_path"].get("issues"):
                fp_issues = ", ".join(i["type"] for i in r["fast_path"]["issues"])
            print(
                f"    {r['merchant'][:28]:<28s} {r['image_id'][:8]} r={r['receipt_id']}  "
                f"{r['status']:<12s} {fp_issues}{llm_info}"
            )
    elif unresolved:
        print(f"\n  Unresolved Receipts: {len(unresolved)} (use --verbose to see detail)")


if __name__ == "__main__":
    main()
