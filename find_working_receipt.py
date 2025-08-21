#!/usr/bin/env python3
"""
Find receipts that work with the DynamoDB methods (don't have data quality issues).
"""

import os
from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env

def find_working_receipt():
    """Find receipts that work with both list_receipt_words_from_receipt and list_receipt_lines_from_receipt."""
    
    # Load environment
    pulumi_env = load_env()
    table_name = os.environ.get("DYNAMODB_TABLE_NAME") or pulumi_env.get("dynamodb_table_name")
    dynamo_client = DynamoClient(table_name)
    
    print(f"🔍 Finding receipts that work with both DynamoDB methods...")
    
    # Test more receipts
    receipts, _ = dynamo_client.list_receipts(limit=50)
    
    working_receipts = []
    
    for receipt in receipts:
        try:
            # Test both methods
            words = dynamo_client.list_receipt_words_from_receipt(receipt.image_id, receipt.receipt_id)
            lines = dynamo_client.list_receipt_lines_from_receipt(receipt.receipt_id, receipt.image_id)
            
            # Only consider it "working" if it has actual data
            if len(words) > 0 and len(lines) > 0:
                working_receipts.append({
                    'image_id': receipt.image_id,
                    'receipt_id': receipt.receipt_id,
                    'words': len(words),
                    'lines': len(lines)
                })
                
                print(f"✅ Found working receipt:")
                print(f"   📝 image_id: {receipt.image_id}")
                print(f"   📝 receipt_id: {receipt.receipt_id}")
                print(f"   📊 Words: {len(words)}")
                print(f"   📊 Lines: {len(lines)}")
                
                # Show sample ChromaDB IDs that would be generated
                if words:
                    sample_word = words[0]
                    word_chroma_id = f"IMAGE#{sample_word.image_id}#RECEIPT#{sample_word.receipt_id:05d}#LINE#{sample_word.line_id:05d}#WORD#{sample_word.word_id:05d}"
                    print(f"   🔗 Sample word ChromaDB ID: {word_chroma_id}")
                    
                if lines:
                    sample_line = lines[0]
                    line_chroma_id = f"IMAGE#{sample_line.image_id}#RECEIPT#{sample_line.receipt_id:05d}#LINE#{sample_line.line_id:05d}"
                    print(f"   🔗 Sample line ChromaDB ID: {line_chroma_id}")
                    
                print(f"   💡 Lambda test command:")
                print(f"   UPDATE metadata for image_id='{receipt.image_id}' receipt_id={receipt.receipt_id}")
                print("")
                
                # Stop after finding 3 working receipts
                if len(working_receipts) >= 3:
                    break
                    
        except Exception as e:
            # Skip receipts with data quality issues
            continue
    
    if working_receipts:
        print(f"✅ Summary: Found {len(working_receipts)} working receipts")
        print(f"🎯 The enhanced compaction handler Lambda should work correctly with these receipts!")
        
        # Show JSON format for easy testing
        print(f"\n📋 Test data for Lambda:")
        for i, receipt in enumerate(working_receipts):
            print(f"Receipt {i+1}:")
            print(f"  image_id: {receipt['image_id']}")
            print(f"  receipt_id: {receipt['receipt_id']}")
            print(f"  Expected: {receipt['words']} words, {receipt['lines']} lines")
    else:
        print(f"⚠️ No working receipts found in first 50 receipts")
        print(f"   This suggests widespread data quality issues in the database")

if __name__ == "__main__":
    find_working_receipt()