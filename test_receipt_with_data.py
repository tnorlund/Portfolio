#!/usr/bin/env python3
"""
Find and test a receipt that actually has words and lines data.
"""

import os
from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env

def find_receipt_with_data():
    """Find a receipt that has actual words and lines to test the Lambda functionality."""
    
    # Load environment
    pulumi_env = load_env()
    table_name = os.environ.get("DYNAMODB_TABLE_NAME") or pulumi_env.get("dynamodb_table_name")
    dynamo_client = DynamoClient(table_name)
    
    print(f"🔍 Finding receipt with actual data...")
    
    # Get more receipts to test
    receipts, _ = dynamo_client.list_receipts(limit=20)
    
    for receipt in receipts:
        try:
            receipt_details = dynamo_client.get_receipt_details(receipt.image_id, receipt.receipt_id)
            
            if len(receipt_details.words) > 0 or len(receipt_details.lines) > 0:
                print(f"\n✅ Found receipt with data!")
                print(f"   📝 image_id: {receipt.image_id}")
                print(f"   📝 receipt_id: {receipt.receipt_id}")
                print(f"   📊 Words: {len(receipt_details.words)}")
                print(f"   📊 Lines: {len(receipt_details.lines)}")
                
                # Test the exact DynamoDB methods used by Lambda
                words = dynamo_client.list_receipt_words_from_receipt(receipt.image_id, receipt.receipt_id)
                lines = dynamo_client.list_receipt_lines_from_receipt(receipt.receipt_id, receipt.image_id)
                
                print(f"   🔍 Method results:")
                print(f"      list_receipt_words_from_receipt: {len(words)} words")
                print(f"      list_receipt_lines_from_receipt: {len(lines)} lines")
                
                if words:
                    print(f"   📄 Sample words:")
                    for word in words[:3]:
                        chroma_id = f"IMAGE#{word.image_id}#RECEIPT#{word.receipt_id:05d}#LINE#{word.line_id:05d}#WORD#{word.word_id:05d}"
                        print(f"      '{word.text}' -> {chroma_id}")
                        
                if lines:
                    print(f"   📄 Sample lines:")
                    for line in lines[:3]:
                        chroma_id = f"IMAGE#{line.image_id}#RECEIPT#{line.receipt_id:05d}#LINE#{line.line_id:05d}"
                        print(f"      Line {line.line_id}: '{line.text}' -> {chroma_id}")
                
                # This is the receipt we should test with!
                print(f"\n💡 Use this receipt for Lambda testing:")
                print(f"   image_id='{receipt.image_id}'")
                print(f"   receipt_id={receipt.receipt_id}")
                return
                
        except Exception as e:
            print(f"   ❌ Error testing receipt {receipt.image_id[:8]}...: {e}")
            continue
    
    print("⚠️ No receipts with data found in first 20 receipts")

if __name__ == "__main__":
    find_receipt_with_data()