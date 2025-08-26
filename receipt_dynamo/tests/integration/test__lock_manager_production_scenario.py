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
        
        # Set a timeout to prevent test from hanging indefinitely
        import signal
        def timeout_handler(signum, frame):
            raise TimeoutError("validate_ownership() call timed out - this reproduces the production hang!")
        
        # Set 10 second timeout 
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(10)
        
        try:
            is_owner = manager.validate_ownership()
            signal.alarm(0)  # Cancel timeout
            elapsed = time.time() - start_time
            print(f"validate_ownership() completed in {elapsed:.2f}s, result: {is_owner}")
            assert is_owner is True
        except TimeoutError as e:
            signal.alarm(0)  # Cancel timeout
            elapsed = time.time() - start_time
            print(f"validate_ownership() timed out after {elapsed:.2f}s")
            manager.release()  # Clean up
            pytest.fail(f"validate_ownership() hung for {elapsed:.2f}s - reproduces production issue: {e}")
        
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
        
        # Test validate_ownership with timeout protection
        import signal
        def timeout_handler(signum, frame):
            raise TimeoutError("validate_ownership() timed out")
        
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(10)  # 10 second timeout
        
        try:
            start_time = time.time()
            is_owner = manager.validate_ownership()
            signal.alarm(0)
            elapsed = time.time() - start_time
            print(f"validate_ownership() completed in {elapsed:.2f}s")
            assert is_owner is True
        except TimeoutError:
            signal.alarm(0)
            elapsed = time.time() - start_time
            manager.release()
            pytest.fail(f"validate_ownership() hung for {elapsed:.2f}s")
        
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
        
        # Test concurrent validation with timeout protection
        import signal
        import threading
        
        results = {}
        timeouts = {}
        
        def validate_with_timeout(manager, manager_name):
            def timeout_handler(signum, frame):
                raise TimeoutError(f"{manager_name} validate_ownership() timed out")
            
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(10)
            
            try:
                start = time.time()
                result = manager.validate_ownership()
                elapsed = time.time() - start
                signal.alarm(0)
                results[manager_name] = result
                print(f"{manager_name} validate_ownership() completed in {elapsed:.2f}s")
            except TimeoutError as e:
                signal.alarm(0)
                elapsed = time.time() - start
                timeouts[manager_name] = elapsed
                print(f"{manager_name} validate_ownership() timed out after {elapsed:.2f}s")
        
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
        
        # Test validate_ownership with timeout protection
        import signal
        def timeout_handler(signum, frame):
            raise TimeoutError("validate_ownership() timed out on potentially expired lock")
        
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(10)
        
        try:
            start_time = time.time()
            is_owner = manager.validate_ownership()
            elapsed = time.time() - start_time
            signal.alarm(0)
            print(f"validate_ownership() on expired lock completed in {elapsed:.2f}s, result: {is_owner}")
            # Could be True or False depending on exact timing
        except TimeoutError:
            signal.alarm(0)
            elapsed = time.time() - start_time
            manager.release()
            pytest.fail(f"validate_ownership() on expired lock hung for {elapsed:.2f}s")
        
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
        import signal
        def timeout_handler(signum, frame):
            raise TimeoutError("validate_ownership() hung after external cleanup")
        
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(10)
        
        try:
            start_time = time.time()
            is_owner = manager.validate_ownership()
            elapsed = time.time() - start_time
            signal.alarm(0)
            print(f"validate_ownership() after cleanup completed in {elapsed:.2f}s, result: {is_owner}")
            # Should return False since lock was deleted externally
            assert is_owner is False
        except TimeoutError:
            signal.alarm(0)
            elapsed = time.time() - start_time
            pytest.fail(f"validate_ownership() hung after cleanup for {elapsed:.2f}s - this may be the bug!")
        
        # Try to release (may fail since lock is gone)
        try:
            manager.release()
        except:
            pass