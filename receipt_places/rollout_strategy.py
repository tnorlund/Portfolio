"""
Gradual rollout strategy for Places API v1 migration.

Manages safe, progressive migration from legacy to v1 API.

Phases:
1. Week 1-2: 10% traffic to v1 (legacy: 90%)
2. Week 3: 50% traffic to v1 (legacy: 50%)
3. Week 4: 100% traffic to v1 (legacy: 0%)

Each phase includes:
- Health check gates
- Metrics validation
- Automatic rollback if thresholds exceeded
- Stakeholder notifications
"""

import logging
import random
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class RolloutPhase(Enum):
    """Rollout phase enum."""
    BASELINE = "baseline"  # 0% v1, feature flag OFF
    PHASE_1 = "phase_1"    # 10% v1
    PHASE_2 = "phase_2"    # 50% v1
    PHASE_3 = "phase_3"    # 100% v1 (complete)


@dataclass
class RolloutConfig:
    """Configuration for gradual rollout."""
    current_phase: RolloutPhase = RolloutPhase.BASELINE
    v1_traffic_percentage: float = 0.0  # 0-100%

    # Health check thresholds for rollout gates
    max_error_rate: float = 0.01  # 1% error rate triggers rollback
    max_latency_p99_ms: float = 2000  # 2s p99 latency triggers rollback
    min_dual_write_success: float = 0.99  # 99% success rate required

    # Critical rollback thresholds
    critical_error_rate: float = 0.05  # 5% error rate triggers immediate rollback
    critical_latency_ms: float = 5000  # 5s p99 latency triggers immediate rollback
    critical_dual_write_success: float = 0.95  # 95% success rate for critical rollback

    # Phase transition requirements
    observations_required: int = 100  # Minimum records before advancing phase

    @classmethod
    def for_phase(cls, phase: RolloutPhase) -> "RolloutConfig":
        """Create config for a specific rollout phase."""
        config = cls()
        config.current_phase = phase

        if phase == RolloutPhase.BASELINE:
            config.v1_traffic_percentage = 0.0
        elif phase == RolloutPhase.PHASE_1:
            config.v1_traffic_percentage = 10.0
        elif phase == RolloutPhase.PHASE_2:
            config.v1_traffic_percentage = 50.0
        elif phase == RolloutPhase.PHASE_3:
            config.v1_traffic_percentage = 100.0

        return config


@dataclass
class HealthMetrics:
    """Health metrics from a rollout phase."""
    phase: RolloutPhase
    observations: int  # Number of receipts processed
    error_rate: float
    latency_p50_ms: float
    latency_p99_ms: float
    dual_write_success_rate: float
    api_cost_impact: float  # % change from baseline
    cache_hit_rate: float

    def meets_rollout_requirements(
        self,
        config: RolloutConfig
    ) -> tuple[bool, list[str]]:
        """
        Check if metrics meet rollout requirements.

        Returns:
            (passed: bool, issues: List[str])
        """
        issues = []

        # Check minimum observations
        if self.observations < config.observations_required:
            issues.append(
                f"Need {config.observations_required} observations, "
                f"only have {self.observations}"
            )

        # Check error rate
        if self.error_rate > config.max_error_rate:
            issues.append(
                f"Error rate {self.error_rate:.2%} exceeds "
                f"threshold {config.max_error_rate:.2%}"
            )

        # Check latency
        if self.latency_p99_ms > config.max_latency_p99_ms:
            issues.append(
                f"p99 latency {self.latency_p99_ms:.1f}ms exceeds "
                f"threshold {config.max_latency_p99_ms:.1f}ms"
            )

        # Check dual-write success
        if self.dual_write_success_rate < config.min_dual_write_success:
            issues.append(
                f"Dual-write success {self.dual_write_success_rate:.2%} "
                f"below threshold {config.min_dual_write_success:.2%}"
            )

        return len(issues) == 0, issues


class RolloutManager:
    """
    Manages gradual rollout of v1 API.

    Handles:
    - Phase transitions
    - Health check gates
    - Traffic split decisions
    - Automatic rollback if needed
    """

    def __init__(self, initial_phase: RolloutPhase = RolloutPhase.BASELINE):
        """Initialize rollout manager."""
        self.config = RolloutConfig.for_phase(initial_phase)
        self.current_phase = initial_phase
        self.phase_metrics: Dict[RolloutPhase, HealthMetrics] = {}
        self.rollback_triggered = False
        self._random_lock = threading.Lock()

    def should_use_v1_api(self) -> bool:
        """
        Determine if v1 API should be used for this request.

        Uses percentage-based traffic split.
        """
        if self.rollback_triggered:
            logger.warning("Rollout paused due to rollback trigger")
            return False

        # Thread-safe random decision based on configured percentage
        with self._random_lock:
            return random.random() < (self.config.v1_traffic_percentage / 100.0)

    def advance_phase(self, new_phase: RolloutPhase) -> bool:
        """
        Advance to next rollout phase.

        Args:
            new_phase: Target phase

        Returns:
            True if advancement successful, False if health checks failed
        """
        logger.info(f"Attempting to advance from {self.current_phase.value} "
                   f"to {new_phase.value}")

        # Check if we have metrics for current phase
        if self.current_phase not in self.phase_metrics:
            logger.error(
                f"No metrics collected for {self.current_phase.value}, "
                "cannot advance"
            )
            return False

        # Validate current phase metrics
        current_metrics = self.phase_metrics[self.current_phase]
        passed, issues = current_metrics.meets_rollout_requirements(
            self.config
        )

        if not passed:
            logger.error(
                f"Current phase {self.current_phase.value} does not meet "
                "rollout requirements:"
            )
            for issue in issues:
                logger.error(f"  - {issue}")
            return False

        # Advance to new phase
        self.current_phase = new_phase
        self.config = RolloutConfig.for_phase(new_phase)
        logger.info(
            f"Advanced to {new_phase.value} "
            f"({self.config.v1_traffic_percentage:.0f}% v1 traffic)"
        )

        return True

    def record_phase_metrics(self, metrics: HealthMetrics) -> None:
        """Record metrics for current phase."""
        self.phase_metrics[self.current_phase] = metrics
        logger.info(
            f"Recorded metrics for {self.current_phase.value}: "
            f"error_rate={metrics.error_rate:.2%}, "
            f"p99={metrics.latency_p99_ms:.0f}ms, "
            f"dual_write_success={metrics.dual_write_success_rate:.2%}"
        )

    def check_rollback_triggers(self, metrics: HealthMetrics) -> bool:
        """
        Check if rollback should be triggered.

        Returns:
            True if rollback needed
        """
        # Severe error rate
        if metrics.error_rate > self.config.critical_error_rate:
            logger.error(
                f"ROLLBACK TRIGGERED: Error rate {metrics.error_rate:.2%} "
                f"exceeds {self.config.critical_error_rate:.2%} threshold"
            )
            self.rollback_triggered = True
            return True

        # Severe latency spike
        if metrics.latency_p99_ms > self.config.critical_latency_ms:
            logger.error(
                f"ROLLBACK TRIGGERED: p99 latency {metrics.latency_p99_ms:.0f}ms "
                f"exceeds {self.config.critical_latency_ms:.0f}ms threshold"
            )
            self.rollback_triggered = True
            return True

        # Dual-write failure
        if metrics.dual_write_success_rate < self.config.critical_dual_write_success:
            logger.error(
                f"ROLLBACK TRIGGERED: Dual-write success "
                f"{metrics.dual_write_success_rate:.2%} below "
                f"{self.config.critical_dual_write_success:.2%} threshold"
            )
            self.rollback_triggered = True
            return True

        return False

    def execute_rollback(self) -> None:
        """Execute automatic rollback to baseline."""
        logger.error("EXECUTING ROLLBACK TO BASELINE")
        self.current_phase = RolloutPhase.BASELINE
        self.config = RolloutConfig.for_phase(RolloutPhase.BASELINE)
        self.rollback_triggered = True
        logger.warning(
            "Rollout paused. Manual investigation required before resuming."
        )

    def get_status(self) -> Dict[str, Any]:
        """Get current rollout status."""
        return {
            "phase": self.current_phase.value,
            "v1_traffic_percentage": self.config.v1_traffic_percentage,
            "rollback_triggered": self.rollback_triggered,
            "metrics": {
                phase.value: {
                    "observations": metrics.observations,
                    "error_rate": metrics.error_rate,
                    "latency_p99_ms": metrics.latency_p99_ms,
                    "dual_write_success": metrics.dual_write_success_rate,
                }
                for phase, metrics in self.phase_metrics.items()
            }
        }


# Global rollout manager instance
_rollout_manager: Optional[RolloutManager] = None
_rollout_lock = threading.Lock()


def get_rollout_manager() -> RolloutManager:
    """Get or create global rollout manager (thread-safe)."""
    global _rollout_manager
    if _rollout_manager is None:
        with _rollout_lock:
            if _rollout_manager is None:
                _rollout_manager = RolloutManager()
    return _rollout_manager


def should_use_v1_api() -> bool:
    """
    Determine if v1 API should be used for this request.

    Called by PlacesClient factory to decide which API to use.
    """
    manager = get_rollout_manager()
    return manager.should_use_v1_api()


def get_rollout_status() -> Dict[str, Any]:
    """Get current rollout status for monitoring."""
    manager = get_rollout_manager()
    return manager.get_status()
