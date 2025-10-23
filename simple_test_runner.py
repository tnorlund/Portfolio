#!/usr/bin/env python3
"""Test runner that works in both Lambda and test environments."""

import sys
import os

def setup_imports():
    """Setup imports to work in both Lambda and test environments."""
    # Add the lambdas directory to the path
    lambdas_path = os.path.join(os.path.dirname(__file__), 'infra', 'chromadb_compaction', 'lambdas')
    if os.path.exists(lambdas_path):
        sys.path.insert(0, lambdas_path)
    
    # Also try the current directory approach
    current_lambdas = os.path.join(os.getcwd(), 'lambdas')
    if os.path.exists(current_lambdas):
        sys.path.insert(0, current_lambdas)

def test_imports():
    """Test that all imports work."""
    print("🧪 Testing imports...")
    
    try:
        # Test stream processor imports
        from stream_processor import LambdaResponse, FieldChange, ParsedStreamRecord
        print("✅ Stream processor imports successful")
        
        # Test compaction imports
        from compaction.models import LambdaResponse as CompactionLambdaResponse, StreamMessage
        from compaction.operations import update_receipt_metadata, update_word_labels
        print("✅ Compaction imports successful")
        
        return True
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        return False

def test_functionality():
    """Test basic functionality."""
    print("🧪 Testing functionality...")
    
    try:
        from stream_processor import LambdaResponse, FieldChange, ParsedStreamRecord
        from compaction.models import LambdaResponse as CompactionLambdaResponse, StreamMessage
        
        # Test stream processor
        stream_response = LambdaResponse(200, 5, 3)
        print(f"✅ Stream LambdaResponse: {stream_response}")
        
        # Test compaction
        compaction_response = CompactionLambdaResponse(200, 'Test message', 5, 2, 3)
        print(f"✅ Compaction LambdaResponse: {compaction_response}")
        
        # Test FieldChange (correct usage)
        field_change = FieldChange(old='old_value', new='new_value')
        print(f"✅ FieldChange: {field_change}")
        
        return True
    except Exception as e:
        print(f"❌ Functionality test failed: {e}")
        return False

def run_simple_tests():
    """Run simple tests that don't require pytest."""
    print("🚀 Running Simple Tests")
    
    setup_imports()
    
    if not test_imports():
        return False
    
    if not test_functionality():
        return False
    
    print("🎉 All tests passed!")
    return True

if __name__ == "__main__":
    success = run_simple_tests()
    sys.exit(0 if success else 1)
