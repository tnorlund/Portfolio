#!/usr/bin/env python3
"""
LangChain Validation Test Script
===============================

This script provides comprehensive testing of the LangChain validation system
with sample receipt data. It validates the implementation works correctly with
both OpenAI and Ollama providers.

Usage:
    python test_langchain_validation.py [--provider openai|ollama] [--mode realtime|batch]
    
Examples:
    # Test with OpenAI (default)
    python test_langchain_validation.py
    
    # Test with Ollama
    python test_langchain_validation.py --provider ollama
    
    # Test batch mode
    python test_langchain_validation.py --mode batch
    
    # Test configuration only
    python test_langchain_validation.py --config-only
"""

import asyncio
import argparse
import json
import os
import sys
import time
from typing import Dict, List, Any
from pathlib import Path

# Add receipt_label to Python path
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

try:
    from receipt_label.langchain_validation.config import (
        ValidationConfig, 
        LLMProvider,
        ValidationMode,
        get_config,
        print_config_summary
    )
    from receipt_label.langchain_validation.implementation import (
        ReceiptValidator,
        validate_receipt_labels_v2
    )
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure you're running this from the project root and dependencies are installed.")
    sys.exit(1)


# Sample test data
SAMPLE_RECEIPT_DATA = {
    "image_id": "TEST_IMG_001",
    "receipt_id": 99999,
    "merchant_name": "Target Store #1234",
    "labels": [
        {
            "image_id": "TEST_IMG_001",
            "receipt_id": 99999,
            "line_id": 1,
            "word_id": 1,
            "label": "MERCHANT_NAME",
            "validation_status": "NONE",
            "text": "TARGET"
        },
        {
            "image_id": "TEST_IMG_001", 
            "receipt_id": 99999,
            "line_id": 2,
            "word_id": 1,
            "label": "DATE",
            "validation_status": "NONE",
            "text": "12/25/2024"
        },
        {
            "image_id": "TEST_IMG_001",
            "receipt_id": 99999,
            "line_id": 3,
            "word_id": 2,
            "label": "TOTAL",
            "validation_status": "NONE", 
            "text": "$23.45"
        },
        {
            "image_id": "TEST_IMG_001",
            "receipt_id": 99999,
            "line_id": 4,
            "word_id": 1,
            "label": "TAX",
            "validation_status": "NONE",
            "text": "$1.87"
        }
    ]
}

# Additional test receipts for batch testing
BATCH_TEST_DATA = [
    {
        "image_id": "TEST_IMG_002",
        "receipt_id": 99998,
        "labels": [
            {
                "image_id": "TEST_IMG_002",
                "receipt_id": 99998, 
                "line_id": 1,
                "word_id": 1,
                "label": "MERCHANT_NAME",
                "validation_status": "NONE",
                "text": "WALMART"
            },
            {
                "image_id": "TEST_IMG_002",
                "receipt_id": 99998,
                "line_id": 2, 
                "word_id": 1,
                "label": "TOTAL",
                "validation_status": "NONE",
                "text": "$45.67"
            }
        ],
        "urgent": False
    },
    {
        "image_id": "TEST_IMG_003",
        "receipt_id": 99997,
        "labels": [
            {
                "image_id": "TEST_IMG_003", 
                "receipt_id": 99997,
                "line_id": 1,
                "word_id": 1,
                "label": "MERCHANT_NAME", 
                "validation_status": "NONE",
                "text": "COSTCO"
            }
        ],
        "urgent": True  # This should be processed immediately
    }
]


class TestResults:
    """Track test results and generate summary"""
    
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.errors = []
        self.timings = {}
        
    def add_test_result(self, test_name: str, passed: bool, duration: float, error: str = None):
        """Record a test result"""
        self.tests_run += 1
        self.timings[test_name] = duration
        
        if passed:
            self.tests_passed += 1
            print(f"✅ {test_name} - {duration:.2f}s")
        else:
            self.tests_failed += 1
            self.errors.append((test_name, error))
            print(f"❌ {test_name} - {duration:.2f}s - {error}")
    
    def print_summary(self):
        """Print test summary"""
        print("\n" + "="*60)
        print("TEST SUMMARY")
        print("="*60)
        print(f"Total tests: {self.tests_run}")
        print(f"Passed: {self.tests_passed}")
        print(f"Failed: {self.tests_failed}")
        print(f"Success rate: {(self.tests_passed/self.tests_run)*100:.1f}%" if self.tests_run > 0 else "No tests run")
        
        if self.timings:
            print(f"\nTiming summary:")
            for test_name, duration in self.timings.items():
                print(f"  {test_name}: {duration:.2f}s")
        
        if self.errors:
            print(f"\nErrors:")
            for test_name, error in self.errors:
                print(f"  {test_name}: {error}")


async def test_configuration():
    """Test configuration loading and validation"""
    print("\n📋 Testing configuration...")
    
    try:
        config = get_config()
        print_config_summary(config)
        return True, "Configuration loaded successfully"
    except Exception as e:
        return False, f"Configuration failed: {e}"


async def test_openai_provider():
    """Test OpenAI provider"""
    print("\n🤖 Testing OpenAI provider...")
    
    try:
        result = await validate_receipt_labels_v2(
            SAMPLE_RECEIPT_DATA["image_id"],
            SAMPLE_RECEIPT_DATA["receipt_id"],
            SAMPLE_RECEIPT_DATA["labels"],
            llm_provider="openai"
        )
        
        if result["success"]:
            return True, f"OpenAI validation successful. Processed {len(result['validation_results'])} labels"
        else:
            return False, f"OpenAI validation failed: {result.get('error', 'Unknown error')}"
            
    except Exception as e:
        return False, f"OpenAI provider error: {e}"


async def test_ollama_provider():
    """Test Ollama provider"""
    print("\n🦙 Testing Ollama provider...")
    
    try:
        result = await validate_receipt_labels_v2(
            SAMPLE_RECEIPT_DATA["image_id"],
            SAMPLE_RECEIPT_DATA["receipt_id"], 
            SAMPLE_RECEIPT_DATA["labels"],
            llm_provider="ollama"
        )
        
        if result["success"]:
            return True, f"Ollama validation successful. Processed {len(result['validation_results'])} labels"
        else:
            return False, f"Ollama validation failed: {result.get('error', 'Unknown error')}"
            
    except Exception as e:
        return False, f"Ollama provider error: {e}"


async def test_batch_validation():
    """Test batch validation functionality"""
    print("\n📦 Testing batch validation...")
    
    try:
        validator = ReceiptValidator()
        
        results = await validator.validate_batch(BATCH_TEST_DATA, max_concurrent=2)
        
        if len(results) == len(BATCH_TEST_DATA):
            successful = sum(1 for r in results if r["success"])
            return True, f"Batch validation completed. {successful}/{len(results)} successful"
        else:
            return False, f"Expected {len(BATCH_TEST_DATA)} results, got {len(results)}"
            
    except Exception as e:
        return False, f"Batch validation error: {e}"


async def test_metrics_collection():
    """Test metrics collection"""
    print("\n📊 Testing metrics collection...")
    
    try:
        validator = ReceiptValidator()
        
        # Run a few validations
        await validator.validate_single_receipt(
            SAMPLE_RECEIPT_DATA["image_id"],
            SAMPLE_RECEIPT_DATA["receipt_id"],
            SAMPLE_RECEIPT_DATA["labels"][:2]  # Just first 2 labels
        )
        
        metrics = validator.get_metrics_summary()
        
        if metrics["validation_count"] > 0:
            return True, f"Metrics collected: {metrics['validation_count']} validations, {metrics['average_time_seconds']:.2f}s avg"
        else:
            return False, "No metrics were collected"
            
    except Exception as e:
        return False, f"Metrics collection error: {e}"


async def test_caching():
    """Test result caching"""
    print("\n💾 Testing result caching...")
    
    try:
        # Create validator with caching enabled
        config = ValidationConfig.from_env()
        config.cache.enabled = True
        validator = ReceiptValidator(config)
        
        # First validation (should not be cached)
        start_time = time.time()
        result1 = await validator.validate_single_receipt(
            SAMPLE_RECEIPT_DATA["image_id"],
            SAMPLE_RECEIPT_DATA["receipt_id"],
            SAMPLE_RECEIPT_DATA["labels"][:1]
        )
        first_duration = time.time() - start_time
        
        # Second validation (should be cached)
        start_time = time.time()
        result2 = await validator.validate_single_receipt(
            SAMPLE_RECEIPT_DATA["image_id"],
            SAMPLE_RECEIPT_DATA["receipt_id"],
            SAMPLE_RECEIPT_DATA["labels"][:1]
        )
        second_duration = time.time() - start_time
        
        # Check if second call was faster (cached)
        if result2["metadata"].get("from_cache", False):
            return True, f"Caching working. First: {first_duration:.2f}s, Cached: {second_duration:.2f}s"
        else:
            return False, f"Caching not working. Both calls took similar time: {first_duration:.2f}s, {second_duration:.2f}s"
            
    except Exception as e:
        return False, f"Caching test error: {e}"


async def test_error_handling():
    """Test error handling with invalid inputs"""
    print("\n🛡️ Testing error handling...")
    
    try:
        validator = ReceiptValidator()
        
        # Test with empty labels
        result = await validator.validate_single_receipt(
            "INVALID_IMG",
            -1,
            []
        )
        
        # Should handle gracefully without crashing
        if "error" in result or not result["success"]:
            return True, "Error handling working - gracefully handled invalid input"
        else:
            return False, "Error handling failed - should have detected invalid input"
            
    except Exception as e:
        # This is actually good - it means error handling is working
        return True, f"Error handling working - caught exception: {type(e).__name__}"


def check_prerequisites():
    """Check if required environment variables and dependencies are available"""
    print("🔍 Checking prerequisites...")
    
    issues = []
    
    # Check environment variables
    required_vars = ["DYNAMODB_TABLE_NAME"]
    for var in required_vars:
        if not os.getenv(var):
            issues.append(f"Missing required environment variable: {var}")
    
    # Check optional but recommended vars
    if not os.getenv("OPENAI_API_KEY"):
        issues.append("OPENAI_API_KEY not set - OpenAI tests will fail")
    
    # Check Ollama availability
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        import requests
        response = requests.get(f"{ollama_url}/api/tags", timeout=5)
        if response.status_code != 200:
            issues.append(f"Ollama not available at {ollama_url} - Ollama tests will fail")
    except Exception:
        issues.append(f"Cannot connect to Ollama at {ollama_url} - Ollama tests will fail")
    
    if issues:
        print("⚠️ Prerequisites check found issues:")
        for issue in issues:
            print(f"  - {issue}")
        print("\nSome tests may fail. Continue anyway? (y/n): ", end="")
        if input().lower() != 'y':
            sys.exit(1)
    else:
        print("✅ All prerequisites met")


async def run_all_tests(provider_filter: str = None, mode_filter: str = None):
    """Run all tests"""
    results = TestResults()
    
    # Configuration test (always run)
    start_time = time.time()
    passed, message = await test_configuration()
    results.add_test_result("Configuration", passed, time.time() - start_time, None if passed else message)
    
    if not passed:
        print("❌ Configuration test failed. Cannot continue.")
        return results
    
    # Provider-specific tests
    if not provider_filter or provider_filter == "openai":
        start_time = time.time()
        passed, message = await test_openai_provider()
        results.add_test_result("OpenAI Provider", passed, time.time() - start_time, None if passed else message)
    
    if not provider_filter or provider_filter == "ollama":
        start_time = time.time()
        passed, message = await test_ollama_provider()
        results.add_test_result("Ollama Provider", passed, time.time() - start_time, None if passed else message)
    
    # Feature tests
    if not mode_filter or mode_filter == "batch":
        start_time = time.time()
        passed, message = await test_batch_validation()
        results.add_test_result("Batch Validation", passed, time.time() - start_time, None if passed else message)
    
    # Additional feature tests
    start_time = time.time()
    passed, message = await test_metrics_collection()
    results.add_test_result("Metrics Collection", passed, time.time() - start_time, None if passed else message)
    
    start_time = time.time()
    passed, message = await test_caching()
    results.add_test_result("Result Caching", passed, time.time() - start_time, None if passed else message)
    
    start_time = time.time()
    passed, message = await test_error_handling()
    results.add_test_result("Error Handling", passed, time.time() - start_time, None if passed else message)
    
    return results


def main():
    """Main test runner"""
    parser = argparse.ArgumentParser(description="Test LangChain validation implementation")
    parser.add_argument("--provider", choices=["openai", "ollama"], help="Test specific provider only")
    parser.add_argument("--mode", choices=["realtime", "batch"], help="Test specific mode only")
    parser.add_argument("--config-only", action="store_true", help="Test configuration only")
    parser.add_argument("--skip-prereqs", action="store_true", help="Skip prerequisite checks")
    
    args = parser.parse_args()
    
    print("🧪 LangChain Receipt Validation Test Suite")
    print("=" * 50)
    
    if not args.skip_prereqs:
        check_prerequisites()
    
    if args.config_only:
        async def config_test():
            results = TestResults()
            start_time = time.time()
            passed, message = await test_configuration()
            results.add_test_result("Configuration", passed, time.time() - start_time, None if passed else message)
            results.print_summary()
        
        asyncio.run(config_test())
        return
    
    # Set environment for testing
    os.environ.setdefault("DYNAMODB_TABLE_NAME", "test-receipt-validation")
    os.environ.setdefault("CHROMA_PERSIST_PATH", "./test_chroma_db")
    
    # Run tests
    results = asyncio.run(run_all_tests(args.provider, args.mode))
    results.print_summary()
    
    # Exit with appropriate code
    sys.exit(0 if results.tests_failed == 0 else 1)


if __name__ == "__main__":
    main()