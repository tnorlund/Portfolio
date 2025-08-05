#!/usr/bin/env python3
"""Fix remaining import references after naming convention changes"""

import re
from pathlib import Path


def fix_imports_in_file(filepath: Path) -> int:
    """Fix import references in a single file"""
    with open(filepath, "r") as f:
        content = f.read()

    original_content = content

    # Map of old names to new names
    replacements = {
        "itemToWordTag": "item_to_word_tag",
        "itemToWord": "item_to_word",
        "itemToJob": "item_to_job",
        "itemToJobStatus": "item_to_job_status",
        "itemToInstance": "item_to_instance",
        "itemToInstanceJob": "item_to_instance_job",
        "itemToLabelCountCache": "item_to_label_count_cache",
        "itemToPlacesCache": "item_to_places_cache",
        "itemToReceiptValidationCategory": "item_to_receipt_validation_category",
        "itemToReceiptLabelAnalysis": "item_to_receipt_label_analysis",
        "itemToBatchSummary": "item_to_batch_summary",
        "itemToReceiptStructureAnalysis": "item_to_receipt_structure_analysis",
        "itemToJobMetric": "item_to_job_metric",
        "itemToOCRJob": "item_to_ocr_job",
        "itemToLine": "item_to_line",
        "itemToReceiptWordLabel": "item_to_receipt_word_label",
        "itemToJobDependency": "item_to_job_dependency",
        "itemToOCRRoutingDecision": "item_to_ocr_routing_decision",
        "itemToJobLog": "item_to_job_log",
        "itemToReceiptChatGPTValidation": "item_to_receipt_chat_gpt_validation",
        "itemToReceiptValidationSummary": "item_to_receipt_validation_summary",
        "itemToReceiptWord": "item_to_receipt_word",
        "itemToQueueJob": "item_to_queue_job",
        "itemToReceiptValidationResult": "item_to_receipt_validation_result",
        "itemToReceiptLetter": "item_to_receipt_letter",
        "itemToLabelHygieneResult": "item_to_label_hygiene_result",
        "itemToReceiptSection": "item_to_receipt_section",
        "itemToLetter": "item_to_letter",
        "itemToReceipt": "item_to_receipt",
        "itemToReceiptLineItemAnalysis": "item_to_receipt_line_item_analysis",
        "itemToReceiptLine": "item_to_receipt_line",
        "itemToJobCheckpoint": "item_to_job_checkpoint",
        "itemToQueue": "item_to_queue",
        "itemToJobResource": "item_to_job_resource",
        "itemToReceiptField": "item_to_receipt_field",
        "itemToReceiptMetadata": "item_to_receipt_metadata",
        "itemToImage": "item_to_image",
        "itemToEmbeddingBatchResult": "item_to_embedding_batch_result",
        "itemToReceiptWordTag": "item_to_receipt_word_tag",
        "itemToCompletionBatchResult": "item_to_completion_batch_result",
        "itemToLabelMetadata": "item_to_label_metadata",
    }

    changes = 0
    for old_name, new_name in replacements.items():
        if old_name in content:
            content = re.sub(rf"\b{old_name}\b", new_name, content)
            changes += 1

    # Write back if changed
    if content != original_content:
        with open(filepath, "w") as f:
            f.write(content)
        print(f"Fixed {filepath.name}: {changes} import references")

    return changes


def main():
    """Fix all import references"""
    receipt_dynamo_path = Path("/Users/tnorlund/GitHub/example/receipt_dynamo")

    total_fixes = 0
    files_fixed = 0

    # Process all Python files
    for filepath in receipt_dynamo_path.rglob("*.py"):
        if "__pycache__" in str(filepath):
            continue

        fixes = fix_imports_in_file(filepath)
        if fixes > 0:
            files_fixed += 1
            total_fixes += fixes

    print(
        f"\nTotal: Fixed {total_fixes} import references in {files_fixed} files"
    )


if __name__ == "__main__":
    main()
