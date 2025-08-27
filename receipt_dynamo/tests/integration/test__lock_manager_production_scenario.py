"""
Integration tests reproducing the production lock validation hanging issue.

This test uses exact values from production logs to reproduce the hanging 
validate_ownership() behavior that occurs during label updates.
"""
import time
from datetime import datetime, timezone
from typing import Literal

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ChromaDBCollection
from receipt_label.utils.lock_manager import LockManager


@pytest.mark.integration
class TestLockManagerProductionScenario:
    """Test lock manager using exact production values to reproduce hanging issue."""

    def test_production_validate_ownership_scenario_1(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test validate_ownership with exact values from production logs."""
        client = DynamoClient(dynamodb_table)
        
        # Create lock manager with production-like configuration
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.WORDS,
            heartbeat_interval=1,  # Short interval like production
            lock_duration_minutes=5,  # Production duration
        )
        
        # Use exact lock_id from production logs: chroma-words-update-1756226197
        production_lock_id = "chroma-words-update-1756226197"
        
        # Acquire lock
        success = manager.acquire(production_lock_id)
        assert success is True
        assert manager.is_locked() is True
        
        # Get lock info to verify state
        lock_info = manager.get_lock_info()
        assert lock_info is not None
        assert lock_info["lock_id"] == production_lock_id
        print(f"Lock acquired: {lock_info}")
        
        # This is where production hangs - validate ownership
        print("About to call validate_ownership()...")
        start_time = time.time()
        
        # Use threading-based timeout to prevent test from hanging indefinitely
        import threading
        result_container = {"is_owner": None, "error": None, "completed": False}
        
        def validate_with_timeout():
            try:
                result_container["is_owner"] = manager.validate_ownership()
                result_container["completed"] = True
            except Exception as e:
                result_container["error"] = e
                result_container["completed"] = True
        
        # Start validation in separate thread
        validation_thread = threading.Thread(target=validate_with_timeout)
        validation_thread.start()
        validation_thread.join(timeout=10)  # 10 second timeout
        
        elapsed = time.time() - start_time
        
        if validation_thread.is_alive():
            # Thread is still running - validation hung
            print(f"validate_ownership() timed out after {elapsed:.2f}s")
            manager.release()  # Clean up
            pytest.fail(f"validate_ownership() hung for {elapsed:.2f}s - reproduces production issue")
        elif result_container["error"]:
            # Thread completed with error
            manager.release()  # Clean up
            raise result_container["error"]
        else:
            # Thread completed successfully
            print(f"validate_ownership() completed in {elapsed:.2f}s, result: {result_container['is_owner']}")
            assert result_container["is_owner"] is True
        
        manager.release()

    def test_production_validate_ownership_scenario_2(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test validate_ownership with second production lock_id."""
        client = DynamoClient(dynamodb_table)
        
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.WORDS,
            heartbeat_interval=1,
            lock_duration_minutes=5,
        )
        
        # Use exact lock_id from production logs: chroma-words-update-1756226222
        production_lock_id = "chroma-words-update-1756226222"
        
        success = manager.acquire(production_lock_id)
        assert success is True
        
        # Test validate_ownership with threading-based timeout protection
        import threading
        result_container = {"is_owner": None, "error": None, "completed": False}
        
        def validate_with_timeout():
            try:
                result_container["is_owner"] = manager.validate_ownership()
                result_container["completed"] = True
            except Exception as e:
                result_container["error"] = e
                result_container["completed"] = True
        
        start_time = time.time()
        validation_thread = threading.Thread(target=validate_with_timeout)
        validation_thread.start()
        validation_thread.join(timeout=10)  # 10 second timeout
        
        elapsed = time.time() - start_time
        
        if validation_thread.is_alive():
            # Thread is still running - validation hung
            manager.release()
            pytest.fail(f"validate_ownership() hung for {elapsed:.2f}s")
        elif result_container["error"]:
            # Thread completed with error
            manager.release()
            raise result_container["error"]
        else:
            # Thread completed successfully
            print(f"validate_ownership() completed in {elapsed:.2f}s")
            assert result_container["is_owner"] is True
        
        manager.release()

    def test_production_multiple_concurrent_validation(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test multiple concurrent validate_ownership calls like in production."""
        client = DynamoClient(dynamodb_table)
        
        # Create two managers like production has multiple Lambda instances
        manager1 = LockManager(client, ChromaDBCollection.WORDS)
        manager2 = LockManager(client, ChromaDBCollection.WORDS) 
        
        lock_id1 = "chroma-words-update-1756226197"
        lock_id2 = "chroma-words-update-1756226222"
        
        # Acquire both locks like production scenario
        success1 = manager1.acquire(lock_id1)
        success2 = manager2.acquire(lock_id2)
        
        assert success1 is True
        assert success2 is True
        
        # Test concurrent validation with threading-based timeout protection
        import threading
        
        results = {}
        timeouts = {}
        
        def validate_with_timeout(manager, manager_name):
            result_container = {"is_owner": None, "error": None, "completed": False}
            
            def do_validation():
                try:
                    result_container["is_owner"] = manager.validate_ownership()
                    result_container["completed"] = True
                except Exception as e:
                    result_container["error"] = e
                    result_container["completed"] = True
            
            start = time.time()
            validation_thread = threading.Thread(target=do_validation)
            validation_thread.start()
            validation_thread.join(timeout=10)  # 10 second timeout
            
            elapsed = time.time() - start
            
            if validation_thread.is_alive():
                timeouts[manager_name] = elapsed
                print(f"{manager_name} validate_ownership() timed out after {elapsed:.2f}s")
            elif result_container["error"]:
                print(f"{manager_name} validation failed with error: {result_container['error']}")
                timeouts[manager_name] = elapsed
            else:
                results[manager_name] = result_container["is_owner"]
                print(f"{manager_name} validate_ownership() completed in {elapsed:.2f}s")
        
        # Run validations concurrently like production
        thread1 = threading.Thread(target=validate_with_timeout, args=(manager1, "manager1"))
        thread2 = threading.Thread(target=validate_with_timeout, args=(manager2, "manager2"))
        
        thread1.start()
        thread2.start()
        
        thread1.join(timeout=15)
        thread2.join(timeout=15)
        
        # Check results
        if timeouts:
            manager1.release()
            manager2.release()
            pytest.fail(f"Concurrent validate_ownership() calls timed out: {timeouts}")
        
        assert results.get("manager1") is True
        assert results.get("manager2") is True
        
        manager1.release()
        manager2.release()

    def test_production_expired_lock_validation(
        self, dynamodb_table: Literal["MyMockedTable"]
    ) -> None:
        """Test validate_ownership on locks that may have expired."""
        client = DynamoClient(dynamodb_table)
        
        # Create manager with very short lock duration like some production locks
        manager = LockManager(
            dynamo_client=client,
            collection=ChromaDBCollection.WORDS,
            lock_duration_minutes=0.01,  # 36 seconds - very short
        )
        
        production_lock_id = "chroma-words-update-test-expired"
        success = manager.acquire(production_lock_id)
        assert success is True
        
        # Wait for lock to potentially expire
        print("Waiting for lock to potentially expire...")
        time.sleep(2)  # Wait 2 seconds
        
        # Test validate_ownership with threading-based timeout protection
        import threading
        result_container = {"is_owner": None, "error": None, "completed": False}
        
        def validate_with_timeout():
            try:
                result_container["is_owner"] = manager.validate_ownership()
                result_container["completed"] = True
            except Exception as e:
                result_container["error"] = e
                result_container["completed"] = True
        
        start_time = time.time()
        validation_thread = threading.Thread(target=validate_with_timeout)
        validation_thread.start()
        validation_thread.join(timeout=10)  # 10 second timeout
        
        elapsed = time.time() - start_time
        
        if validation_thread.is_alive():
            # Thread is still running - validation hung
            manager.release()
            pytest.fail(f"validate_ownership() on expired lock hung for {elapsed:.2f}s")
        elif result_container["error"]:
            # Thread completed with error
            print(f"validate_ownership() on expired lock failed with error: {result_container['error']}")
            # This might be expected for expired locks
        else:
            # Thread completed successfully
            print(f"validate_ownership() on expired lock completed in {elapsed:.2f}s, result: {result_container['is_owner']}")
            # Could be True or False depending on exact timing
        
        try:
            manager.release()
        except:
            pass  # May fail if lock expired

    def test_validate_ownership_after_cleanup(
        self, dynamodb_table: Literal["MyMockedTable"]  
    ) -> None:
        """Test validate_ownership behavior after lock cleanup (like we just did in production)."""
        client = DynamoClient(dynamodb_table)
        
        manager = LockManager(client, ChromaDBCollection.WORDS)
        
        # Acquire lock
        lock_id = "chroma-words-update-after-cleanup"
        success = manager.acquire(lock_id)
        assert success is True
        
        # Simulate the cleanup we just did - manually delete the lock
        # This mimics the situation where locks were cleaned up externally
        try:
            client.delete_compaction_lock(lock_id, manager.lock_owner, ChromaDBCollection.WORDS)
            print("Lock manually deleted (simulating cleanup)")
        except:
            pass  # May already be gone
        
        # Now test validate_ownership - this should detect the lock is gone
        # and NOT hang
        import threading
        result_container = {"is_owner": None, "error": None, "completed": False}
        
        def validate_with_timeout():
            try:
                result_container["is_owner"] = manager.validate_ownership()
                result_container["completed"] = True
            except Exception as e:
                result_container["error"] = e
                result_container["completed"] = True
        
        start_time = time.time()
        validation_thread = threading.Thread(target=validate_with_timeout)
        validation_thread.start()
        validation_thread.join(timeout=10)  # 10 second timeout
        
        elapsed = time.time() - start_time
        
        if validation_thread.is_alive():
            # Thread is still running - validation hung
            pytest.fail(f"validate_ownership() hung after cleanup for {elapsed:.2f}s - this may be the bug!")
        elif result_container["error"]:
            # Thread completed with error
            print(f"validate_ownership() after cleanup failed with error: {result_container['error']}")
            # This might be expected when lock doesn't exist
        else:
            # Thread completed successfully
            print(f"validate_ownership() after cleanup completed in {elapsed:.2f}s, result: {result_container['is_owner']}")
            # Should return False since lock was deleted externally
            assert result_container["is_owner"] is False
        
        # Try to release (may fail since lock is gone)
        try:
            manager.release()
        except:
            pass