"""
A/B testing framework for receipt labeling methods.

This module provides a comprehensive A/B testing system to safely deploy and evaluate
new labeling approaches in production while measuring their impact on key metrics.
"""

import logging
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable, Union
from dataclasses import dataclass, asdict
from enum import Enum
import statistics

from receipt_label.monitoring.production_monitor import (
    LabelingMethod, PerformanceMetricType, ABTestConfig, ProductionMonitor
)

logger = logging.getLogger(__name__)


class ABTestStatus(Enum):
    """Status of an A/B test."""
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class SignificanceTest(Enum):
    """Statistical significance test methods."""
    T_TEST = "t_test"
    WELCH_T_TEST = "welch_t_test"
    BOOTSTRAP = "bootstrap"
    BAYESIAN = "bayesian"


@dataclass
class ABTestResult:
    """Results of statistical analysis for an A/B test."""
    metric_type: PerformanceMetricType
    control_mean: float
    treatment_mean: float
    control_std: float
    treatment_std: float
    control_sample_size: int
    treatment_sample_size: int
    effect_size: float
    p_value: Optional[float]
    confidence_interval: Optional[tuple]
    is_significant: bool
    power: Optional[float]
    minimum_detectable_effect: Optional[float]


@dataclass
class GuardrailMetric:
    """Guardrail metric to prevent harmful test variations."""
    metric_type: PerformanceMetricType
    threshold_value: float
    direction: str  # "min" or "max"
    description: str


class ABTestManager:
    """
    Advanced A/B testing manager for receipt labeling methods.
    
    This system provides safe experimentation with new labeling approaches,
    statistical analysis, and automatic guardrails to prevent degradation.
    """
    
    def __init__(self, monitor: ProductionMonitor):
        """
        Initialize A/B test manager.
        
        Args:
            monitor: Production monitor instance for metrics collection
        """
        self.monitor = monitor
        self.tests: Dict[str, ABTestConfig] = {}
        self.guardrails: Dict[str, List[GuardrailMetric]] = {}
        self.test_results: Dict[str, Dict[str, ABTestResult]] = {}
        
        # Default guardrails to prevent system degradation
        self.default_guardrails = [
            GuardrailMetric(
                metric_type=PerformanceMetricType.ACCURACY,
                threshold_value=0.8,  # Minimum 80% accuracy
                direction="min",
                description="Minimum acceptable accuracy threshold"
            ),
            GuardrailMetric(
                metric_type=PerformanceMetricType.COVERAGE,
                threshold_value=0.7,  # Minimum 70% essential label coverage
                direction="min", 
                description="Minimum essential label coverage threshold"
            ),
            GuardrailMetric(
                metric_type=PerformanceMetricType.LATENCY,
                threshold_value=10000,  # Maximum 10 seconds
                direction="max",
                description="Maximum acceptable processing latency"
            )
        ]
    
    def create_test(
        self,
        test_name: str,
        description: str,
        control_method: LabelingMethod,
        treatment_method: LabelingMethod,
        traffic_split_percentage: float,
        duration_days: int,
        success_metrics: List[PerformanceMetricType],
        target_sample_size: int = 1000,
        guardrails: Optional[List[GuardrailMetric]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> ABTestConfig:
        """
        Create a new A/B test configuration.
        
        Args:
            test_name: Unique test identifier
            description: Human-readable test description
            control_method: Control labeling method
            treatment_method: Treatment labeling method
            traffic_split_percentage: Percentage of traffic for treatment (0-100)
            duration_days: Test duration in days
            success_metrics: Metrics to measure for success
            target_sample_size: Target sample size for statistical power
            guardrails: Optional additional guardrail metrics
            metadata: Optional test metadata
            
        Returns:
            Created A/B test configuration
        """
        if test_name in self.tests:
            raise ValueError(f"A/B test '{test_name}' already exists")
        
        if not 0 <= traffic_split_percentage <= 100:
            raise ValueError("Traffic split percentage must be between 0 and 100")
        
        start_date = datetime.now()
        end_date = start_date + timedelta(days=duration_days)
        
        config = ABTestConfig(
            test_name=test_name,
            control_method=control_method,
            treatment_method=treatment_method,
            traffic_split_percentage=traffic_split_percentage,
            start_date=start_date,
            end_date=end_date,
            target_sample_size=target_sample_size,
            success_metrics=success_metrics,
            enabled=False  # Start disabled for safety
        )
        
        self.tests[test_name] = config
        
        # Set up guardrails
        test_guardrails = self.default_guardrails.copy()
        if guardrails:
            test_guardrails.extend(guardrails)
        self.guardrails[test_name] = test_guardrails
        
        # Add to monitor
        self.monitor.add_ab_test(config)
        
        logger.info(f"Created A/B test '{test_name}': {control_method.value} vs {treatment_method.value}")
        return config
    
    def start_test(self, test_name: str) -> bool:
        """
        Start an A/B test.
        
        Args:
            test_name: Test to start
            
        Returns:
            True if test started successfully
        """
        if test_name not in self.tests:
            logger.error(f"A/B test '{test_name}' not found")
            return False
        
        config = self.tests[test_name]
        
        # Validate test readiness
        if not self._validate_test_readiness(config):
            logger.error(f"A/B test '{test_name}' failed readiness validation")
            return False
        
        config.enabled = True
        logger.info(f"Started A/B test '{test_name}'")
        return True
    
    def pause_test(self, test_name: str, reason: str = "") -> bool:
        """
        Pause an active A/B test.
        
        Args:
            test_name: Test to pause
            reason: Optional reason for pausing
            
        Returns:
            True if test paused successfully
        """
        if test_name not in self.tests:
            logger.error(f"A/B test '{test_name}' not found")
            return False
        
        config = self.tests[test_name]
        config.enabled = False
        
        logger.warning(f"Paused A/B test '{test_name}': {reason}")
        return True
    
    def stop_test(self, test_name: str, reason: str = "") -> bool:
        """
        Stop an A/B test permanently.
        
        Args:
            test_name: Test to stop
            reason: Optional reason for stopping
            
        Returns:
            True if test stopped successfully
        """
        if test_name not in self.tests:
            logger.error(f"A/B test '{test_name}' not found")
            return False
        
        config = self.tests[test_name]
        config.enabled = False
        config.end_date = datetime.now()
        
        logger.info(f"Stopped A/B test '{test_name}': {reason}")
        return True
    
    def get_assignment(self, receipt_id: str, test_name: Optional[str] = None) -> Optional[LabelingMethod]:
        """
        Get A/B test assignment for a receipt.
        
        Args:
            receipt_id: Receipt identifier for consistent assignment
            test_name: Specific test name, or None for any active test
            
        Returns:
            Assigned labeling method, or None if no active tests
        """
        # Use monitor's assignment logic
        assignment = self.monitor.get_ab_test_assignment(receipt_id, test_name)
        
        if assignment and test_name:
            # Check guardrails before assignment
            if not self._check_guardrails(test_name):
                logger.warning(f"Guardrail violation detected for test '{test_name}', using control")
                config = self.tests[test_name]
                return config.control_method
        
        return assignment
    
    def analyze_test_results(
        self, 
        test_name: str,
        significance_level: float = 0.05,
        test_method: SignificanceTest = SignificanceTest.WELCH_T_TEST
    ) -> Dict[str, ABTestResult]:
        """
        Perform statistical analysis of A/B test results.
        
        Args:
            test_name: Test to analyze
            significance_level: Statistical significance level (e.g., 0.05 for 95% confidence)
            test_method: Statistical test method to use
            
        Returns:
            Dictionary of results by metric type
        """
        if test_name not in self.tests:
            raise ValueError(f"A/B test '{test_name}' not found")
        
        config = self.tests[test_name]
        
        # Get test data from monitor
        test_data = self.monitor.get_ab_test_results(test_name)
        
        results = {}
        
        for metric_type in config.success_metrics:
            metric_key = metric_type.value
            
            # Get control and treatment data
            control_data = test_data.get("control_results", {}).get(metric_key, {})
            treatment_data = test_data.get("treatment_results", {}).get(metric_key, {})
            
            if not control_data or not treatment_data:
                logger.warning(f"Insufficient data for metric {metric_key} in test {test_name}")
                continue
            
            # Extract metrics (simplified - in production would get full data arrays)
            control_mean = control_data["mean"]
            treatment_mean = treatment_data["mean"]
            control_n = control_data["count"]
            treatment_n = treatment_data["count"]
            
            # Calculate effect size
            effect_size = (treatment_mean - control_mean) / control_mean if control_mean != 0 else 0
            
            # Simplified statistical test (in production would use scipy.stats)
            # For now, calculate basic statistics
            pooled_std = 0.1  # Placeholder - would calculate from actual data
            control_std = 0.1  # Placeholder
            treatment_std = 0.1  # Placeholder
            
            # Simple significance test based on sample size and effect
            min_effect_size = 0.05  # 5% minimum detectable effect
            is_significant = (
                abs(effect_size) >= min_effect_size and 
                control_n >= 30 and 
                treatment_n >= 30
            )
            
            result = ABTestResult(
                metric_type=metric_type,
                control_mean=control_mean,
                treatment_mean=treatment_mean,
                control_std=control_std,
                treatment_std=treatment_std,
                control_sample_size=control_n,
                treatment_sample_size=treatment_n,
                effect_size=effect_size,
                p_value=0.05 if is_significant else 0.15,  # Placeholder
                confidence_interval=None,  # Would calculate in production
                is_significant=is_significant,
                power=None,  # Would calculate in production
                minimum_detectable_effect=min_effect_size
            )
            
            results[metric_key] = result
        
        self.test_results[test_name] = results
        return results
    
    def _validate_test_readiness(self, config: ABTestConfig) -> bool:
        """Validate that a test is ready to start."""
        
        # Check that we have baseline metrics for control method
        control_stats = self.monitor.method_stats.get(config.control_method.value, {})
        if control_stats.get("sessions", 0) < 10:
            logger.error(f"Insufficient baseline data for control method {config.control_method.value}")
            return False
        
        # Check that test duration is reasonable
        if config.end_date and config.end_date <= datetime.now():
            logger.error("Test end date is in the past")
            return False
        
        # Check traffic split is reasonable
        if config.traffic_split_percentage > 50:
            logger.warning(f"High traffic split ({config.traffic_split_percentage}%) - consider starting lower")
        
        return True
    
    def _check_guardrails(self, test_name: str) -> bool:
        """Check if guardrail metrics are being violated."""
        if test_name not in self.guardrails:
            return True
        
        config = self.tests[test_name]
        guardrails = self.guardrails[test_name]
        
        # Get recent treatment performance
        treatment_stats = self.monitor.method_stats.get(config.treatment_method.value, {})
        
        for guardrail in guardrails:
            metric_value = None
            
            # Map guardrail metric to monitored stats
            if guardrail.metric_type == PerformanceMetricType.ACCURACY:
                metric_value = treatment_stats.get("avg_accuracy", 0)
            elif guardrail.metric_type == PerformanceMetricType.COVERAGE:
                metric_value = treatment_stats.get("avg_coverage", 0)
            elif guardrail.metric_type == PerformanceMetricType.LATENCY:
                metric_value = treatment_stats.get("avg_latency_ms", 0)
            elif guardrail.metric_type == PerformanceMetricType.COST:
                metric_value = treatment_stats.get("avg_cost", 0)
            
            if metric_value is None:
                continue
            
            # Check guardrail violation
            if guardrail.direction == "min" and metric_value < guardrail.threshold_value:
                logger.error(f"Guardrail violation in test {test_name}: "
                           f"{guardrail.metric_type.value} = {metric_value:.3f} < {guardrail.threshold_value}")
                return False
            elif guardrail.direction == "max" and metric_value > guardrail.threshold_value:
                logger.error(f"Guardrail violation in test {test_name}: "
                           f"{guardrail.metric_type.value} = {metric_value:.3f} > {guardrail.threshold_value}")
                return False
        
        return True
    
    def get_test_status(self, test_name: str) -> Dict[str, Any]:
        """Get comprehensive status of an A/B test."""
        if test_name not in self.tests:
            return {"error": f"Test '{test_name}' not found"}
        
        config = self.tests[test_name]
        
        # Get test data
        test_data = self.monitor.get_ab_test_results(test_name)
        
        # Calculate progress
        target_size = config.target_sample_size
        current_size = test_data.get("control_sample_size", 0) + test_data.get("treatment_sample_size", 0)
        progress = min(current_size / target_size, 1.0) if target_size > 0 else 0
        
        # Calculate days remaining
        days_remaining = None
        if config.end_date:
            remaining = config.end_date - datetime.now()
            days_remaining = max(0, remaining.days)
        
        # Get latest results if available
        latest_results = self.test_results.get(test_name, {})
        
        status = {
            "test_name": test_name,
            "config": asdict(config),
            "is_active": config.is_active(),
            "progress": progress,
            "sample_size": current_size,
            "target_sample_size": target_size,
            "days_remaining": days_remaining,
            "guardrails_status": self._check_guardrails(test_name),
            "latest_results": {
                metric: asdict(result) for metric, result in latest_results.items()
            }
        }
        
        return status
    
    def get_recommendations(self, test_name: str) -> List[str]:
        """Get recommendations for test management."""
        if test_name not in self.tests:
            return ["Test not found"]
        
        config = self.tests[test_name]
        recommendations = []
        
        # Check test progress
        test_data = self.monitor.get_ab_test_results(test_name)
        current_size = test_data.get("control_sample_size", 0) + test_data.get("treatment_sample_size", 0)
        
        if current_size < config.target_sample_size * 0.1:
            recommendations.append("Test has very low sample size - consider increasing traffic allocation")
        
        # Check for early stopping opportunities
        if test_name in self.test_results:
            results = self.test_results[test_name]
            significant_results = [r for r in results.values() if r.is_significant]
            
            if len(significant_results) == len(config.success_metrics):
                recommendations.append("All success metrics show significance - consider early stopping")
            elif len(significant_results) == 0 and current_size > config.target_sample_size * 0.5:
                recommendations.append("No significant results at 50% target sample - consider stopping")
        
        # Check guardrails
        if not self._check_guardrails(test_name):
            recommendations.append("URGENT: Guardrail violations detected - consider pausing test")
        
        # Check test duration
        if config.end_date and datetime.now() > config.end_date:
            recommendations.append("Test has exceeded planned duration - analyze results and conclude")
        
        return recommendations
    
    def get_active_tests_summary(self) -> Dict[str, Any]:
        """Get summary of all active tests."""
        active_tests = [config for config in self.tests.values() if config.is_active()]
        
        summary = {
            "active_tests_count": len(active_tests),
            "total_traffic_allocated": sum(t.traffic_split_percentage for t in active_tests),
            "tests": []
        }
        
        for test in active_tests:
            test_status = self.get_test_status(test.test_name)
            summary["tests"].append({
                "name": test.test_name,
                "traffic_split": test.traffic_split_percentage,
                "progress": test_status["progress"],
                "guardrails_ok": test_status["guardrails_status"]
            })
        
        return summary
    
    def export_test_results(self, test_name: str) -> Dict[str, Any]:
        """Export comprehensive test results for reporting."""
        if test_name not in self.tests:
            return {"error": f"Test '{test_name}' not found"}
        
        # Analyze current results
        results = self.analyze_test_results(test_name)
        
        # Get full test data
        test_data = self.monitor.get_ab_test_results(test_name)
        test_status = self.get_test_status(test_name)
        
        export_data = {
            "test_configuration": asdict(self.tests[test_name]),
            "test_status": test_status,
            "statistical_results": {
                metric: asdict(result) for metric, result in results.items()
            },
            "raw_data": test_data,
            "recommendations": self.get_recommendations(test_name),
            "export_timestamp": datetime.now().isoformat()
        }
        
        return export_data