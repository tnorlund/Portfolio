"""
Production monitoring system for receipt labeling performance.

This module provides comprehensive monitoring capabilities for tracking the performance
and cost effectiveness of the agent-based receipt labeling system in production.
"""

import json
import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union
from enum import Enum
import uuid

from receipt_label.monitoring.token_ledger import TokenLedger, ModelTier, TokenUsageType

logger = logging.getLogger(__name__)


class LabelingMethod(Enum):
    """Different labeling methods being monitored."""
    PATTERN_ONLY = "pattern_only"
    AGENT_BASED = "agent_based"
    FULL_GPT = "full_gpt"
    HYBRID = "hybrid"


class PerformanceMetricType(Enum):
    """Types of performance metrics."""
    ACCURACY = "accuracy"
    COVERAGE = "coverage"
    COST = "cost"
    LATENCY = "latency"
    THROUGHPUT = "throughput"


@dataclass
class LabelingSession:
    """Represents a single receipt labeling session."""
    session_id: str
    receipt_id: str
    image_id: str
    method: LabelingMethod
    merchant_name: Optional[str]
    merchant_category: Optional[str]
    
    # Timing metrics
    start_time: datetime
    end_time: Optional[datetime] = None
    pattern_detection_time_ms: Optional[float] = None
    gpt_processing_time_ms: Optional[float] = None
    total_time_ms: Optional[float] = None
    
    # Latency bucketing metrics
    pattern_time_ms: Optional[float] = None  # Pattern detection latency
    pinecone_time_ms: Optional[float] = None  # Pinecone query latency
    gpt_time_ms: Optional[float] = None  # GPT processing latency
    
    # Quality metrics
    labels_found: int = 0
    pattern_labels: int = 0
    gpt_labels: int = 0
    essential_labels_coverage: float = 0.0
    accuracy_score: Optional[float] = None
    
    # Cost metrics
    gpt_tokens_used: int = 0
    estimated_cost_usd: float = 0.0
    
    # Decision engine metrics
    gpt_decision: Optional[str] = None
    gpt_decision_reason: Optional[str] = None
    unlabeled_words_count: int = 0
    missing_fields: List[str] = None
    
    # Error tracking
    errors: List[str] = None
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.missing_fields is None:
            self.missing_fields = []
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []
    
    def finish(self, end_time: Optional[datetime] = None):
        """Mark the session as finished and calculate total time."""
        self.end_time = end_time or datetime.now()
        if self.start_time:
            self.total_time_ms = (self.end_time - self.start_time).total_seconds() * 1000
    
    def add_error(self, error: str):
        """Add an error to the session."""
        self.errors.append(error)
        logger.error(f"Session {self.session_id}: {error}")
    
    def add_warning(self, warning: str):
        """Add a warning to the session."""
        self.warnings.append(warning)
        logger.warning(f"Session {self.session_id}: {warning}")


@dataclass
class PerformanceMetric:
    """A single performance metric measurement."""
    metric_type: PerformanceMetricType
    method: LabelingMethod
    value: float
    timestamp: datetime
    metadata: Dict[str, Any]
    session_id: Optional[str] = None


@dataclass
class ABTestConfig:
    """Configuration for A/B testing."""
    test_name: str
    control_method: LabelingMethod
    treatment_method: LabelingMethod
    traffic_split_percentage: float  # 0.0 to 100.0
    start_date: datetime
    end_date: Optional[datetime]
    target_sample_size: int
    success_metrics: List[PerformanceMetricType]
    enabled: bool = True
    
    def is_active(self) -> bool:
        """Check if the A/B test is currently active."""
        now = datetime.now()
        return (
            self.enabled and 
            self.start_date <= now and 
            (self.end_date is None or now <= self.end_date)
        )


class ProductionMonitor:
    """
    Comprehensive production monitoring system for receipt labeling.
    
    This system tracks performance metrics, manages A/B tests, and provides
    insights into the effectiveness of different labeling approaches.
    """
    
    def __init__(self, storage_backend: Optional[Any] = None):
        """
        Initialize the production monitor.
        
        Args:
            storage_backend: Optional backend for persistent storage (DynamoDB, etc.)
        """
        self.storage_backend = storage_backend
        self.active_sessions: Dict[str, LabelingSession] = {}
        self.metrics_buffer: List[PerformanceMetric] = []
        self.ab_tests: Dict[str, ABTestConfig] = {}
        
        # Initialize token ledger for detailed cost tracking
        self.token_ledger = TokenLedger(storage_backend)
        
        # Performance tracking
        self.stats = {
            "sessions_started": 0,
            "sessions_completed": 0,
            "total_processing_time_ms": 0,
            "total_gpt_tokens": 0,
            "total_cost_usd": 0.0,
            "errors_count": 0,
            "warnings_count": 0,
        }
        
        # Method-specific statistics
        self.method_stats = {method.value: {
            "sessions": 0,
            "avg_accuracy": 0.0,
            "avg_coverage": 0.0,
            "avg_cost": 0.0,
            "avg_latency_ms": 0.0,
            "error_rate": 0.0
        } for method in LabelingMethod}
    
    def start_session(
        self,
        receipt_id: str,
        image_id: str,
        method: LabelingMethod,
        merchant_name: Optional[str] = None,
        merchant_category: Optional[str] = None
    ) -> str:
        """
        Start a new labeling session.
        
        Args:
            receipt_id: Unique receipt identifier
            image_id: Unique image identifier
            method: Labeling method being used
            merchant_name: Optional merchant name
            merchant_category: Optional merchant category
            
        Returns:
            Session ID for tracking
        """
        session_id = str(uuid.uuid4())
        
        session = LabelingSession(
            session_id=session_id,
            receipt_id=receipt_id,
            image_id=image_id,
            method=method,
            merchant_name=merchant_name,
            merchant_category=merchant_category,
            start_time=datetime.now()
        )
        
        self.active_sessions[session_id] = session
        self.stats["sessions_started"] += 1
        
        logger.debug(f"Started monitoring session {session_id} for receipt {receipt_id}")
        return session_id
    
    def update_session_timing(
        self,
        session_id: str,
        pattern_detection_time_ms: Optional[float] = None,
        gpt_processing_time_ms: Optional[float] = None,
        pattern_time_ms: Optional[float] = None,
        pinecone_time_ms: Optional[float] = None,
        gpt_time_ms: Optional[float] = None
    ):
        """Update timing metrics for a session."""
        if session_id not in self.active_sessions:
            logger.warning(f"Session {session_id} not found for timing update")
            return
        
        session = self.active_sessions[session_id]
        
        # Legacy timing metrics (kept for backward compatibility)
        if pattern_detection_time_ms is not None:
            session.pattern_detection_time_ms = pattern_detection_time_ms
        if gpt_processing_time_ms is not None:
            session.gpt_processing_time_ms = gpt_processing_time_ms
        
        # Latency bucketing metrics
        if pattern_time_ms is not None:
            session.pattern_time_ms = pattern_time_ms
        if pinecone_time_ms is not None:
            session.pinecone_time_ms = pinecone_time_ms
        if gpt_time_ms is not None:
            session.gpt_time_ms = gpt_time_ms
    
    def update_session_quality(
        self,
        session_id: str,
        labels_found: int,
        pattern_labels: int,
        gpt_labels: int,
        essential_labels_coverage: float,
        accuracy_score: Optional[float] = None
    ):
        """Update quality metrics for a session."""
        if session_id not in self.active_sessions:
            logger.warning(f"Session {session_id} not found for quality update")
            return
        
        session = self.active_sessions[session_id]
        session.labels_found = labels_found
        session.pattern_labels = pattern_labels
        session.gpt_labels = gpt_labels
        session.essential_labels_coverage = essential_labels_coverage
        session.accuracy_score = accuracy_score
    
    def update_session_cost(
        self,
        session_id: str,
        gpt_tokens_used: int,
        estimated_cost_usd: float
    ):
        """Update cost metrics for a session."""
        if session_id not in self.active_sessions:
            logger.warning(f"Session {session_id} not found for cost update")
            return
        
        session = self.active_sessions[session_id]
        session.gpt_tokens_used = gpt_tokens_used
        session.estimated_cost_usd = estimated_cost_usd
    
    def log_token_usage(
        self,
        session_id: str,
        model_tier: ModelTier,
        usage_type: TokenUsageType,
        prompt_tokens: int,
        completion_tokens: int,
        agent_decision: str,
        missing_fields: Optional[List[str]] = None,
        batch_id: Optional[str] = None,
        labels_extracted: int = 0,
        essential_labels_found: int = 0,
        accuracy_score: Optional[float] = None,
        retry_count: int = 0,
        error_message: Optional[str] = None
    ) -> str:
        """
        Log detailed token usage for cost tracking.
        
        Args:
            session_id: Monitoring session ID
            model_tier: GPT model used
            usage_type: Type of usage (immediate, batch, shadow test)
            prompt_tokens: Number of prompt tokens
            completion_tokens: Number of completion tokens
            agent_decision: Original agent decision
            missing_fields: Fields that were missing and requested
            batch_id: Batch identifier for batch processing
            labels_extracted: Number of labels extracted by GPT
            essential_labels_found: Number of essential labels found
            accuracy_score: Quality score if available
            retry_count: Number of retries for this request
            error_message: Error message if request failed
            
        Returns:
            Token usage record ID
        """
        if session_id not in self.active_sessions:
            logger.warning(f"Session {session_id} not found for token usage logging")
            return ""
        
        session = self.active_sessions[session_id]
        
        # Log to token ledger
        record_id = self.token_ledger.log_token_usage(
            session_id=session_id,
            receipt_id=session.receipt_id,
            image_id=session.image_id,
            model_tier=model_tier,
            usage_type=usage_type,
            agent_decision=agent_decision,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            missing_fields=missing_fields,
            merchant_name=session.merchant_name,
            merchant_category=session.merchant_category,
            batch_id=batch_id,
            labels_extracted=labels_extracted,
            essential_labels_found=essential_labels_found,
            accuracy_score=accuracy_score,
            retry_count=retry_count,
            error_message=error_message
        )
        
        # Update session cost metrics
        total_tokens = prompt_tokens + completion_tokens
        cost_breakdown = self.token_ledger.calculate_cost(
            model_tier, prompt_tokens, completion_tokens
        )
        
        session.gpt_tokens_used += total_tokens
        session.estimated_cost_usd += cost_breakdown["total_cost"]
        
        # Update global statistics
        self.stats["total_gpt_tokens"] += total_tokens
        self.stats["total_cost_usd"] += cost_breakdown["total_cost"]
        
        return record_id
    
    def update_session_decision(
        self,
        session_id: str,
        gpt_decision: str,
        gpt_decision_reason: str,
        unlabeled_words_count: int,
        missing_fields: List[str]
    ):
        """Update decision engine metrics for a session."""
        if session_id not in self.active_sessions:
            logger.warning(f"Session {session_id} not found for decision update")
            return
        
        session = self.active_sessions[session_id]
        session.gpt_decision = gpt_decision
        session.gpt_decision_reason = gpt_decision_reason
        session.unlabeled_words_count = unlabeled_words_count
        session.missing_fields = missing_fields.copy()
    
    def finish_session(self, session_id: str, success: bool = True) -> Optional[LabelingSession]:
        """
        Finish a labeling session and calculate final metrics.
        
        Args:
            session_id: Session to finish
            success: Whether the session completed successfully
            
        Returns:
            Completed session object, or None if session not found
        """
        if session_id not in self.active_sessions:
            logger.warning(f"Session {session_id} not found for completion")
            return None
        
        session = self.active_sessions.pop(session_id)
        session.finish()
        
        if not success:
            session.add_error("Session completed with failure")
        
        # Update global statistics
        self.stats["sessions_completed"] += 1
        if session.total_time_ms:
            self.stats["total_processing_time_ms"] += session.total_time_ms
        self.stats["total_gpt_tokens"] += session.gpt_tokens_used
        self.stats["total_cost_usd"] += session.estimated_cost_usd
        self.stats["errors_count"] += len(session.errors)
        self.stats["warnings_count"] += len(session.warnings)
        
        # Update method-specific statistics
        self._update_method_stats(session)
        
        # Generate performance metrics
        self._generate_session_metrics(session)
        
        # Store session if backend available
        if self.storage_backend:
            self._store_session(session)
        
        logger.debug(f"Completed monitoring session {session_id}")
        return session
    
    def _update_method_stats(self, session: LabelingSession):
        """Update statistics for the session's labeling method."""
        method_key = session.method.value
        stats = self.method_stats[method_key]
        
        # Update session count
        stats["sessions"] += 1
        session_count = stats["sessions"]
        
        # Update running averages
        if session.accuracy_score is not None:
            stats["avg_accuracy"] = (
                (stats["avg_accuracy"] * (session_count - 1) + session.accuracy_score) 
                / session_count
            )
        
        stats["avg_coverage"] = (
            (stats["avg_coverage"] * (session_count - 1) + session.essential_labels_coverage)
            / session_count
        )
        
        stats["avg_cost"] = (
            (stats["avg_cost"] * (session_count - 1) + session.estimated_cost_usd)
            / session_count
        )
        
        if session.total_time_ms:
            stats["avg_latency_ms"] = (
                (stats["avg_latency_ms"] * (session_count - 1) + session.total_time_ms)
                / session_count
            )
        
        # Update error rate
        has_errors = len(session.errors) > 0
        stats["error_rate"] = (
            (stats["error_rate"] * (session_count - 1) + (1.0 if has_errors else 0.0))
            / session_count
        )
    
    def _generate_session_metrics(self, session: LabelingSession):
        """Generate performance metrics from a completed session."""
        timestamp = session.end_time or datetime.now()
        
        # Accuracy metric
        if session.accuracy_score is not None:
            self.metrics_buffer.append(PerformanceMetric(
                metric_type=PerformanceMetricType.ACCURACY,
                method=session.method,
                value=session.accuracy_score,
                timestamp=timestamp,
                metadata={"merchant_category": session.merchant_category},
                session_id=session.session_id
            ))
        
        # Coverage metric
        self.metrics_buffer.append(PerformanceMetric(
            metric_type=PerformanceMetricType.COVERAGE,
            method=session.method,
            value=session.essential_labels_coverage,
            timestamp=timestamp,
            metadata={"merchant_category": session.merchant_category},
            session_id=session.session_id
        ))
        
        # Cost metric
        self.metrics_buffer.append(PerformanceMetric(
            metric_type=PerformanceMetricType.COST,
            method=session.method,
            value=session.estimated_cost_usd,
            timestamp=timestamp,
            metadata={"gpt_tokens": session.gpt_tokens_used},
            session_id=session.session_id
        ))
        
        # Latency metric
        if session.total_time_ms:
            self.metrics_buffer.append(PerformanceMetric(
                metric_type=PerformanceMetricType.LATENCY,
                method=session.method,
                value=session.total_time_ms,
                timestamp=timestamp,
                metadata={},
                session_id=session.session_id
            ))
    
    def _store_session(self, session: LabelingSession):
        """Store session data using the configured backend."""
        try:
            # Convert to dictionary for storage
            session_data = asdict(session)
            
            # Convert datetime objects to ISO strings
            session_data["start_time"] = session.start_time.isoformat()
            if session.end_time:
                session_data["end_time"] = session.end_time.isoformat()
            
            # Store using backend
            self.storage_backend.store_session(session_data)
            
        except Exception as e:
            logger.error(f"Failed to store session {session.session_id}: {e}")
    
    def add_ab_test(self, config: ABTestConfig):
        """Add a new A/B test configuration."""
        self.ab_tests[config.test_name] = config
        logger.info(f"Added A/B test: {config.test_name}")
    
    def get_ab_test_assignment(
        self, 
        receipt_id: str, 
        test_name: Optional[str] = None
    ) -> Optional[LabelingMethod]:
        """
        Get A/B test assignment for a receipt.
        
        Args:
            receipt_id: Receipt identifier for consistent assignment
            test_name: Specific test name, or None for any active test
            
        Returns:
            Assigned labeling method, or None if no active tests
        """
        # Find active test
        active_test = None
        if test_name and test_name in self.ab_tests:
            test_config = self.ab_tests[test_name]
            if test_config.is_active():
                active_test = test_config
        else:
            # Find any active test
            for test_config in self.ab_tests.values():
                if test_config.is_active():
                    active_test = test_config
                    break
        
        if not active_test:
            return None
        
        # Consistent assignment based on receipt ID hash
        import hashlib
        hash_value = int(hashlib.md5(receipt_id.encode()).hexdigest()[:8], 16)
        percentage = (hash_value % 100) + 1
        
        if percentage <= active_test.traffic_split_percentage:
            return active_test.treatment_method
        else:
            return active_test.control_method
    
    def get_performance_summary(
        self, 
        method: Optional[LabelingMethod] = None,
        hours_lookback: int = 24
    ) -> Dict[str, Any]:
        """
        Get performance summary for monitoring dashboards.
        
        Args:
            method: Optional filter by labeling method
            hours_lookback: Hours to look back for recent metrics
            
        Returns:
            Performance summary dictionary
        """
        summary = {
            "global_stats": self.stats.copy(),
            "method_stats": {},
            "recent_metrics": {},
            "active_sessions": len(self.active_sessions),
            "active_ab_tests": len([t for t in self.ab_tests.values() if t.is_active()])
        }
        
        # Method-specific stats
        if method:
            summary["method_stats"] = {method.value: self.method_stats[method.value]}
        else:
            summary["method_stats"] = self.method_stats.copy()
        
        # Recent metrics analysis
        cutoff_time = datetime.now() - timedelta(hours=hours_lookback)
        recent_metrics = [m for m in self.metrics_buffer if m.timestamp >= cutoff_time]
        
        if recent_metrics:
            # Group by metric type
            for metric_type in PerformanceMetricType:
                type_metrics = [m for m in recent_metrics if m.metric_type == metric_type]
                if type_metrics:
                    values = [m.value for m in type_metrics]
                    summary["recent_metrics"][metric_type.value] = {
                        "count": len(values),
                        "avg": sum(values) / len(values),
                        "min": min(values),
                        "max": max(values)
                    }
        
        # Add token usage summary
        summary["token_usage"] = self.token_ledger.get_statistics()
        
        return summary
    
    def get_ab_test_results(self, test_name: str) -> Dict[str, Any]:
        """Get results for a specific A/B test."""
        if test_name not in self.ab_tests:
            return {"error": f"A/B test '{test_name}' not found"}
        
        test_config = self.ab_tests[test_name]
        
        # Filter metrics for this test period
        test_metrics = [
            m for m in self.metrics_buffer 
            if (m.timestamp >= test_config.start_date and
                (test_config.end_date is None or m.timestamp <= test_config.end_date))
        ]
        
        # Group by method
        control_metrics = [m for m in test_metrics if m.method == test_config.control_method]
        treatment_metrics = [m for m in test_metrics if m.method == test_config.treatment_method]
        
        results = {
            "test_config": asdict(test_config),
            "control_sample_size": len(control_metrics),
            "treatment_sample_size": len(treatment_metrics),
            "control_results": {},
            "treatment_results": {},
            "statistical_significance": {}
        }
        
        # Calculate results for each success metric
        for metric_type in test_config.success_metrics:
            control_values = [m.value for m in control_metrics if m.metric_type == metric_type]
            treatment_values = [m.value for m in treatment_metrics if m.metric_type == metric_type]
            
            if control_values:
                results["control_results"][metric_type.value] = {
                    "mean": sum(control_values) / len(control_values),
                    "count": len(control_values)
                }
            
            if treatment_values:
                results["treatment_results"][metric_type.value] = {
                    "mean": sum(treatment_values) / len(treatment_values),
                    "count": len(treatment_values)
                }
        
        return results
    
    def flush_metrics(self):
        """Flush metrics buffer to storage backend."""
        if self.storage_backend and self.metrics_buffer:
            try:
                # Convert metrics to storage format
                metrics_data = []
                for metric in self.metrics_buffer:
                    metric_data = asdict(metric)
                    metric_data["timestamp"] = metric.timestamp.isoformat()
                    metric_data["metric_type"] = metric.metric_type.value
                    metric_data["method"] = metric.method.value
                    metrics_data.append(metric_data)
                
                self.storage_backend.store_metrics(metrics_data)
                self.metrics_buffer.clear()
                
                logger.debug(f"Flushed {len(metrics_data)} metrics to storage")
                
            except Exception as e:
                logger.error(f"Failed to flush metrics to storage: {e}")
        
        # Also flush token usage records
        if self.token_ledger:
            try:
                self.token_ledger.flush_to_storage()
            except Exception as e:
                logger.error(f"Failed to flush token usage records: {e}")
    
    def get_token_usage_summary(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        usage_type: Optional[TokenUsageType] = None,
        model_tier: Optional[ModelTier] = None
    ) -> Dict[str, Any]:
        """
        Get detailed token usage summary.
        
        Args:
            start_date: Start date for filtering
            end_date: End date for filtering  
            usage_type: Filter by usage type
            model_tier: Filter by model tier
            
        Returns:
            Token usage summary
        """
        return self.token_ledger.get_usage_summary(
            start_date=start_date,
            end_date=end_date,
            usage_type=usage_type,
            model_tier=model_tier
        )
    
    def generate_cost_report(self, days: int = 30) -> str:
        """
        Generate human-readable cost report.
        
        Args:
            days: Number of days to include in report
            
        Returns:
            Formatted cost report
        """
        return self.token_ledger.generate_cost_report(days=days)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive monitoring statistics."""
        stats = {
            "global_stats": self.stats.copy(),
            "method_stats": self.method_stats.copy(),
            "active_sessions": len(self.active_sessions),
            "metrics_buffer_size": len(self.metrics_buffer),
            "ab_tests": len(self.ab_tests),
            "active_ab_tests": len([t for t in self.ab_tests.values() if t.is_active()])
        }
        
        # Add token usage statistics
        if self.token_ledger:
            stats["token_usage"] = self.token_ledger.get_statistics()
        
        return stats