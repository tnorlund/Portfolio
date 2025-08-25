#!/usr/bin/env python3
"""
Find labels that still need validation (NONE status)
"""

from dotenv import load_dotenv
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus
from receipt_label.constants import CORE_LABELS

load_dotenv()
pulumi_env = load_env()

def find_unvalidated_labels():
    print("🔍 FINDING LABELS THAT STILL NEED VALIDATION")
    print("=" * 50)
    
    dynamo_client = DynamoClient(pulumi_env.get("dynamodb_table_name"))
    
    none_status_counts = {}
    
    # Check each core label type
    for label_type in CORE_LABELS.keys():
        try:
            # Get labels with NONE status for this type
            labels, _ = dynamo_client.get_receipt_word_labels_by_validation_status_and_label(
                status=ValidationStatus.NONE,
                label=label_type,
                limit=100
            )
            
            if len(labels) > 0:
                none_status_counts[label_type] = len(labels)
                print(f"📋 {label_type}: {len(labels)} NONE status labels")
                
                # Show a sample
                if len(labels) > 0:
                    sample = labels[0]
                    text = getattr(sample, 'text', getattr(sample, 'word_text', 'N/A'))
                    print(f"   Sample: '{text}' from image {sample.image_id[:8]}...")
            
        except Exception as e:
            print(f"❌ Error checking {label_type}: {e}")
    
    print(f"\n📊 SUMMARY:")
    if none_status_counts:
        total_unvalidated = sum(none_status_counts.values())
        print(f"   Total labels needing validation: {total_unvalidated}")
        print(f"   Label types needing work: {len(none_status_counts)}")
        
        # Sort by count
        sorted_labels = sorted(none_status_counts.items(), key=lambda x: x[1], reverse=True)
        print(f"\n🎯 TOP CANDIDATES FOR VALIDATION:")
        for label_type, count in sorted_labels[:5]:
            definition = CORE_LABELS.get(label_type, "No definition")
            print(f"   {label_type}: {count} labels")
            print(f"      Definition: {definition[:80]}...")
            
    else:
        print("   🎉 ALL CORE LABELS HAVE BEEN VALIDATED!")
        print("   No labels with NONE status found")
    
    return none_status_counts

if __name__ == "__main__":
    find_unvalidated_labels()