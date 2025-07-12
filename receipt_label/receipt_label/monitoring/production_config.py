"""
Production configuration for monitoring and A/B testing.

This module provides pre-configured setups for common production monitoring
scenarios and A/B test configurations.
"""

from datetime import datetime, timedelta
from typing import Dict, List
from dataclasses import dataclass

from receipt_label.monitoring.production_monitor import LabelingMethod, PerformanceMetricType
from receipt_label.monitoring.ab_testing import GuardrailMetric


@dataclass
class ProductionConfig:
    """Production monitoring configuration."""
    
    # Monitoring settings
    enable_monitoring: bool = True
    enable_ab_testing: bool = True
    monitoring_sample_rate: float = 1.0  # Sample 100% of traffic
    
    # Storage settings
    metrics_flush_interval_minutes: int = 5
    session_retention_days: int = 30
    metrics_retention_days: int = 90
    
    # Performance thresholds
    max_processing_time_ms: float = 10000  # 10 seconds
    min_accuracy_threshold: float = 0.8    # 80%
    min_coverage_threshold: float = 0.7    # 70%
    max_cost_per_receipt: float = 0.05     # $0.05
    
    # A/B testing settings
    default_test_duration_days: int = 14
    max_concurrent_tests: int = 3
    default_traffic_split: float = 10.0    # 10% treatment


class StandardABTests:
    """Pre-configured A/B test scenarios for common experiments."""
    
    @staticmethod
    def pattern_vs_agent_test() -> Dict:
        """Test pattern-only vs agent-based labeling."""
        return {
            "test_name": "pattern_vs_agent_efficiency",
            "description": "Compare pure pattern detection vs agent-based decision making",
            "control_method": LabelingMethod.PATTERN_ONLY,
            "treatment_method": LabelingMethod.AGENT_BASED,
            "traffic_split_percentage": 20.0,
            "duration_days": 14,
            "success_metrics": [
                PerformanceMetricType.ACCURACY,
                PerformanceMetricType.COVERAGE,
                PerformanceMetricType.COST
            ],
            "guardrails": [
                GuardrailMetric(
                    metric_type=PerformanceMetricType.ACCURACY,
                    threshold_value=0.75,
                    direction="min",
                    description="Minimum 75% accuracy for safety"
                ),
                GuardrailMetric(
                    metric_type=PerformanceMetricType.LATENCY,
                    threshold_value=15000,  # 15 seconds
                    direction="max",
                    description="Maximum processing time threshold"
                )
            ]
        }
    
    @staticmethod
    def merchant_specific_test() -> Dict:
        """Test merchant-specific essential labels impact."""
        return {
            "test_name": "merchant_specific_labels",
            "description": "Evaluate impact of merchant-specific essential label requirements",
            "control_method": LabelingMethod.AGENT_BASED,
            "treatment_method": LabelingMethod.HYBRID,  # Agent + merchant patterns
            "traffic_split_percentage": 15.0,
            "duration_days": 21,
            "success_metrics": [
                PerformanceMetricType.COST,
                PerformanceMetricType.COVERAGE,
                PerformanceMetricType.THROUGHPUT
            ],
            "guardrails": [
                GuardrailMetric(
                    metric_type=PerformanceMetricType.COVERAGE,
                    threshold_value=0.7,
                    direction="min",
                    description="Maintain minimum coverage during test"
                ),
                GuardrailMetric(
                    metric_type=PerformanceMetricType.COST,
                    threshold_value=0.08,  # $0.08 per receipt
                    direction="max",
                    description="Prevent cost explosion"
                )
            ]
        }
    
    @staticmethod
    def batch_processing_test() -> Dict:
        """Test batch processing vs immediate GPT calls."""
        return {
            "test_name": "batch_vs_immediate_gpt",
            "description": "Compare batch processing vs immediate GPT for secondary labels",
            "control_method": LabelingMethod.AGENT_BASED,
            "treatment_method": LabelingMethod.HYBRID,
            "traffic_split_percentage": 25.0,
            "duration_days": 10,
            "success_metrics": [
                PerformanceMetricType.COST,
                PerformanceMetricType.LATENCY,
                PerformanceMetricType.ACCURACY
            ],
            "guardrails": [
                GuardrailMetric(
                    metric_type=PerformanceMetricType.ACCURACY,
                    threshold_value=0.8,
                    direction="min",
                    description="Accuracy must not degrade significantly"
                )
            ]
        }


class MonitoringAlerts:
    """Pre-configured monitoring alerts for production issues."""
    
    @staticmethod
    def get_alert_thresholds() -> Dict[str, Dict]:
        """Get standard alert thresholds for production monitoring."""
        return {
            "critical_alerts": {
                "accuracy_drop": {
                    "metric": PerformanceMetricType.ACCURACY,
                    "threshold": 0.6,  # Below 60% accuracy
                    "condition": "below",
                    "window_minutes": 15,
                    "description": "Critical accuracy degradation"
                },
                "cost_spike": {
                    "metric": PerformanceMetricType.COST,
                    "threshold": 0.15,  # Above $0.15 per receipt
                    "condition": "above", 
                    "window_minutes": 10,
                    "description": "Unexpected cost increase"
                },
                "processing_timeout": {
                    "metric": PerformanceMetricType.LATENCY,
                    "threshold": 30000,  # 30 seconds
                    "condition": "above",
                    "window_minutes": 5,
                    "description": "Processing time exceeding limits"
                }
            },
            
            "warning_alerts": {
                "coverage_decline": {
                    "metric": PerformanceMetricType.COVERAGE,
                    "threshold": 0.75,  # Below 75% coverage
                    "condition": "below",
                    "window_minutes": 30,
                    "description": "Label coverage declining"
                },
                "error_rate_increase": {
                    "metric": "error_rate",
                    "threshold": 0.05,  # Above 5% error rate
                    "condition": "above",
                    "window_minutes": 20,
                    "description": "Increased error rate detected"
                }
            },
            
            "info_alerts": {
                "traffic_anomaly": {
                    "metric": "throughput",
                    "threshold": 2.0,  # 2x normal traffic
                    "condition": "above",
                    "window_minutes": 60,
                    "description": "Unusual traffic pattern detected"
                }
            }
        }


class ProductionDeploymentStrategy:
    """Strategies for safely deploying new receipt labeling features."""
    
    @staticmethod
    def safe_rollout_phases() -> List[Dict]:
        """Get phases for safe production rollout."""
        return [
            {
                "phase": "canary",
                "name": "Canary Deployment",
                "traffic_percentage": 1.0,
                "duration_hours": 24,
                "success_criteria": {
                    "accuracy": 0.8,
                    "coverage": 0.7,
                    "error_rate": 0.02,
                    "latency_p95": 5000
                },
                "auto_rollback": True,
                "description": "Initial safety check with minimal traffic"
            },
            {
                "phase": "pilot", 
                "name": "Pilot Deployment",
                "traffic_percentage": 5.0,
                "duration_hours": 72,
                "success_criteria": {
                    "accuracy": 0.82,
                    "coverage": 0.75,
                    "error_rate": 0.015,
                    "cost_increase": 0.1  # Max 10% cost increase
                },
                "auto_rollback": True,
                "description": "Expanded testing with broader merchant coverage"
            },
            {
                "phase": "gradual",
                "name": "Gradual Rollout",
                "traffic_percentage": 25.0,
                "duration_hours": 168,  # 1 week
                "success_criteria": {
                    "accuracy": 0.85,
                    "coverage": 0.8,
                    "cost_reduction": 0.15  # Target 15% cost reduction
                },
                "auto_rollback": False,  # Manual oversight required
                "description": "Quarter traffic with full merchant diversity"
            },
            {
                "phase": "full",
                "name": "Full Deployment", 
                "traffic_percentage": 100.0,
                "duration_hours": None,  # Ongoing
                "success_criteria": {
                    "accuracy": 0.87,
                    "coverage": 0.85,
                    "cost_reduction": 0.2  # Target 20% cost reduction
                },
                "auto_rollback": False,
                "description": "Complete migration to new system"
            }
        ]
    
    @staticmethod
    def merchant_specific_rollout() -> Dict[str, List[str]]:
        """Get merchant-specific rollout strategy."""
        return {
            "phase_1_merchants": [
                "McDonald's", "Burger King", "Subway",  # Simple restaurants
                "Shell", "Exxon", "BP"  # Gas stations
            ],
            "phase_2_merchants": [
                "Walmart", "Target", "Kroger",  # Major retailers
                "Starbucks", "Dunkin", "Tim Hortons"  # Coffee shops
            ],
            "phase_3_merchants": [
                "CVS", "Walgreens", "Rite Aid",  # Pharmacies
                "Home Depot", "Lowe's", "Best Buy"  # Specialty retail
            ],
            "phase_4_merchants": [
                "*"  # All remaining merchants
            ]
        }


class DashboardConfig:
    """Configuration for monitoring dashboards and reports."""
    
    @staticmethod
    def get_dashboard_metrics() -> Dict[str, List[str]]:
        """Get metrics to display on monitoring dashboards."""
        return {
            "real_time_metrics": [
                "requests_per_minute",
                "avg_processing_time",
                "current_error_rate",
                "active_ab_tests"
            ],
            
            "performance_metrics": [
                "accuracy_trend_24h",
                "coverage_trend_24h", 
                "cost_per_receipt_trend",
                "latency_p95_trend"
            ],
            
            "business_metrics": [
                "gpt_cost_savings",
                "merchant_coverage_distribution",
                "label_type_distribution",
                "decision_type_distribution"
            ],
            
            "system_health": [
                "memory_usage",
                "cpu_utilization",
                "database_connections",
                "queue_depths"
            ]
        }
    
    @staticmethod
    def get_report_schedule() -> Dict[str, Dict]:
        """Get automated report generation schedule."""
        return {
            "daily_summary": {
                "frequency": "daily",
                "time": "09:00",
                "recipients": ["product-team@company.com"],
                "metrics": ["cost_savings", "accuracy", "coverage", "volume"],
                "format": "email"
            },
            
            "weekly_analysis": {
                "frequency": "weekly", 
                "day": "monday",
                "time": "10:00",
                "recipients": ["engineering-team@company.com", "product-team@company.com"],
                "metrics": ["trends", "ab_test_results", "merchant_analysis"],
                "format": "dashboard_link"
            },
            
            "monthly_review": {
                "frequency": "monthly",
                "day": 1,
                "time": "14:00", 
                "recipients": ["leadership@company.com"],
                "metrics": ["business_impact", "cost_optimization", "system_health"],
                "format": "executive_summary"
            }
        }


# Production-ready configuration instance
PRODUCTION_CONFIG = ProductionConfig(
    enable_monitoring=True,
    enable_ab_testing=True,
    monitoring_sample_rate=1.0,
    metrics_flush_interval_minutes=5,
    max_processing_time_ms=8000,  # Conservative 8-second limit
    min_accuracy_threshold=0.85,  # High accuracy requirement
    min_coverage_threshold=0.8,   # High coverage requirement
    max_cost_per_receipt=0.03     # Target cost limit
)