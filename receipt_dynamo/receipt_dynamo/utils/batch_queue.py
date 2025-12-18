"""
Batch queue implementation for efficient DynamoDB writes and rate limiting.
"""

import threading
import time
from typing import Any, Callable, Dict, List, Literal, Optional


class BatchQueue:
    """
    Thread-safe batch queue for aggregating items before processing.

    Automatically flushes when batch size is reached or after timeout.
    Helps reduce API calls and handle rate limiting gracefully.
    """

    def __init__(
        self,
        flush_callback: Callable[[List[Dict[str, Any]]], None],
        batch_size: int = 25,
        flush_interval: float = 5.0,
        auto_flush: bool = True,
    ):
        """
        Initialize batch queue.

        Args:
            flush_callback: Function to call with batched items
            batch_size: Maximum items before automatic flush
            flush_interval: Maximum seconds before automatic flush
            auto_flush: Whether to start auto-flush thread
        """
        self.flush_callback = flush_callback
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        # Thread-safe queue
        self.queue: List[Dict[str, Any]] = []
        self.lock = threading.Lock()
        self.last_flush_time = time.time()

        # Auto-flush thread
        self.auto_flush = auto_flush
        self.flush_thread: Optional[threading.Thread] = None
        self.stop_flag = threading.Event()

        if self.auto_flush:
            self._start_auto_flush()

    def _start_auto_flush(self) -> None:
        """Start background thread for automatic flushing."""
        self.flush_thread = threading.Thread(
            target=self._auto_flush_worker, daemon=True
        )
        self.flush_thread.start()

    def _auto_flush_worker(self) -> None:
        """Background worker that flushes queue periodically."""
        while not self.stop_flag.is_set():
            time.sleep(0.1)  # Check every 100ms

            items_to_flush = None
            with self.lock:
                time_since_flush = time.time() - self.last_flush_time
                if self.queue and time_since_flush >= self.flush_interval:
                    items_to_flush = self._prepare_flush()

            # Execute callback outside of lock
            if items_to_flush:
                self.flush_callback(items_to_flush)

    def add_item(self, item: Dict[str, Any]) -> None:
        """
        Add item to queue, flushing if batch size reached.

        Args:
            item: Item to add to queue
        """
        items_to_flush = None
        with self.lock:
            self.queue.append(item)

            # Check if we should flush
            if len(self.queue) >= self.batch_size:
                items_to_flush = self._prepare_flush()

        # Execute callback outside of lock
        if items_to_flush:
            self.flush_callback(items_to_flush)

    def flush(self) -> int:
        """
        Manually flush all items in queue.

        Returns:
            Number of items flushed
        """
        items_to_flush = None
        with self.lock:
            items_to_flush = self._prepare_flush()

        # Execute callback outside of lock
        if items_to_flush:
            self.flush_callback(items_to_flush)
            return len(items_to_flush)
        return 0

    def _prepare_flush(self) -> Optional[List[Dict[str, Any]]]:
        """
        Prepare items for flushing (must be called with lock held).

        Returns:
            Items to flush or None if queue is empty
        """
        if not self.queue:
            return None

        # Copy items and clear queue
        items_to_flush = self.queue.copy()
        self.queue.clear()
        self.last_flush_time = time.time()
        return items_to_flush

    def get_stats(self) -> Dict[str, Any]:
        """Get current queue statistics."""
        with self.lock:
            return {
                "queue_size": len(self.queue),
                "time_since_flush": time.time() - self.last_flush_time,
                "auto_flush_enabled": self.auto_flush,
            }

    def stop(self) -> None:
        """Stop auto-flush thread and flush remaining items."""
        if self.auto_flush and self.flush_thread:
            self.stop_flag.set()
            self.flush_thread.join(timeout=1.0)

        # Final flush
        self.flush()

    def __enter__(self) -> "BatchQueue":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Literal[False]:
        """Context manager exit - ensure all items are flushed."""
        self.stop()
        return False
