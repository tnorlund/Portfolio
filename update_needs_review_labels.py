#!/usr/bin/env python3
"""
Update NEEDS_REVIEW Labels to INVALID
====================================

Script to manually update the 2 NEEDS_REVIEW GRAND_TOTAL labels to INVALID
with human-reviewed reasoning.
"""

from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus
from receipt_label.langchain_validation.validation_tool import ValidationResult
from receipt_label.langchain_validation.update_receipt_labels import update_label_with_validation_result

# Load environment
load_env()
pulumi_env = load_env()

def main():
    print("🔄 UPDATING NEEDS_REVIEW LABELS TO INVALID")
    print("=" * 50)
    
    # Initialize client
    dynamo_client = DynamoClient(pulumi_env.get("dynamodb_table_name"))
    
    # Get NEEDS_REVIEW labels
    all_labels, _ = dynamo_client.get_receipt_word_labels_by_label('GRAND_TOTAL', limit=None)
    review_labels = [l for l in all_labels if l.validation_status == ValidationStatus.NEEDS_REVIEW.value]
    
    print(f"Found {len(review_labels)} NEEDS_REVIEW labels")
    
    # Define the updates with custom reasoning
    updates = [
        {
            "image_id": "41885f70-0a10-4897-8840-8f7373d150cd",
            "reasoning": "The word 'TOTAL' on line 29 corresponds to the amount $3.03, but this appears to be a subtotal or section total rather than the grand total. The actual transaction amount is $66.70 as shown later in the receipt. For GRAND_TOTAL labeling, the word should point to the final amount the customer pays ($66.70), not an intermediate total. Therefore, this label is incorrect."
        },
        {
            "image_id": "4c5ba3ff-a7c1-49a3-bb91-4e6d75bf1ebb", 
            "reasoning": "The word 'Amount' on line 23 is a generic field header rather than a specific indicator of the grand total. Standard grand total terminology includes 'Total', 'Grand Total', 'Amount Due', or 'Balance Due'. A standalone 'Amount' heading is too ambiguous and could refer to any monetary value on the receipt. GRAND_TOTAL labels should be applied to clear, unambiguous indicators of the final transaction amount."
        }
    ]
    
    # Process each label
    for i, label in enumerate(review_labels):
        print(f"\n📋 Processing Label {i+1}: {label.image_id}")
        
        # Find matching update
        update_info = None
        for update in updates:
            if update["image_id"] == label.image_id:
                update_info = update
                break
        
        if not update_info:
            print(f"  ❌ No update info found for {label.image_id}")
            continue
        
        # Create validation result
        validation_result = ValidationResult(
            validation_status="INVALID",
            reasoning=update_info["reasoning"]
        )
        
        print(f"  🔄 Updating to INVALID...")
        print(f"  📝 Reasoning: {update_info['reasoning'][:100]}...")
        
        # Update the label
        updated_label = update_label_with_validation_result(
            label,
            validation_result,
            "human_review_manual_update"
        )
        
        # Save to database
        try:
            dynamo_client.update_receipt_word_label(updated_label)
            print(f"  ✅ Successfully updated!")
        except Exception as e:
            print(f"  ❌ Failed to update: {e}")
    
    print(f"\n🎉 Update complete!")
    
    # Verify final status
    print(f"\n📊 Verifying final status...")
    final_review_labels = [l for l in dynamo_client.get_receipt_word_labels_by_label('GRAND_TOTAL', limit=None)[0] 
                          if l.validation_status == ValidationStatus.NEEDS_REVIEW.value]
    
    print(f"Remaining NEEDS_REVIEW labels: {len(final_review_labels)}")
    
    if len(final_review_labels) == 0:
        print("🎉 All NEEDS_REVIEW labels have been resolved!")

if __name__ == "__main__":
    main()