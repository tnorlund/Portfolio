#!/usr/bin/env python3
"""Test script to verify the tests work with the modular structure."""

import sys
import os

# Add the lambdas directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'infra', 'chromadb_compaction', 'lambdas'))

try:
    # Test stream processor imports
    from stream_processor import LambdaResponse, FieldChange, ParsedStreamRecord
    print("✅ Stream processor imports successful")
    
    # Test compaction imports
    from compaction.models import LambdaResponse as CompactionLambdaResponse, StreamMessage
    from compaction.operations import update_receipt_metadata, update_word_labels
    print("✅ Compaction imports successful")
    
    # Test that classes are properly defined
    print(f"✅ LambdaResponse from stream_processor: {LambdaResponse}")
    print(f"✅ LambdaResponse from compaction: {CompactionLambdaResponse}")
    print(f"✅ FieldChange: {FieldChange}")
    print(f"✅ ParsedStreamRecord: {ParsedStreamRecord}")
    print(f"✅ StreamMessage: {StreamMessage}")
    print(f"✅ update_receipt_metadata: {update_receipt_metadata}")
    print(f"✅ update_word_labels: {update_word_labels}")
    
    # Test creating instances
    stream_response = LambdaResponse(200, 5, 3)
    compaction_response = CompactionLambdaResponse(200, 5, 2, 3, "Test message")
    
    print(f"✅ Stream LambdaResponse created: {stream_response}")
    print(f"✅ Compaction LambdaResponse created: {compaction_response}")
    
    print("\n🎉 All imports and instantiations successful! The modular structure is working.")
    
except ImportError as e:
    print(f"❌ Import error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Unexpected error: {e}")
    sys.exit(1)
