#!/usr/bin/env python3
"""
Show Current Prompt Format
==========================

Simple script to show the exact prompt being generated.
"""

import json
from receipt_label.constants import CORE_LABELS

# Mock data to demonstrate prompt format
receipt_metadata = {
    "merchant_name": "Vons",
    "merchant_category": "Grocery", 
    "address": "2725 Agoura Rd, Thousand Oaks, CA 91361, USA",
    "phone_number": "(805) 497-1921",
    "validation_status": "MATCHED",
    "matched_fields": ["address", "phone"]
}

targets = [{
    "image_id": "abc123",
    "receipt_id": 1,
    "line_id": 10,
    "word_id": 2,
    "word_text": "4.99",
    "label": "GRAND_TOTAL"
}]

receipt_text = """10: TOTAL 4.99
11: TAX 0.50
12: GRAND TOTAL 5.49"""

def generate_sample_prompt():
    """Generate a sample prompt to show the current format"""
    
    # Schema for validate_labels function (matching proven format)
    schema = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "is_valid": {"type": "boolean"},
                        "correct_label": {
                            "type": "string",
                            "enum": list(CORE_LABELS.keys()),
                        },
                    },
                    "required": ["id", "is_valid"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
    }
    
    # Build targets in proven format
    formatted_targets = []
    for target in targets:
        # Create unique ID matching proven format
        target_id = (
            f"IMAGE#{target['image_id']}#"
            f"RECEIPT#{target['receipt_id']:05d}#" 
            f"LINE#{target['line_id']:05d}#"
            f"WORD#{target['word_id']:05d}#"
            f"LABEL#{target['label']}#"
            f"VALIDATION_STATUS#NONE"
        )
        formatted_targets.append({
            "id": target_id,
            "text": target['word_text'],
            "line_id": target['line_id'],
            "proposed_label": target['label'],
        })

    prompt_lines = []
    prompt_lines.append("### Schema")
    prompt_lines.append("```json")
    prompt_lines.append(json.dumps(schema, indent=2))
    prompt_lines.append("```")
    prompt_lines.append("")
    
    prompt_lines.append("### Task")
    prompt_lines.append(
        f'Validate these labels on one "{receipt_metadata["merchant_name"]}" receipt. '
        "Return **only** a call to the **`validate_labels`** function with a "
        "`results` array, and do NOT renumber the `id` field.\n"
    )
    
    # Add merchant information in proven format
    prompt_lines.append("### Merchant Information")
    prompt_lines.append(f"🏪 Merchant: {receipt_metadata['merchant_name']}")
    if receipt_metadata.get('merchant_category'):
        prompt_lines.append(f"   Category: {receipt_metadata['merchant_category']}")
    if receipt_metadata.get('address'):
        prompt_lines.append(f"   Address: {receipt_metadata['address']}")
    if receipt_metadata.get('phone_number'):
        prompt_lines.append(f"   Phone: {receipt_metadata['phone_number']}")
    if receipt_metadata.get('validation_status'):
        prompt_lines.append(f"   Validation Status: {receipt_metadata['validation_status']}")
    if receipt_metadata.get('matched_fields'):
        prompt_lines.append(f"   Matched Fields: {', '.join(receipt_metadata['matched_fields'])}")
    prompt_lines.append("")
    
    prompt_lines.append("Each result object must include:")
    prompt_lines.append('- `"id"`: the original label identifier')
    prompt_lines.append('- `"is_valid"`: true or false')
    prompt_lines.append('- `"correct_label"`: (only if `"is_valid": false`)')
    
    prompt_lines.append("### Examples (for guidance only)")
    prompt_lines.append(
        'SPROUTS → {"results":[{"id":"IMAGE#abc123#RECEIPT#00001#LINE#'
        '00003#WORD#00001#LABEL#MERCHANT_NAME#VALIDATION_STATUS#NONE",'
        '"is_valid":true}]}'
    )
    prompt_lines.append(
        '4.99 → {"results":[{"id":"IMAGE#abc123#RECEIPT#00001#LINE#00010#'
        'WORD#00002#LABEL#LINE_TOTAL#VALIDATION_STATUS#NONE","is_valid":'
        'false,"correct_label":"UNIT_PRICE"}]}'
    )
    
    prompt_lines.append("### Allowed labels")
    prompt_lines.append(", ".join(CORE_LABELS.keys()))
    prompt_lines.append(
        "Only labels from the above list are valid; do NOT propose any other "
        "label."
    )
    prompt_lines.append("")
    
    prompt_lines.append("### Targets")
    prompt_lines.append(json.dumps(formatted_targets))
    prompt_lines.append("")

    prompt_lines.append("### Receipt")
    prompt_lines.append(
        "Below is the receipt for context. Each line determines the range of "
        "line IDs\n"
    )
    prompt_lines.append("---")
    prompt_lines.append(receipt_text)
    prompt_lines.append("---")
    prompt_lines.append("### END PROMPT — reply with JSON only ###")

    return "\n".join(prompt_lines)

if __name__ == "__main__":
    print("🤖 Current Validation Prompt Format:")
    print("=" * 80)
    print(generate_sample_prompt())
    print("=" * 80)
    print("✅ This is the proven format now being used by LangChain validation!")