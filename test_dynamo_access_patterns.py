#!/usr/bin/env python3
"""
Test script to verify DynamoDB access patterns used by enhanced compaction handler.

This script tests the exact same methods and parameters that the Lambda uses:
- list_receipt_words_from_receipt(image_id, receipt_id)
- list_receipt_lines_from_receipt(receipt_id, image_id)

Using the same test receipt: image_id=7e2bd911-7afb-4e0a-84de-57f51ce4daff, receipt_id=1
"""

import os
import sys
from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env

def test_dynamo_access_patterns():
    """Test the exact DynamoDB access patterns used by the enhanced compaction handler."""
    
    # Load environment like the Lambda does
    pulumi_env = load_env()
    if not pulumi_env:
        print("❌ Failed to load Pulumi environment")
        sys.exit(1)
    
    # Initialize DynamoDB client
    table_name = os.environ.get("DYNAMODB_TABLE_NAME") or pulumi_env.get("dynamodb_table_name")
    if not table_name:
        print("❌ No DynamoDB table name found")
        sys.exit(1)
    
    print(f"🔍 Testing DynamoDB access patterns")
    print(f"📊 Table: {table_name}")
    
    dynamo_client = DynamoClient(table_name)
    
    # Test data from the Lambda logs
    image_id = "7e2bd911-7afb-4e0a-84de-57f51ce4daff"
    receipt_id = 1
    
    print(f"\n📝 Testing receipt: image_id={image_id[:8]}..., receipt_id={receipt_id}")
    
    # Test 1: Query words using the exact method the Lambda uses
    print(f"\n🔍 TEST 1: list_receipt_words_from_receipt(image_id, receipt_id)")
    try:
        words = dynamo_client.list_receipt_words_from_receipt(image_id, receipt_id)
        print(f"✅ Words found: {len(words)}")
        
        if words:
            print(f"   📄 Sample words:")
            for i, word in enumerate(words[:5]):  # Show first 5 words
                chroma_id = f"IMAGE#{word.image_id}#RECEIPT#{word.receipt_id:05d}#LINE#{word.line_id:05d}#WORD#{word.word_id:05d}"
                print(f"     {i+1}. '{word.text}' -> {chroma_id}")
            if len(words) > 5:
                print(f"     ... and {len(words) - 5} more words")
        else:
            print(f"   ⚠️  No words found for this receipt")
            
    except Exception as e:
        print(f"❌ Error querying words: {e}")
        print(f"   Exception type: {type(e).__name__}")
        
    # Test 2: Query lines using the exact method the Lambda uses  
    print(f"\n🔍 TEST 2: list_receipt_lines_from_receipt(receipt_id, image_id)")
    try:
        lines = dynamo_client.list_receipt_lines_from_receipt(receipt_id, image_id)
        print(f"✅ Lines found: {len(lines)}")
        
        if lines:
            print(f"   📄 Sample lines:")
            for i, line in enumerate(lines[:5]):  # Show first 5 lines
                chroma_id = f"IMAGE#{line.image_id}#RECEIPT#{line.receipt_id:05d}#LINE#{line.line_id:05d}"
                print(f"     {i+1}. Line {line.line_id}: '{line.text}' -> {chroma_id}")
            if len(lines) > 5:
                print(f"     ... and {len(lines) - 5} more lines")
        else:
            print(f"   ⚠️  No lines found for this receipt")
            
    except Exception as e:
        print(f"❌ Error querying lines: {e}")
        print(f"   Exception type: {type(e).__name__}")
        
    # Test 3: Check if receipt exists at all
    print(f"\n🔍 TEST 3: Check if receipt exists")
    try:
        receipt_details = dynamo_client.get_receipt_details(image_id, receipt_id)
        print(f"✅ Receipt exists!")
        print(f"   📊 Words: {len(receipt_details.words)}")
        print(f"   📊 Lines: {len(receipt_details.lines)}")
        print(f"   📊 Labels: {len(receipt_details.labels)}")
        
        if receipt_details.words:
            print(f"   📝 First few words: {[w.text for w in receipt_details.words[:10]]}")
            
    except Exception as e:
        print(f"❌ Error getting receipt details: {e}")
        print(f"   Exception type: {type(e).__name__}")
        
    # Test 4: List some receipts to find a valid one
    print(f"\n🔍 TEST 4: Find valid receipts for testing")
    try:
        receipts, _ = dynamo_client.list_receipts(limit=5)
        print(f"✅ Found {len(receipts)} receipts")
        
        for i, receipt in enumerate(receipts):
            print(f"   {i+1}. image_id={receipt.image_id[:8]}..., receipt_id={receipt.receipt_id}")
            
            # Try to get words/lines for first receipt
            if i == 0:
                try:
                    test_words = dynamo_client.list_receipt_words_from_receipt(receipt.image_id, receipt.receipt_id)
                    test_lines = dynamo_client.list_receipt_lines_from_receipt(receipt.receipt_id, receipt.image_id)
                    print(f"      📊 Words: {len(test_words)}, Lines: {len(test_lines)}")
                except Exception as e:
                    print(f"      ❌ Error testing receipt: {e}")
                    
    except Exception as e:
        print(f"❌ Error listing receipts: {e}")
        
    print(f"\n✅ DynamoDB access pattern test complete")
    
    
if __name__ == "__main__":
    test_dynamo_access_patterns()