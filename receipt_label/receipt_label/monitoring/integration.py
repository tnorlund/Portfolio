"""
Integration layer for production monitoring and A/B testing.

This module provides seamless integration of monitoring and A/B testing
with the existing receipt labeling system.
"""

import logging
import time
from contextlib import contextmanager
from typing import Dict, List, Optional, Any, Union
from datetime import datetime

from receipt_label.monitoring.production_monitor import (
    ProductionMonitor, LabelingMethod, LabelingSession
)
from receipt_label.monitoring.ab_testing import ABTestManager
from receipt_label.agent.decision_engine import DecisionEngine, GPTDecision

logger = logging.getLogger(__name__)


class MonitoredLabelingSystem:
    """
    Wrapper that adds comprehensive monitoring to the receipt labeling system.
    
    This class integrates production monitoring and A/B testing with existing
    labeling components while maintaining backward compatibility.
    """
    
    def __init__(
        self, 
        decision_engine: DecisionEngine,
        pattern_orchestrator: Any,
        storage_backend: Optional[Any] = None,
        enable_ab_testing: bool = True
    ):
        """
        Initialize monitored labeling system.
        
        Args:
            decision_engine: Decision engine instance
            pattern_orchestrator: Pattern detection orchestrator
            storage_backend: Optional storage backend for persistence
            enable_ab_testing: Whether to enable A/B testing
        """
        self.decision_engine = decision_engine
        self.pattern_orchestrator = pattern_orchestrator
        
        # Initialize monitoring
        self.monitor = ProductionMonitor(storage_backend)
        
        # Initialize A/B testing if enabled
        self.ab_test_manager = ABTestManager(self.monitor) if enable_ab_testing else None
        
        # Default labeling method
        self.default_method = LabelingMethod.AGENT_BASED
        
        # Statistics
        self.stats = {
            "receipts_processed": 0,
            "ab_tests_active": 0,
            "monitoring_sessions_created": 0,
            "errors_encountered": 0
        }
    
    @contextmanager
    def monitored_session(
        self,
        receipt_id: str,
        image_id: str,
        merchant_name: Optional[str] = None,
        merchant_category: Optional[str] = None,
        method_override: Optional[LabelingMethod] = None
    ):
        """
        Context manager for monitored labeling sessions.
        
        Args:
            receipt_id: Unique receipt identifier
            image_id: Unique image identifier  
            merchant_name: Optional merchant name
            merchant_category: Optional merchant category
            method_override: Optional method override (for testing)
            
        Yields:
            MonitoredSession object for tracking metrics
        """
        # Determine labeling method
        method = method_override or self._get_labeling_method(receipt_id)
        
        # Start monitoring session
        session_id = self.monitor.start_session(
            receipt_id=receipt_id,
            image_id=image_id,
            method=method,
            merchant_name=merchant_name,
            merchant_category=merchant_category
        )
        
        self.stats["monitoring_sessions_created"] += 1
        
        try:
            # Create session wrapper
            session = MonitoredSession(
                session_id=session_id,
                monitor=self.monitor,
                method=method
            )
            
            yield session
            
            # Mark session as successful
            completed_session = self.monitor.finish_session(session_id, success=True)
            if completed_session:
                logger.debug(f"Completed monitoring session {session_id} successfully")
        
        except Exception as e:
            # Mark session as failed
            self.monitor.finish_session(session_id, success=False)
            self.stats["errors_encountered"] += 1
            logger.error(f"Monitoring session {session_id} failed: {e}")
            raise
    
    def _get_labeling_method(self, receipt_id: str) -> LabelingMethod:
        """Determine labeling method for a receipt (including A/B test assignment)."""
        
        # Check for A/B test assignment
        if self.ab_test_manager:
            ab_assignment = self.ab_test_manager.get_assignment(receipt_id)
            if ab_assignment:
                return ab_assignment
        
        # Use default method
        return self.default_method
    
    async def process_receipt_with_monitoring(
        self,
        receipt_words: List[Dict],
        receipt_metadata: Dict[str, Any],
        method_override: Optional[LabelingMethod] = None
    ) -> Dict[str, Any]:
        """
        Process a receipt with full monitoring and A/B testing.
        
        Args:
            receipt_words: List of word dictionaries from receipt
            receipt_metadata: Receipt metadata including IDs and merchant info
            method_override: Optional method override for testing
            
        Returns:
            Dictionary containing labeling results and monitoring data
        """
        receipt_id = receipt_metadata.get("receipt_id", "unknown")
        image_id = receipt_metadata.get("image_id", "unknown")
        merchant_name = receipt_metadata.get("merchant_name")
        merchant_category = receipt_metadata.get("merchant_category")
        
        with self.monitored_session(
            receipt_id=receipt_id,
            image_id=image_id,
            merchant_name=merchant_name,
            merchant_category=merchant_category,
            method_override=method_override
        ) as session:
            
            # Time pattern detection
            pattern_start = time.time()
            
            # Run pattern detection with merchant enhancement
            labeled_words = await self.pattern_orchestrator.detect_with_merchant_enhancement(
                receipt_words,
                merchant_name=merchant_name,
                receipt_metadata=receipt_metadata
            )
            
            pattern_time_ms = (time.time() - pattern_start) * 1000
            session.update_timing(pattern_detection_time_ms=pattern_time_ms)
            
            # Run decision engine
            decision, reason, unlabeled_words, missing_fields = self.decision_engine.make_smart_gpt_decision(
                receipt_words, labeled_words, receipt_metadata
            )
            
            # Update session with decision metrics
            session.update_decision(
                gpt_decision=decision.value,
                reason=reason,
                unlabeled_words_count=len(unlabeled_words),
                missing_fields=missing_fields
            )
            
            # Calculate quality metrics
            essential_coverage = self._calculate_essential_coverage(labeled_words, receipt_metadata)
            pattern_labels_count = len(labeled_words)
            gpt_labels_count = 0
            gpt_time_ms = 0
            gpt_tokens = 0
            estimated_cost = 0.0
            
            # Handle GPT processing based on decision
            if decision == GPTDecision.REQUIRED:
                # Immediate GPT processing
                gpt_start = time.time()
                
                # In production, this would call actual GPT service
                # For now, simulate GPT processing
                await self._simulate_gpt_processing(missing_fields)
                
                gpt_time_ms = (time.time() - gpt_start) * 1000
                gpt_tokens = len(missing_fields) * 50  # Estimated tokens per field
                gpt_labels_count = len(missing_fields)
                estimated_cost = gpt_tokens * 0.00002  # Estimated cost per token
                
                session.update_timing(gpt_processing_time_ms=gpt_time_ms)
                session.update_cost(gpt_tokens_used=gpt_tokens, estimated_cost_usd=estimated_cost)
                
            elif decision == GPTDecision.BATCH:
                # Queue for batch processing - minimal cost
                estimated_cost = len(missing_fields) * 0.00001  # Lower cost for batch
                session.update_cost(gpt_tokens_used=0, estimated_cost_usd=estimated_cost)
            
            # Update quality metrics
            session.update_quality(
                labels_found=pattern_labels_count + gpt_labels_count,
                pattern_labels=pattern_labels_count,
                gpt_labels=gpt_labels_count,
                essential_labels_coverage=essential_coverage
            )
            
            # Compile results
            results = {
                "labeled_words": labeled_words,
                "gpt_decision": decision.value,
                "decision_reason": reason,
                "missing_fields": missing_fields,
                "unlabeled_words_count": len(unlabeled_words),
                "metrics": {
                    "pattern_detection_time_ms": pattern_time_ms,
                    "gpt_processing_time_ms": gpt_time_ms,
                    "essential_coverage": essential_coverage,
                    "estimated_cost_usd": estimated_cost,
                    "gpt_tokens_used": gpt_tokens
                },
                "session_id": session.session_id,
                "labeling_method": session.method.value
            }
            
            self.stats["receipts_processed"] += 1
            return results
    
    def _calculate_essential_coverage(
        self, 
        labeled_words: Dict[int, Dict],
        receipt_metadata: Dict[str, Any]
    ) -> float:
        """Calculate essential label coverage percentage."""
        
        # Get merchant-specific essential requirements
        merchant_name = receipt_metadata.get("merchant_name")
        merchant_category = receipt_metadata.get("merchant_category")
        
        essential_config, _ = self.decision_engine.merchant_essential_labels.get_essential_labels_for_merchant(
            merchant_name, merchant_category
        )
        
        total_essential = len(essential_config.all_labels)
        if total_essential == 0:
            return 1.0
        
        # Count found essential labels
        found_labels = {info.get("label") for info in labeled_words.values()}
        found_essential = found_labels & essential_config.all_labels
        
        return len(found_essential) / total_essential
    
    async def _simulate_gpt_processing(self, missing_fields: List[str]):
        """Simulate GPT processing for testing."""
        # Simulate processing time based on field count
        import asyncio
        processing_time = len(missing_fields) * 0.1  # 100ms per field
        await asyncio.sleep(processing_time)
    
    def setup_ab_test(
        self,
        test_name: str,
        description: str,
        control_method: LabelingMethod,
        treatment_method: LabelingMethod,
        traffic_split_percentage: float = 10.0,
        duration_days: int = 7,
        auto_start: bool = False
    ) -> bool:
        """
        Set up a new A/B test.
        
        Args:
            test_name: Unique test identifier
            description: Human-readable description
            control_method: Control labeling method
            treatment_method: Treatment labeling method
            traffic_split_percentage: Percentage for treatment group
            duration_days: Test duration in days
            auto_start: Whether to start the test immediately
            
        Returns:
            True if test was set up successfully
        """
        if not self.ab_test_manager:
            logger.error("A/B testing not enabled")
            return False
        
        try:
            from receipt_label.monitoring.production_monitor import PerformanceMetricType
            
            # Create test with standard success metrics
            config = self.ab_test_manager.create_test(
                test_name=test_name,
                description=description,
                control_method=control_method,
                treatment_method=treatment_method,
                traffic_split_percentage=traffic_split_percentage,
                duration_days=duration_days,
                success_metrics=[
                    PerformanceMetricType.ACCURACY,
                    PerformanceMetricType.COVERAGE,
                    PerformanceMetricType.COST
                ],
                target_sample_size=max(100, int(1000 * (traffic_split_percentage / 100)))
            )
            
            if auto_start:
                self.ab_test_manager.start_test(test_name)
            
            self.stats["ab_tests_active"] += 1
            logger.info(f"Set up A/B test '{test_name}' successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to set up A/B test '{test_name}': {e}")
            return False
    
    def get_monitoring_dashboard(self) -> Dict[str, Any]:
        """Get comprehensive monitoring dashboard data."""
        dashboard = {
            "system_stats": self.stats.copy(),
            "monitor_stats": self.monitor.get_statistics(),
            "decision_engine_stats": self.decision_engine.get_statistics(),
            "merchant_essential_stats": self.decision_engine.get_merchant_essential_labels_statistics(),
            "performance_summary": self.monitor.get_performance_summary(),
            "timestamp": datetime.now().isoformat()
        }
        
        # Add A/B testing data if available
        if self.ab_test_manager:
            dashboard["ab_testing"] = self.ab_test_manager.get_active_tests_summary()
        
        return dashboard
    
    def get_test_results(self, test_name: str) -> Dict[str, Any]:
        """Get results for a specific A/B test."""
        if not self.ab_test_manager:
            return {"error": "A/B testing not enabled"}
        
        return self.ab_test_manager.export_test_results(test_name)
    
    def flush_monitoring_data(self):
        """Flush monitoring data to persistent storage."""
        try:
            self.monitor.flush_metrics()
            logger.debug("Flushed monitoring data to storage")
        except Exception as e:
            logger.error(f"Failed to flush monitoring data: {e}")


class MonitoredSession:
    """
    Helper class for tracking metrics during a labeling session.
    """
    
    def __init__(self, session_id: str, monitor: ProductionMonitor, method: LabelingMethod):
        """Initialize monitored session."""
        self.session_id = session_id
        self.monitor = monitor
        self.method = method
    
    def update_timing(
        self,
        pattern_detection_time_ms: Optional[float] = None,
        gpt_processing_time_ms: Optional[float] = None
    ):
        """Update timing metrics."""
        self.monitor.update_session_timing(
            self.session_id,
            pattern_detection_time_ms,
            gpt_processing_time_ms
        )
    
    def update_quality(
        self,
        labels_found: int,
        pattern_labels: int,
        gpt_labels: int,
        essential_labels_coverage: float,
        accuracy_score: Optional[float] = None
    ):
        """Update quality metrics."""
        self.monitor.update_session_quality(
            self.session_id,
            labels_found,
            pattern_labels,
            gpt_labels,
            essential_labels_coverage,
            accuracy_score
        )
    
    def update_cost(self, gpt_tokens_used: int, estimated_cost_usd: float):
        """Update cost metrics."""
        self.monitor.update_session_cost(
            self.session_id,
            gpt_tokens_used,
            estimated_cost_usd
        )
    
    def update_decision(
        self,
        gpt_decision: str,
        reason: str,
        unlabeled_words_count: int,
        missing_fields: List[str]
    ):
        """Update decision engine metrics."""
        self.monitor.update_session_decision(
            self.session_id,
            gpt_decision,
            reason,
            unlabeled_words_count,
            missing_fields
        )
    
    def add_error(self, error: str):
        """Add an error to the session."""
        if self.session_id in self.monitor.active_sessions:
            session = self.monitor.active_sessions[self.session_id]
            session.add_error(error)
    
    def add_warning(self, warning: str):
        """Add a warning to the session."""
        if self.session_id in self.monitor.active_sessions:
            session = self.monitor.active_sessions[self.session_id]
            session.add_warning(warning)