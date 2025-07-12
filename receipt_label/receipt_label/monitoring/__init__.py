"""
Production monitoring and A/B testing for receipt labeling.

This module provides comprehensive monitoring, A/B testing, and performance
tracking capabilities for the receipt labeling system.

Key Components:
- ProductionMonitor: Core monitoring and metrics collection
- ABTestManager: A/B testing framework with statistical analysis
- MonitoredLabelingSystem: Integration layer for seamless monitoring
- Production configuration and deployment strategies

Example Usage:
    ```python
    from receipt_label.monitoring import MonitoredLabelingSystem
    from receipt_label.agent.decision_engine import DecisionEngine
    from receipt_label.pattern_detection.orchestrator import ParallelPatternOrchestrator
    
    # Initialize monitored system
    decision_engine = DecisionEngine()
    pattern_orchestrator = ParallelPatternOrchestrator()
    
    monitored_system = MonitoredLabelingSystem(
        decision_engine=decision_engine,
        pattern_orchestrator=pattern_orchestrator,
        enable_ab_testing=True
    )
    
    # Process receipt with monitoring
    with monitored_system.monitored_session(
        receipt_id="receipt_001",
        image_id="img_001", 
        merchant_name="McDonald's"
    ) as session:
        # Your labeling logic here
        session.update_quality(labels_found=5, essential_coverage=0.9)
    
    # Set up A/B test
    monitored_system.setup_ab_test(
        test_name="new_pattern_algorithm",
        description="Test improved pattern detection",
        control_method=LabelingMethod.AGENT_BASED,
        treatment_method=LabelingMethod.HYBRID,
        traffic_split_percentage=15.0
    )
    
    # Get monitoring dashboard
    dashboard = monitored_system.get_monitoring_dashboard()
    ```

A/B Testing Example:
    ```python
    from receipt_label.monitoring.ab_testing import ABTestManager
    from receipt_label.monitoring.production_monitor import LabelingMethod, PerformanceMetricType
    
    # Create A/B test manager
    ab_manager = ABTestManager(monitor)
    
    # Create test
    test_config = ab_manager.create_test(
        test_name="cost_optimization_v2",
        description="Test new cost optimization approach",
        control_method=LabelingMethod.AGENT_BASED,
        treatment_method=LabelingMethod.HYBRID,
        traffic_split_percentage=20.0,
        duration_days=14,
        success_metrics=[PerformanceMetricType.COST, PerformanceMetricType.COVERAGE]
    )
    
    # Start test
    ab_manager.start_test("cost_optimization_v2")
    
    # Analyze results
    results = ab_manager.analyze_test_results("cost_optimization_v2")
    ```
"""

from .token_ledger import (
    TokenLedger,
    ModelTier,
    TokenUsageType,
    TokenUsageRecord
)

from .production_monitor import (
    ProductionMonitor,
    LabelingMethod,
    PerformanceMetricType,
    LabelingSession,
    PerformanceMetric,
    ABTestConfig
)

from .ab_testing import (
    ABTestManager,
    ABTestStatus,
    SignificanceTest,
    ABTestResult,
    GuardrailMetric
)

from .shadow_testing import (
    ShadowTestManager,
    ShadowTestMode,
    ShadowTestResult
)

from .integration import (
    MonitoredLabelingSystem,
    MonitoredSession
)

from .production_config import (
    ProductionConfig,
    StandardABTests,
    MonitoringAlerts,
    ProductionDeploymentStrategy,
    DashboardConfig,
    PRODUCTION_CONFIG
)

__all__ = [
    # Token usage tracking
    "TokenLedger",
    "ModelTier",
    "TokenUsageType", 
    "TokenUsageRecord",
    
    # Core monitoring
    "ProductionMonitor",
    "LabelingMethod", 
    "PerformanceMetricType",
    "LabelingSession",
    "PerformanceMetric",
    "ABTestConfig",
    
    # A/B testing
    "ABTestManager",
    "ABTestStatus",
    "SignificanceTest", 
    "ABTestResult",
    "GuardrailMetric",
    
    # Shadow testing
    "ShadowTestManager",
    "ShadowTestMode",
    "ShadowTestResult",
    
    # Integration
    "MonitoredLabelingSystem",
    "MonitoredSession",
    
    # Configuration
    "ProductionConfig",
    "StandardABTests",
    "MonitoringAlerts",
    "ProductionDeploymentStrategy",
    "DashboardConfig",
    "PRODUCTION_CONFIG"
]

# Version info
__version__ = "1.0.0"
__author__ = "Receipt Labeling Team"
__description__ = "Production monitoring and A/B testing for receipt labeling system"