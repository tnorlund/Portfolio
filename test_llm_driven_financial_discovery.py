#!/usr/bin/env python3
"""
Test script for the LLM-driven financial discovery sub-agent.

This tests the new approach that uses LLM reasoning rather than hard-coded rules
to identify financial values (LINE_TOTAL, SUBTOTAL, TAX, GRAND_TOTAL) on receipts.

Features tested:
1. Structure analysis and numeric candidate identification
2. LLM reasoning about financial value assignments
3. Mathematical relationship verification
4. Rich context generation for label assignment
5. Works with sparse or missing labels
"""

import asyncio
import os
import sys

# Add current directory to path so we can import receipt_agent
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from receipt_agent.subagents.financial_validation.llm_driven_graph import (
    create_llm_driven_financial_graph,
    run_llm_driven_financial_discovery,
)


async def test_llm_driven_financial_discovery():
    """Test LLM-driven financial discovery with sample receipt data."""

    print("🧠 Testing LLM-Driven Financial Discovery Sub-Agent")
    print("=" * 60)

    # Sample receipt data - realistic grocery receipt
    sample_receipt_text = """
    WHOLE FOODS MARKET
    123 Organic Ave, Austin TX 78701
    Phone: (512) 555-0199

    Transaction #: 12345-67890
    Date: 12/13/2024  Time: 2:30 PM
    Cashier: Sarah M.

    365 Organic Bananas     2.5 lbs @ $1.99/lb     $4.98
    Avocados Large          3 ea @ $1.29 ea        $3.87
    Organic Spinach         1 bag                  $3.49
    Almond Milk Unsweetened 1 @ $4.99             $4.99
    Free Range Eggs         1 dz @ $6.49          $6.49

    SUBTOTAL                                      $23.82
    TX SALES TAX 8.25%                             $1.97
    TOTAL                                         $25.79

    VISA CARD ****1234                           $25.79

    Thank you for shopping with us!
    Visit us at wholefoodsmarket.com
    """

    # Sample word data (simulating OCR output)
    sample_words = [
        # Header
        {"line_id": 1, "word_id": 1, "text": "WHOLE", "confidence": 0.99},
        {"line_id": 1, "word_id": 2, "text": "FOODS", "confidence": 0.99},
        {"line_id": 1, "word_id": 3, "text": "MARKET", "confidence": 0.99},
        # Line item 1: Bananas
        {"line_id": 8, "word_id": 1, "text": "365", "confidence": 0.95},
        {"line_id": 8, "word_id": 2, "text": "Organic", "confidence": 0.98},
        {"line_id": 8, "word_id": 3, "text": "Bananas", "confidence": 0.97},
        {"line_id": 8, "word_id": 4, "text": "2.5", "confidence": 0.96},
        {"line_id": 8, "word_id": 5, "text": "lbs", "confidence": 0.94},
        {"line_id": 8, "word_id": 6, "text": "@", "confidence": 0.90},
        {"line_id": 8, "word_id": 7, "text": "$1.99/lb", "confidence": 0.93},
        {"line_id": 8, "word_id": 8, "text": "$4.98", "confidence": 0.98},
        # Line item 2: Avocados
        {"line_id": 9, "word_id": 1, "text": "Avocados", "confidence": 0.97},
        {"line_id": 9, "word_id": 2, "text": "Large", "confidence": 0.95},
        {"line_id": 9, "word_id": 3, "text": "3", "confidence": 0.98},
        {"line_id": 9, "word_id": 4, "text": "ea", "confidence": 0.92},
        {"line_id": 9, "word_id": 5, "text": "@", "confidence": 0.88},
        {"line_id": 9, "word_id": 6, "text": "$1.29", "confidence": 0.96},
        {"line_id": 9, "word_id": 7, "text": "ea", "confidence": 0.90},
        {"line_id": 9, "word_id": 8, "text": "$3.87", "confidence": 0.97},
        # Line item 3: Spinach
        {"line_id": 10, "word_id": 1, "text": "Organic", "confidence": 0.98},
        {"line_id": 10, "word_id": 2, "text": "Spinach", "confidence": 0.99},
        {"line_id": 10, "word_id": 3, "text": "1", "confidence": 0.97},
        {"line_id": 10, "word_id": 4, "text": "bag", "confidence": 0.94},
        {"line_id": 10, "word_id": 5, "text": "$3.49", "confidence": 0.98},
        # Line item 4: Almond Milk
        {"line_id": 11, "word_id": 1, "text": "Almond", "confidence": 0.97},
        {"line_id": 11, "word_id": 2, "text": "Milk", "confidence": 0.98},
        {
            "line_id": 11,
            "word_id": 3,
            "text": "Unsweetened",
            "confidence": 0.96,
        },
        {"line_id": 11, "word_id": 4, "text": "1", "confidence": 0.98},
        {"line_id": 11, "word_id": 5, "text": "@", "confidence": 0.89},
        {"line_id": 11, "word_id": 6, "text": "$4.99", "confidence": 0.97},
        {"line_id": 11, "word_id": 7, "text": "$4.99", "confidence": 0.98},
        # Line item 5: Eggs
        {"line_id": 12, "word_id": 1, "text": "Free", "confidence": 0.96},
        {"line_id": 12, "word_id": 2, "text": "Range", "confidence": 0.97},
        {"line_id": 12, "word_id": 3, "text": "Eggs", "confidence": 0.99},
        {"line_id": 12, "word_id": 4, "text": "1", "confidence": 0.98},
        {"line_id": 12, "word_id": 5, "text": "dz", "confidence": 0.93},
        {"line_id": 12, "word_id": 6, "text": "@", "confidence": 0.87},
        {"line_id": 12, "word_id": 7, "text": "$6.49", "confidence": 0.97},
        {"line_id": 12, "word_id": 8, "text": "$6.49", "confidence": 0.98},
        # Totals section
        {"line_id": 14, "word_id": 1, "text": "SUBTOTAL", "confidence": 0.99},
        {"line_id": 14, "word_id": 2, "text": "$23.82", "confidence": 0.98},
        {"line_id": 15, "word_id": 1, "text": "TX", "confidence": 0.96},
        {"line_id": 15, "word_id": 2, "text": "SALES", "confidence": 0.97},
        {"line_id": 15, "word_id": 3, "text": "TAX", "confidence": 0.98},
        {"line_id": 15, "word_id": 4, "text": "8.25%", "confidence": 0.95},
        {"line_id": 15, "word_id": 5, "text": "$1.97", "confidence": 0.98},
        {"line_id": 16, "word_id": 1, "text": "TOTAL", "confidence": 0.99},
        {"line_id": 16, "word_id": 2, "text": "$25.79", "confidence": 0.99},
        # Payment
        {"line_id": 18, "word_id": 1, "text": "VISA", "confidence": 0.97},
        {"line_id": 18, "word_id": 2, "text": "CARD", "confidence": 0.96},
        {"line_id": 18, "word_id": 3, "text": "****1234", "confidence": 0.94},
        {"line_id": 18, "word_id": 4, "text": "$25.79", "confidence": 0.98},
    ]

    # Start with minimal labels (simulating sparse labeling scenario)
    sample_labels = [
        # Only a few labels exist - agent should discover the rest
        {
            "line_id": 8,
            "word_id": 3,
            "label": "PRODUCT_NAME",
            "validation_status": "VALID",
        },
        {
            "line_id": 14,
            "word_id": 1,
            "label": "SUBTOTAL",
            "validation_status": "VALID",
        },
        {
            "line_id": 16,
            "word_id": 1,
            "label": "GRAND_TOTAL",
            "validation_status": "VALID",
        },
    ]

    # Optional table structure (simulating table sub-agent output)
    sample_table_structure = {
        "summary": "Receipt has structured line items with product names, quantities, unit prices, and line totals in columns",
        "columns": [
            {
                "column_id": 0,
                "type": "product_info",
                "words": ["365", "Organic", "Bananas", "Avocados", "Large"],
            },
            {
                "column_id": 1,
                "type": "quantity",
                "words": ["2.5", "lbs", "3", "ea", "1"],
            },
            {
                "column_id": 2,
                "type": "unit_price",
                "words": ["$1.99/lb", "$1.29", "$4.99", "$6.49"],
            },
            {
                "column_id": 3,
                "type": "line_total",
                "words": ["$4.98", "$3.87", "$3.49", "$4.99", "$6.49"],
            },
        ],
        "rows": [
            {"row_id": 8, "type": "line_item", "line_id": 8},
            {"row_id": 9, "type": "line_item", "line_id": 9},
            {"row_id": 10, "type": "line_item", "line_id": 10},
            {"row_id": 11, "type": "line_item", "line_id": 11},
            {"row_id": 12, "type": "line_item", "line_id": 12},
            {"row_id": 14, "type": "subtotal", "line_id": 14},
            {"row_id": 15, "type": "tax", "line_id": 15},
            {"row_id": 16, "type": "total", "line_id": 16},
        ],
    }

    print("📄 Sample Receipt Summary:")
    print(f"   - Total words: {len(sample_words)}")
    print(f"   - Existing labels: {len(sample_labels)}")
    print(f"   - Has table structure: {sample_table_structure is not None}")
    print("   - Expected SUBTOTAL: $23.82")
    print("   - Expected TAX: $1.97")
    print("   - Expected GRAND_TOTAL: $25.79")
    print("   - Expected LINE_TOTALs: $4.98, $3.87, $3.49, $4.99, $6.49")
    print()

    # Create and run the LLM-driven financial discovery sub-agent
    print("🔧 Creating LLM-driven financial discovery graph...")
    try:
        graph, state_holder = create_llm_driven_financial_graph()
        print("✅ Graph created successfully")
    except Exception as e:
        print(f"❌ Failed to create graph: {e}")
        return

    print("\n🧠 Running LLM-driven financial discovery...")
    try:
        result = await run_llm_driven_financial_discovery(
            graph=graph,
            state_holder=state_holder,
            receipt_text=sample_receipt_text,
            labels=sample_labels,
            words=sample_words,
            table_structure=sample_table_structure,
        )
        print("✅ Financial discovery completed")
    except Exception as e:
        print(f"❌ Financial discovery failed: {e}")
        import traceback

        traceback.print_exc()
        return

    # Display results
    print("\n" + "=" * 60)
    print("📊 FINANCIAL DISCOVERY RESULTS")
    print("=" * 60)

    # Financial candidates
    financial_candidates = result.get("financial_candidates", {})
    if financial_candidates:
        print("\n💰 Financial Value Candidates:")
        for financial_type, candidates in financial_candidates.items():
            print(f"\n   {financial_type}:")
            for i, candidate in enumerate(candidates, 1):
                print(
                    f"      {i}. Line {candidate.get('line_id')}, Word {candidate.get('word_id')}"
                )
                print(f"         Value: ${candidate.get('value'):.2f}")
                print(
                    f"         Confidence: {candidate.get('confidence', 0.0):.2f}"
                )
                print(
                    f"         Reasoning: {candidate.get('reasoning', 'N/A')}"
                )
    else:
        print("\n❌ No financial candidates identified")

    # Mathematical validation
    math_validation = result.get("mathematical_validation", {})
    print(f"\n🧮 Mathematical Validation:")
    print(
        f"   - Tests passed: {math_validation.get('verified', 0)}/{math_validation.get('total_tests', 0)}"
    )
    print(
        f"   - All valid: {'✅ Yes' if math_validation.get('all_valid', False) else '❌ No'}"
    )

    if math_validation.get("test_details"):
        print("\n   Test Details:")
        for test in math_validation["test_details"]:
            test_name = test.get("test_name", "Unknown")
            passes = test.get("passes", False)
            status = "✅ PASS" if passes else "❌ FAIL"
            print(f"      {status} {test_name}")

            if "grand_total" in test:
                print(
                    f"         Grand Total: ${test.get('grand_total', 0):.2f}"
                )
                print(f"         Subtotal: ${test.get('subtotal', 0):.2f}")
                print(f"         Tax: ${test.get('tax', 0):.2f}")
                print(
                    f"         Calculated: ${test.get('calculated_total', 0):.2f}"
                )
                print(f"         Difference: ${test.get('difference', 0):.2f}")

            if "line_totals" in test:
                print(f"         Subtotal: ${test.get('subtotal', 0):.2f}")
                print(
                    f"         Line Totals: {[f'${lt:.2f}' for lt in test.get('line_totals', [])]}"
                )
                print(
                    f"         Calculated: ${test.get('calculated_subtotal', 0):.2f}"
                )
                print(f"         Difference: ${test.get('difference', 0):.2f}")

    # LLM reasoning
    llm_reasoning = result.get("llm_reasoning", {})
    print(f"\n🤖 LLM Reasoning:")
    print(f"   - Currency: {result.get('currency', 'Unknown')}")
    print(f"   - Confidence: {llm_reasoning.get('confidence', 'Unknown')}")

    if llm_reasoning.get("structure_analysis"):
        print(f"\n   Structure Analysis:")
        print(f"   {llm_reasoning['structure_analysis'][:200]}...")

    if llm_reasoning.get("final_assessment"):
        print(f"\n   Final Assessment:")
        print(f"   {llm_reasoning['final_assessment'][:200]}...")

    # Error handling
    if result.get("error"):
        print(f"\n❌ Error: {result['error']}")

    # Summary
    summary = result.get("summary", {})
    if summary:
        print(f"\n📈 Summary:")
        print(
            f"   - Total financial values: {summary.get('total_financial_values', 0)}"
        )
        print(
            f"   - Financial types identified: {', '.join(summary.get('financial_types_identified', []))}"
        )
        print(
            f"   - Average confidence: {summary.get('avg_confidence', 0.0):.2f}"
        )
        print(
            f"   - Mathematical validity: {summary.get('mathematical_validity', 'unknown')}"
        )

    print(f"\n🎯 Context for Label Assignment:")
    print(
        f"   This rich financial context can now be used by label sub-agents"
    )
    print(
        f"   to make informed decisions about assigning financial labels to words."
    )
    print(
        f"   The LLM has identified candidates and validated mathematical relationships"
    )
    print(f"   without relying on hard-coded rules or existing labels.")

    print("\n" + "=" * 60)
    print("✅ LLM-Driven Financial Discovery Test Complete")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_llm_driven_financial_discovery())
