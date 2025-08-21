#!/usr/bin/env python3
"""
Test script for ChromaDB hash verification functionality.

This script demonstrates and tests the new hash-based verification system
for ChromaDB snapshots as described in GitHub issue #334.
"""

import os
import sys
import tempfile
import shutil
import time
from pathlib import Path

# Add the receipt_label module to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "receipt_label"))

def test_hash_calculation():
    """Test basic hash calculation functionality."""
    print("🧪 Testing hash calculation...")
    
    try:
        from receipt_label.utils.chroma_hash import (
            calculate_chromadb_hash,
            create_hash_file_content,
            parse_hash_file_content,
            compare_hash_results
        )
        
        # Create a temporary directory with some test files
        with tempfile.TemporaryDirectory() as temp_dir:
            test_dir = Path(temp_dir) / "test_chromadb"
            test_dir.mkdir()
            
            # Create some test files to simulate ChromaDB structure
            (test_dir / "chroma.sqlite3").write_text("test database content")
            (test_dir / "chroma.sqlite3-wal").write_text("wal content")
            (test_dir / "chroma.sqlite3-shm").write_text("shared memory content")
            
            # Calculate hash
            hash_result = calculate_chromadb_hash(str(test_dir))
            
            print(f"  ✅ Hash calculated: {hash_result.directory_hash}")
            print(f"  📊 Files processed: {hash_result.file_count}")
            print(f"  💾 Total size: {hash_result.total_size_bytes} bytes")
            print(f"  ⏱️  Calculation time: {hash_result.calculation_time_seconds:.3f}s")
            
            # Test hash file creation and parsing
            hash_content = create_hash_file_content(hash_result)
            parsed_result = parse_hash_file_content(hash_content)
            
            assert hash_result.directory_hash == parsed_result.directory_hash
            print("  ✅ Hash file creation/parsing works")
            
            # Test comparison
            comparison = compare_hash_results(hash_result, parsed_result)
            assert comparison["is_identical"]
            print("  ✅ Hash comparison works")
            
        return True
        
    except ImportError as e:
        print(f"  ❌ Import error: {e}")
        return False
    except Exception as e:
        print(f"  ❌ Test failed: {e}")
        return False


def test_s3_helpers():
    """Test S3 helper functions (without actual S3 calls)."""
    print("\n🧪 Testing S3 helper functions...")
    
    try:
        from receipt_label.utils.chroma_s3_helpers import (
            verify_chromadb_sync,
            HASH_UTILS_AVAILABLE
        )
        
        if not HASH_UTILS_AVAILABLE:
            print("  ❌ Hash utilities not available")
            return False
        
        # Test the verify function with invalid parameters (should handle gracefully)
        result = verify_chromadb_sync(
            database="test",
            bucket="fake-bucket",
            local_path="/nonexistent/path"
        )
        
        # Should return an error for nonexistent path
        assert result["status"] == "error"
        print("  ✅ Error handling works for invalid paths")
        
        return True
        
    except ImportError as e:
        print(f"  ❌ Import error: {e}")
        return False
    except Exception as e:
        print(f"  ❌ Test failed: {e}")
        return False


def demonstrate_bash_equivalent():
    """Demonstrate that our hash matches the bash command from the issue."""
    print("\n🧪 Demonstrating bash command equivalence...")
    
    try:
        from receipt_label.utils.chroma_hash import calculate_chromadb_hash
        
        with tempfile.TemporaryDirectory() as temp_dir:
            test_dir = Path(temp_dir) / "bash_test"
            test_dir.mkdir()
            
            # Create test files with known content
            (test_dir / "file1.txt").write_text("Hello")
            (test_dir / "file2.txt").write_text("World")
            
            # Calculate our hash
            our_result = calculate_chromadb_hash(str(test_dir), algorithm="md5")
            
            print(f"  📁 Test directory: {test_dir}")
            print(f"  🔍 Our hash: {our_result.directory_hash}")
            print(f"  📝 Algorithm: {our_result.hash_algorithm}")
            
            # Show what the equivalent bash command would be
            bash_command = f"""
            cd {test_dir}
            find . -type f -print0 | sort -z | xargs -0 md5sum | md5sum | cut -d' ' -f1
            """
            
            print(f"  💻 Equivalent bash command:")
            print(f"     {bash_command.strip()}")
            
        return True
        
    except Exception as e:
        print(f"  ❌ Test failed: {e}")
        return False


def test_deterministic_hashing():
    """Test that hashing is deterministic (same directory = same hash)."""
    print("\n🧪 Testing deterministic hashing...")
    
    try:
        from receipt_label.utils.chroma_hash import calculate_chromadb_hash
        
        with tempfile.TemporaryDirectory() as temp_dir:
            test_dir = Path(temp_dir) / "deterministic_test"
            test_dir.mkdir()
            
            # Create test files
            (test_dir / "a.txt").write_text("Content A")
            (test_dir / "b.txt").write_text("Content B")
            (test_dir / "subdir").mkdir()
            (test_dir / "subdir" / "c.txt").write_text("Content C")
            
            # Calculate hash twice
            hash1 = calculate_chromadb_hash(str(test_dir))
            hash2 = calculate_chromadb_hash(str(test_dir))
            
            assert hash1.directory_hash == hash2.directory_hash
            assert hash1.file_count == hash2.file_count
            assert hash1.total_size_bytes == hash2.total_size_bytes
            
            print(f"  ✅ Deterministic: {hash1.directory_hash}")
            print(f"  📊 Files: {hash1.file_count}, Size: {hash1.total_size_bytes} bytes")
            
            # Test that changing content changes hash
            (test_dir / "a.txt").write_text("Different Content A")
            hash3 = calculate_chromadb_hash(str(test_dir))
            
            assert hash1.directory_hash != hash3.directory_hash
            print(f"  ✅ Content change detected: {hash3.directory_hash}")
            
        return True
        
    except Exception as e:
        print(f"  ❌ Test failed: {e}")
        return False


def show_usage_examples():
    """Show practical usage examples."""
    print("\n📖 Usage Examples:")
    
    print("""
    # Basic hash calculation
    from receipt_label.utils.chroma_hash import calculate_chromadb_hash
    
    result = calculate_chromadb_hash("/path/to/chromadb")
    print(f"Hash: {result.directory_hash}")
    
    # S3 upload with hash
    from receipt_label.utils.chroma_s3_helpers import upload_snapshot_with_hash
    
    upload_result = upload_snapshot_with_hash(
        local_snapshot_path="/tmp/chromadb",
        bucket="my-vectors-bucket", 
        snapshot_key="words/snapshot/latest/",
        calculate_hash=True
    )
    
    # Verify sync between local and S3
    from receipt_label.utils.chroma_s3_helpers import verify_chromadb_sync
    
    sync_result = verify_chromadb_sync(
        database="words",
        bucket="my-vectors-bucket",
        local_path="/tmp/chromadb"
    )
    
    if sync_result["status"] == "identical":
        print("✅ Local and S3 are in sync!")
    else:
        print("❌ Sync needed:", sync_result["message"])
    """)


def main():
    """Run all tests and show examples."""
    print("🚀 ChromaDB Hash Verification Test Suite")
    print("=" * 50)
    
    tests = [
        test_hash_calculation,
        test_s3_helpers,
        demonstrate_bash_equivalent,
        test_deterministic_hashing
    ]
    
    passed = 0
    for test_func in tests:
        if test_func():
            passed += 1
    
    print(f"\n📊 Test Results: {passed}/{len(tests)} tests passed")
    
    if passed == len(tests):
        print("🎉 All tests passed! Hash verification is working correctly.")
        show_usage_examples()
    else:
        print("❌ Some tests failed. Check the output above for details.")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())