#!/usr/bin/env python3
"""Simple test runner to run tests without pytest infrastructure issues."""

import sys
import os
import importlib.util

# Add the lambdas directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'infra', 'chromadb_compaction', 'lambdas'))

def run_test_file(test_file_path):
    """Run a single test file."""
    print(f"\n🧪 Running {test_file_path}")
    
    try:
        # Load the test module
        spec = importlib.util.spec_from_file_location("test_module", test_file_path)
        test_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(test_module)
        
        # Find and run test functions
        test_functions = []
        for name in dir(test_module):
            if name.startswith('test_'):
                test_functions.append(getattr(test_module, name))
        
        print(f"Found {len(test_functions)} test functions")
        
        passed = 0
        failed = 0
        
        for test_func in test_functions:
            try:
                print(f"  Running {test_func.__name__}...")
                test_func()
                print(f"  ✅ {test_func.__name__} passed")
                passed += 1
            except Exception as e:
                print(f"  ❌ {test_func.__name__} failed: {e}")
                failed += 1
        
        print(f"  Results: {passed} passed, {failed} failed")
        return passed, failed
        
    except Exception as e:
        print(f"  ❌ Failed to load test file: {e}")
        return 0, 1

def main():
    """Run all tests."""
    print("🚀 Running ChromaDB Compaction Tests")
    
    # Test files to run
    test_files = [
        "infra/chromadb_compaction/tests/unit/test_dataclasses.py",
        "infra/chromadb_compaction/tests/unit/test_parsing.py",
        "infra/chromadb_compaction/tests/unit/test_change_detection.py",
        "infra/chromadb_compaction/tests/unit/test_routing_logic.py",
        "infra/chromadb_compaction/tests/test_enhanced_compaction_unit.py",
        "infra/chromadb_compaction/tests/test_smoke.py",
    ]
    
    total_passed = 0
    total_failed = 0
    
    for test_file in test_files:
        if os.path.exists(test_file):
            passed, failed = run_test_file(test_file)
            total_passed += passed
            total_failed += failed
        else:
            print(f"⚠️  Test file not found: {test_file}")
    
    print(f"\n📊 Final Results:")
    print(f"  Total passed: {total_passed}")
    print(f"  Total failed: {total_failed}")
    
    if total_failed == 0:
        print("🎉 All tests passed!")
        return 0
    else:
        print("❌ Some tests failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
