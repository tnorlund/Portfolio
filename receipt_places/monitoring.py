"""
Monitoring and metrics collection for Places API v1 migration.

Provides utilities for:
- Tracking dual-write success rates
- Monitoring API performance
- Recording deployment metrics
- Generating dashboards
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class MetricsSnapshot:
    """Snapshot of system metrics at a point in time."""
    timestamp: datetime = field(default_factory=datetime.utcnow)

    # Dual-write metrics
    metadata_writes: int = 0
    metadata_write_errors: int = 0
    receipt_place_writes: int = 0
    receipt_place_write_errors: int = 0

    # API metrics
    api_calls_legacy: int = 0
    api_calls_v1: int = 0
    api_errors: int = 0
    api_latency_ms: Dict[str, float] = field(default_factory=dict)

    # DynamoDB metrics
    dynamodb_write_units: int = 0
    dynamodb_throttles: int = 0

    # Cache metrics
    cache_hits: int = 0
    cache_misses: int = 0
    cache_evictions: int = 0

    @property
    def dual_write_success_rate(self) -> float:
        """Calculate dual-write success rate."""
        total = self.metadata_writes + self.metadata_write_errors
        if total == 0:
            return 1.0  # No writes yet, assume success
        return self.metadata_writes / total

    @property
    def receipt_place_success_rate(self) -> float:
        """Calculate ReceiptPlace creation success rate."""
        total = self.receipt_place_writes + self.receipt_place_write_errors
        if total == 0:
            return 1.0  # No writes yet, assume success
        return self.receipt_place_writes / total

    @property
    def cache_hit_rate(self) -> float:
        """Calculate cache hit rate."""
        total = self.cache_hits + self.cache_misses
        if total == 0:
            return 0.0
        return self.cache_hits / total


class MetricsCollector:
    """
    Collects metrics during v1 migration deployment.

    Tracks:
    - Dual-write success/failure rates
    - API performance metrics
    - DynamoDB capacity usage
    - Cache performance
    """

    def __init__(self):
        """Initialize metrics collector."""
        self.snapshots = []
        self.current_snapshot = MetricsSnapshot()
        self._start_time = time.time()

    def record_metadata_write(self, success: bool):
        """Record a metadata write attempt."""
        if success:
            self.current_snapshot.metadata_writes += 1
        else:
            self.current_snapshot.metadata_write_errors += 1

    def record_receipt_place_write(self, success: bool):
        """Record a ReceiptPlace write attempt."""
        if success:
            self.current_snapshot.receipt_place_writes += 1
        else:
            self.current_snapshot.receipt_place_write_errors += 1

    def record_api_call(
        self,
        api_version: str,
        latency_ms: float,
        success: bool,
    ):
        """Record an API call."""
        if api_version == "v1":
            self.current_snapshot.api_calls_v1 += 1
        else:
            self.current_snapshot.api_calls_legacy += 1

        if not success:
            self.current_snapshot.api_errors += 1

        # Track latency percentiles
        key = f"latency_{api_version}"
        if key not in self.current_snapshot.api_latency_ms:
            self.current_snapshot.api_latency_ms[key] = latency_ms
        else:
            # Simple moving average
            prev = self.current_snapshot.api_latency_ms[key]
            self.current_snapshot.api_latency_ms[key] = (prev + latency_ms) / 2

    def record_cache_hit(self, hit: bool):
        """Record a cache hit or miss."""
        if hit:
            self.current_snapshot.cache_hits += 1
        else:
            self.current_snapshot.cache_misses += 1

    def take_snapshot(self) -> MetricsSnapshot:
        """Take a metrics snapshot and reset counters."""
        snapshot = self.current_snapshot
        self.snapshots.append(snapshot)
        self.current_snapshot = MetricsSnapshot()
        return snapshot

    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics across all snapshots."""
        if not self.snapshots:
            return {}

        total_metadata_writes = sum(s.metadata_writes for s in self.snapshots)
        total_metadata_errors = sum(
            s.metadata_write_errors for s in self.snapshots
        )
        total_receipt_place_writes = sum(
            s.receipt_place_writes for s in self.snapshots
        )
        total_receipt_place_errors = sum(
            s.receipt_place_write_errors for s in self.snapshots
        )
        total_api_calls = sum(s.api_calls_legacy + s.api_calls_v1
                             for s in self.snapshots)
        total_api_errors = sum(s.api_errors for s in self.snapshots)

        elapsed_seconds = time.time() - self._start_time

        return {
            "elapsed_seconds": elapsed_seconds,
            "snapshots_collected": len(self.snapshots),
            "dual_write": {
                "metadata_writes": total_metadata_writes,
                "metadata_errors": total_metadata_errors,
                "metadata_success_rate": (
                    total_metadata_writes / max(total_metadata_writes +
                                               total_metadata_errors, 1)
                ),
                "receipt_place_writes": total_receipt_place_writes,
                "receipt_place_errors": total_receipt_place_errors,
                "receipt_place_success_rate": (
                    total_receipt_place_writes /
                    max(total_receipt_place_writes + total_receipt_place_errors, 1)
                ),
            },
            "api": {
                "legacy_calls": sum(s.api_calls_legacy for s in self.snapshots),
                "v1_calls": sum(s.api_calls_v1 for s in self.snapshots),
                "total_calls": total_api_calls,
                "errors": total_api_errors,
                "error_rate": (
                    total_api_errors / max(total_api_calls, 1)
                ),
            },
            "cache": {
                "hits": sum(s.cache_hits for s in self.snapshots),
                "misses": sum(s.cache_misses for s in self.snapshots),
                "avg_hit_rate": (
                    sum(s.cache_hit_rate for s in self.snapshots) /
                    max(len(self.snapshots), 1)
                ),
            },
        }

    def print_summary(self):
        """Print metrics summary to console."""
        summary = self.get_summary()

        if not summary:
            print("No metrics collected yet")
            return

        print("\n" + "=" * 70)
        print("METRICS SUMMARY")
        print("=" * 70)

        print(f"\nUptime: {summary['elapsed_seconds']:.1f}s")
        print(f"Snapshots: {summary['snapshots_collected']}")

        print("\nDual-Write Metrics:")
        dw = summary["dual_write"]
        print(f"  ReceiptMetadata: {dw['metadata_writes']} writes, "
              f"{dw['metadata_errors']} errors "
              f"({dw['metadata_success_rate']:.1%} success)")
        print(f"  ReceiptPlace: {dw['receipt_place_writes']} writes, "
              f"{dw['receipt_place_errors']} errors "
              f"({dw['receipt_place_success_rate']:.1%} success)")

        print("\nAPI Metrics:")
        api = summary["api"]
        print(f"  Legacy API: {api['legacy_calls']} calls")
        print(f"  v1 API: {api['v1_calls']} calls")
        print(f"  Total: {api['total_calls']} calls, "
              f"{api['errors']} errors "
              f"({api['error_rate']:.2%} error rate)")

        print("\nCache Metrics:")
        cache = summary["cache"]
        print(f"  Hits: {cache['hits']}, Misses: {cache['misses']}")
        print(f"  Average Hit Rate: {cache['avg_hit_rate']:.1%}")

        print("=" * 70 + "\n")


# Global metrics collector instance
_metrics_collector: Optional[MetricsCollector] = None


def get_metrics_collector() -> MetricsCollector:
    """Get or create global metrics collector."""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector


def record_dual_write(metadata_success: bool, place_success: bool):
    """Record a dual-write operation."""
    collector = get_metrics_collector()
    collector.record_metadata_write(metadata_success)
    collector.record_receipt_place_write(place_success)


def record_api_call(
    api_version: str,
    latency_ms: float,
    success: bool,
):
    """Record an API call."""
    collector = get_metrics_collector()
    collector.record_api_call(api_version, latency_ms, success)


def record_cache_hit(hit: bool):
    """Record a cache hit or miss."""
    collector = get_metrics_collector()
    collector.record_cache_hit(hit)


def take_snapshot() -> MetricsSnapshot:
    """Take a metrics snapshot."""
    collector = get_metrics_collector()
    return collector.take_snapshot()
