#!/usr/bin/env python3
"""
Simplified Costco receipt analyzer using modular components.
Extracted from the original 1,269-line costco_label_discovery.py script.
"""

import asyncio
import logging
import os
from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_label.prompt_formatting import format_receipt_lines_visual_order

# Import our modular components
from receipt_label.costco_models import ReceiptAnalysis
from receipt_label.text_reconstruction import ReceiptTextReconstructor
from receipt_label.llm_classifier import analyze_with_ollama
from receipt_label.validator import validate_arithmetic_relationships

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def analyze_costco_receipt(
    client: DynamoClient, 
    image_id: str, 
    receipt_id: int, 
    known_total: float
) -> ReceiptAnalysis:
    """Analyze a single COSTCO receipt to discover labels - main orchestration function."""
    
    receipt_identifier = f"{image_id}/{receipt_id}"
    print(f"\n🔍 ANALYZING COSTCO RECEIPT: {receipt_identifier}")
    print(f"   Known GRAND_TOTAL: ${known_total:.2f}")
    print("-" * 60)
    
    # Step 1: Get receipt data from DynamoDB
    lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
    print(f"📊 Found {len(lines)} lines")
    
    # Step 2: Reconstruct readable text using our modular component
    reconstructor = ReceiptTextReconstructor()
    formatted_text, text_groups = reconstructor.reconstruct_receipt(lines)
    
    # Use the existing prompt formatting for consistency
    formatted_receipt_text = format_receipt_lines_visual_order(lines)
    
    # Extract currency contexts for LLM analysis
    currency_contexts = reconstructor.extract_currency_context(text_groups)
    print(f"💰 Found {len(currency_contexts)} currency amounts in text")
    
    # Step 3: Classify currencies using LLM
    discovered_labels = await analyze_with_ollama(
        formatted_receipt_text, 
        currency_contexts, 
        known_total, 
        receipt_identifier
    )
    
    # Step 4: Validate arithmetic relationships
    validation_results = validate_arithmetic_relationships(discovered_labels, known_total)
    
    # Step 5: Calculate overall confidence score
    confidence_score = (
        sum(label.confidence for label in discovered_labels) / len(discovered_labels)
        if discovered_labels else 0.0
    )
    
    # Return structured analysis result
    return ReceiptAnalysis(
        receipt_id=receipt_identifier,
        known_total=known_total,
        discovered_labels=discovered_labels,
        validation_results=validation_results,
        total_lines=len(lines),
        confidence_score=confidence_score,
        formatted_text=formatted_receipt_text
    )


def setup_langsmith_tracing():
    """Setup LangSmith tracing for LLM interactions."""
    langchain_api_key = os.getenv("LANGCHAIN_API_KEY")
    
    if langchain_api_key and langchain_api_key.strip():
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = "costco-label-discovery"
        print("✅ LangSmith tracing enabled")
        print(f"   Project: costco-label-discovery")
        print(f"   View traces at: https://smith.langchain.com/")
    else:
        print("⚠️ LANGCHAIN_API_KEY not set - tracing disabled")


def display_analysis_summary(results: list[ReceiptAnalysis]):
    """Display a summary of analysis results."""
    print("\n" + "=" * 80)
    print("📋 COSTCO LABEL DISCOVERY SUMMARY")
    print("=" * 80)
    
    total_labels = sum(len(r.discovered_labels) for r in results)
    avg_confidence = (
        sum(r.confidence_score for r in results) / len(results)
        if results else 0.0
    )
    
    print(f"Receipts analyzed: {len(results)}")
    print(f"Total labels discovered: {total_labels}")
    print(f"Average confidence: {avg_confidence:.2f}")
    print()
    
    # Display results for each receipt
    for i, result in enumerate(results, 1):
        print(f"Receipt {i}: {result.receipt_id}")
        print(f"  Known total: ${result.known_total:.2f}")
        print(f"  Labels found: {len(result.discovered_labels)}")
        print(f"  Confidence: {result.confidence_score:.2f}")
        
        # Group labels by type for display
        labels_by_type = {"GRAND_TOTAL": [], "TAX": [], "SUBTOTAL": [], "LINE_TOTAL": []}
        for label in result.discovered_labels:
            labels_by_type[label.label_type.value].append(label)
        
        for label_type, labels in labels_by_type.items():
            if labels:
                values = [f"${label.value:.2f}" for label in labels]
                print(f"    {label_type}: {', '.join(values)}")
        
        # Show validation status
        validation = result.validation_results
        if validation.get("line_totals_plus_tax_equals_grand_total"):
            print(f"    ✅ Arithmetic validation passed")
        elif validation.get("grand_total_matches_known"):
            print(f"    ✅ Grand total matches known value")
        else:
            print(f"    ⚠️ Arithmetic validation issues")
        
        print()


async def main():
    """Main entry point - analyze COSTCO receipts to discover labels."""
    
    print("🏪 COSTCO LABEL DISCOVERY USING LANGCHAIN")
    print("=" * 80)
    print("Discovering GRAND_TOTAL, TAX, LINE_TOTAL, SUBTOTAL from raw OCR text")
    print()
    
    # Setup LangSmith tracing
    setup_langsmith_tracing()
    print()
    
    # Initialize DynamoDB client
    env_vars = load_env()
    client = DynamoClient(env_vars.get("dynamodb_table_name"))
    
    # COSTCO test receipts with known grand totals
    costco_receipts = [
        ("6cd1f7f5-d988-4e11-9209-cb6535fc3b04", 1, 198.93),
        ("314ac65b-2b97-45d6-81c2-e48fb0b8cef4", 1, 87.71),
        ("a861f6a6-8d6d-42bc-907c-3330d8bd2022", 1, 35.68),
        ("8388d1f1-b5d6-4560-b7dc-db273815dda1", 1, 37.66),
    ]
    
    results = []
    
    # Analyze each receipt using our modular components
    for image_id, receipt_id, known_total in costco_receipts:
        try:
            result = await analyze_costco_receipt(client, image_id, receipt_id, known_total)
            results.append(result)
        except Exception as e:
            logger.error(f"Error analyzing {image_id}/{receipt_id}: {e}")
            continue
    
    # Display final summary
    display_analysis_summary(results)
    
    print("🎉 Analysis complete!")


if __name__ == "__main__":
    asyncio.run(main())