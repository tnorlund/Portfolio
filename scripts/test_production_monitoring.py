#!/usr/bin/env python3
"""
Test script for production monitoring and A/B testing framework.

This script demonstrates the comprehensive monitoring and A/B testing capabilities
for the receipt labeling system, showing how to safely deploy and measure new approaches.
"""

import asyncio
import json
import logging
from pathlib import Path
import sys
import time
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "receipt_label"))

from receipt_label.agent.decision_engine import DecisionEngine
from receipt_label.pattern_detection.orchestrator import ParallelPatternOrchestrator
from receipt_label.monitoring.production_monitor import (
    ProductionMonitor, LabelingMethod, PerformanceMetricType
)
from receipt_label.monitoring.ab_testing import ABTestManager, GuardrailMetric
from receipt_label.monitoring.integration import MonitoredLabelingSystem
from receipt_label.constants import CORE_LABELS

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def create_test_receipt(receipt_id: str, merchant_name: str, complexity: str = "simple") -> tuple:
    """Create test receipt data with varying complexity."""
    
    base_words = [
        {"word_id": 1, "text": merchant_name, "x": 10, "y": 10, "width": 100, "height": 20},
        {"word_id": 2, "text": "2024-01-15", "x": 10, "y": 40, "width": 80, "height": 15},
        {"word_id": 3, "text": "$19.99", "x": 10, "y": 70, "width": 50, "height": 15},
    ]
    
    # Add complexity based on type
    if complexity == "simple":
        words = base_words + [
            {"word_id": 4, "text": "Cash", "x": 10, "y": 100, "width": 40, "height": 15},
            {"word_id": 5, "text": "Thank you", "x": 10, "y": 130, "width": 70, "height": 15},
        ]
    elif complexity == "medium":
        words = base_words + [
            {"word_id": 4, "text": "Big Mac", "x": 10, "y": 100, "width": 60, "height": 15},
            {"word_id": 5, "text": "French Fries", "x": 10, "y": 130, "width": 80, "height": 15},
            {"word_id": 6, "text": "Visa ••••1234", "x": 10, "y": 160, "width": 90, "height": 15},
            {"word_id": 7, "text": "Tax $1.50", "x": 10, "y": 190, "width": 60, "height": 15},
        ]
    else:  # complex
        words = base_words + [
            {"word_id": 4, "text": "Organic Bananas", "x": 10, "y": 100, "width": 100, "height": 15},
            {"word_id": 5, "text": "2.5 lbs", "x": 10, "y": 130, "width": 50, "height": 15},
            {"word_id": 6, "text": "Whole Milk", "x": 10, "y": 160, "width": 70, "height": 15},
            {"word_id": 7, "text": "1 gallon", "x": 10, "y": 190, "width": 60, "height": 15},
            {"word_id": 8, "text": "Store discount", "x": 10, "y": 220, "width": 100, "height": 15},
            {"word_id": 9, "text": "-$2.00", "x": 10, "y": 250, "width": 50, "height": 15},
            {"word_id": 10, "text": "Subtotal $17.99", "x": 10, "y": 280, "width": 100, "height": 15},
            {"word_id": 11, "text": "Tax $1.50", "x": 10, "y": 310, "width": 60, "height": 15},
            {"word_id": 12, "text": "Visa ••••1234", "x": 10, "y": 340, "width": 90, "height": 15},
        ]
    
    metadata = {
        "receipt_id": receipt_id,
        "image_id": f"img_{receipt_id}",
        "merchant_name": merchant_name,
        "complexity": complexity
    }
    
    return words, metadata


async def test_basic_monitoring():
    """Test basic production monitoring functionality."""
    print("\n" + "="*60)
    print("TESTING BASIC PRODUCTION MONITORING")
    print("="*60)
    
    # Initialize components
    decision_engine = DecisionEngine()
    pattern_orchestrator = ParallelPatternOrchestrator()
    
    # Create monitored system
    monitored_system = MonitoredLabelingSystem(
        decision_engine=decision_engine,
        pattern_orchestrator=pattern_orchestrator,
        enable_ab_testing=False  # Start without A/B testing
    )
    
    # Process several test receipts
    test_receipts = [
        ("receipt_001", "McDonald's", "simple"),
        ("receipt_002", "Walmart", "complex"),
        ("receipt_003", "Shell Gas Station", "simple"),
        ("receipt_004", "CVS Pharmacy", "medium"),
        ("receipt_005", "Target", "medium"),
    ]
    
    print(f"Processing {len(test_receipts)} test receipts...")
    
    for receipt_id, merchant_name, complexity in test_receipts:
        print(f"\n--- Processing {receipt_id} ({merchant_name}) ---")
        
        # Create test receipt
        words, metadata = create_test_receipt(receipt_id, merchant_name, complexity)
        
        # Process with monitoring
        try:
            results = await monitored_system.process_receipt_with_monitoring(
                receipt_words=words,
                receipt_metadata=metadata
            )
            
            print(f"  ✅ Session: {results['session_id']}")
            print(f"  Decision: {results['gpt_decision']}")
            print(f"  Labels found: {results['metrics']['essential_coverage']:.1%} coverage")
            print(f"  Cost: ${results['metrics']['estimated_cost_usd']:.4f}")
            print(f"  Time: {results['metrics']['pattern_detection_time_ms']:.1f}ms")
            
        except Exception as e:
            print(f"  ❌ Error: {e}")
    
    # Get monitoring dashboard
    dashboard = monitored_system.get_monitoring_dashboard()
    
    print(f"\n--- Monitoring Summary ---")
    print(f"Receipts processed: {dashboard['system_stats']['receipts_processed']}")
    print(f"Sessions created: {dashboard['system_stats']['monitoring_sessions_created']}")
    print(f"Errors: {dashboard['system_stats']['errors_encountered']}")
    
    # Method statistics
    print(f"\nMethod Performance:")
    for method, stats in dashboard['monitor_stats']['method_stats'].items():
        if stats['sessions'] > 0:
            print(f"  {method}: {stats['sessions']} sessions, "
                  f"{stats['avg_coverage']:.1%} coverage, "
                  f"${stats['avg_cost']:.4f} avg cost")


async def test_ab_testing_framework():
    """Test A/B testing framework."""
    print("\n" + "="*60)
    print("TESTING A/B TESTING FRAMEWORK")
    print("="*60)
    
    # Initialize components with A/B testing enabled
    decision_engine = DecisionEngine()
    pattern_orchestrator = ParallelPatternOrchestrator()
    
    monitored_system = MonitoredLabelingSystem(
        decision_engine=decision_engine,
        pattern_orchestrator=pattern_orchestrator,
        enable_ab_testing=True
    )
    
    # Set up A/B test
    test_setup_success = monitored_system.setup_ab_test(
        test_name="agent_vs_pattern_comparison",
        description="Compare agent-based labeling vs pure pattern detection",
        control_method=LabelingMethod.PATTERN_ONLY,
        treatment_method=LabelingMethod.AGENT_BASED,
        traffic_split_percentage=30.0,  # 30% treatment, 70% control
        duration_days=1,  # Short test for demo
        auto_start=True
    )
    
    if test_setup_success:
        print("✅ A/B test set up successfully")
    else:
        print("❌ Failed to set up A/B test")
        return
    
    # Process receipts through A/B test
    test_receipts = []
    for i in range(10):
        receipt_id = f"ab_test_receipt_{i:03d}"
        merchant_name = ["McDonald's", "Walmart", "Shell", "CVS", "Target"][i % 5]
        complexity = ["simple", "medium", "complex"][i % 3]
        test_receipts.append((receipt_id, merchant_name, complexity))
    
    print(f"\nProcessing {len(test_receipts)} receipts through A/B test...")
    
    control_count = 0
    treatment_count = 0
    
    for receipt_id, merchant_name, complexity in test_receipts:
        words, metadata = create_test_receipt(receipt_id, merchant_name, complexity)
        
        try:
            results = await monitored_system.process_receipt_with_monitoring(
                receipt_words=words,
                receipt_metadata=metadata
            )
            
            if results['labeling_method'] == LabelingMethod.PATTERN_ONLY.value:
                control_count += 1
            elif results['labeling_method'] == LabelingMethod.AGENT_BASED.value:
                treatment_count += 1
            
            print(f"  {receipt_id}: {results['labeling_method']} "
                  f"({results['metrics']['essential_coverage']:.1%} coverage)")
            
        except Exception as e:
            print(f"  ❌ {receipt_id}: Error - {e}")
    
    print(f"\nA/B Test Assignment Results:")
    print(f"  Control (Pattern Only): {control_count}")
    print(f"  Treatment (Agent Based): {treatment_count}")
    print(f"  Split: {treatment_count/(control_count+treatment_count):.1%} treatment")
    
    # Get A/B test results
    if monitored_system.ab_test_manager:
        test_results = monitored_system.ab_test_manager.analyze_test_results("agent_vs_pattern_comparison")
        
        print(f"\n--- A/B Test Analysis ---")
        for metric_type, result in test_results.items():
            print(f"{metric_type}:")
            print(f"  Control: {result.control_mean:.3f} (n={result.control_sample_size})")
            print(f"  Treatment: {result.treatment_mean:.3f} (n={result.treatment_sample_size})")
            print(f"  Effect size: {result.effect_size:.1%}")
            print(f"  Significant: {result.is_significant}")


async def test_guardrails_system():
    """Test guardrails and safety mechanisms."""
    print("\n" + "="*60)
    print("TESTING GUARDRAILS AND SAFETY MECHANISMS")
    print("="*60)
    
    # Initialize system
    decision_engine = DecisionEngine()
    pattern_orchestrator = ParallelPatternOrchestrator()
    
    monitored_system = MonitoredLabelingSystem(
        decision_engine=decision_engine,
        pattern_orchestrator=pattern_orchestrator,
        enable_ab_testing=True
    )
    
    # Create test with strict guardrails
    if monitored_system.ab_test_manager:
        
        # Define strict guardrails
        strict_guardrails = [
            GuardrailMetric(
                metric_type=PerformanceMetricType.COVERAGE,
                threshold_value=0.95,  # Very high coverage requirement
                direction="min",
                description="Minimum 95% coverage required"
            ),
            GuardrailMetric(
                metric_type=PerformanceMetricType.LATENCY,
                threshold_value=1000,  # Maximum 1 second
                direction="max",
                description="Maximum 1 second processing time"
            )
        ]
        
        # Create test with guardrails
        test_config = monitored_system.ab_test_manager.create_test(
            test_name="guardrail_test",
            description="Test with strict guardrails",
            control_method=LabelingMethod.PATTERN_ONLY,
            treatment_method=LabelingMethod.FULL_GPT,
            traffic_split_percentage=20.0,
            duration_days=1,
            success_metrics=[PerformanceMetricType.COVERAGE, PerformanceMetricType.LATENCY],
            guardrails=strict_guardrails
        )
        
        # Start test
        monitored_system.ab_test_manager.start_test("guardrail_test")
        print("✅ Created test with strict guardrails")
        
        # Process some receipts to generate metrics
        for i in range(3):
            receipt_id = f"guardrail_test_{i}"
            words, metadata = create_test_receipt(receipt_id, "Test Store", "medium")
            
            try:
                results = await monitored_system.process_receipt_with_monitoring(
                    receipt_words=words,
                    receipt_metadata=metadata
                )
                print(f"  Processed {receipt_id}: {results['labeling_method']}")
            except Exception as e:
                print(f"  Error processing {receipt_id}: {e}")
        
        # Check guardrails
        guardrails_ok = monitored_system.ab_test_manager._check_guardrails("guardrail_test")
        print(f"\nGuardrails status: {'✅ OK' if guardrails_ok else '❌ VIOLATION'}")
        
        # Get recommendations
        recommendations = monitored_system.ab_test_manager.get_recommendations("guardrail_test")
        if recommendations:
            print(f"Recommendations:")
            for rec in recommendations:
                print(f"  • {rec}")


def test_dashboard_and_reporting():
    """Test monitoring dashboard and reporting."""
    print("\n" + "="*60)
    print("TESTING DASHBOARD AND REPORTING")
    print("="*60)
    
    # Initialize system
    decision_engine = DecisionEngine()
    pattern_orchestrator = ParallelPatternOrchestrator()
    
    monitored_system = MonitoredLabelingSystem(
        decision_engine=decision_engine,
        pattern_orchestrator=pattern_orchestrator,
        enable_ab_testing=True
    )
    
    # Get comprehensive dashboard
    dashboard = monitored_system.get_monitoring_dashboard()
    
    print("Dashboard Components:")
    for section, data in dashboard.items():
        if section != "timestamp":
            print(f"  ✅ {section}: {type(data).__name__}")
            if isinstance(data, dict) and "sessions" in str(data):
                session_count = str(data).count("sessions")
                if session_count > 0:
                    print(f"     Contains session data")
    
    # Test exporting data as JSON
    try:
        dashboard_json = json.dumps(dashboard, indent=2, default=str)
        print(f"\n✅ Dashboard JSON export: {len(dashboard_json)} characters")
    except Exception as e:
        print(f"❌ JSON export failed: {e}")
    
    print(f"\nKey Metrics Summary:")
    print(f"  System uptime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Total sessions: {dashboard['system_stats']['monitoring_sessions_created']}")
    print(f"  Active A/B tests: {dashboard.get('ab_testing', {}).get('active_tests_count', 0)}")


async def demonstrate_cost_optimization():
    """Demonstrate cost optimization through monitoring."""
    print("\n" + "="*60)
    print("DEMONSTRATING COST OPTIMIZATION MONITORING")
    print("="*60)
    
    # Initialize system
    decision_engine = DecisionEngine()
    pattern_orchestrator = ParallelPatternOrchestrator()
    
    monitored_system = MonitoredLabelingSystem(
        decision_engine=decision_engine,
        pattern_orchestrator=pattern_orchestrator,
        enable_ab_testing=False
    )
    
    # Simulate different merchant types to show cost variations
    test_scenarios = [
        ("McDonald's", "simple", "Should have low cost - simple restaurant"),
        ("Walmart", "complex", "Higher cost - complex grocery receipt"),
        ("Shell Gas", "simple", "Low cost - simple gas station"),
        ("Unknown Store", "complex", "Higher cost - unknown merchant with complexity"),
    ]
    
    total_cost = 0.0
    
    print("Cost Analysis by Merchant Type:")
    
    for i, (merchant, complexity, description) in enumerate(test_scenarios):
        receipt_id = f"cost_test_{i}"
        words, metadata = create_test_receipt(receipt_id, merchant, complexity)
        
        try:
            results = await monitored_system.process_receipt_with_monitoring(
                receipt_words=words,
                receipt_metadata=metadata
            )
            
            cost = results['metrics']['estimated_cost_usd']
            coverage = results['metrics']['essential_coverage']
            decision = results['gpt_decision']
            
            total_cost += cost
            
            print(f"\n{merchant} ({complexity}):")
            print(f"  Description: {description}")
            print(f"  Decision: {decision}")
            print(f"  Coverage: {coverage:.1%}")
            print(f"  Cost: ${cost:.4f}")
            
        except Exception as e:
            print(f"❌ Error processing {merchant}: {e}")
    
    print(f"\nTotal estimated cost: ${total_cost:.4f}")
    print(f"Average cost per receipt: ${total_cost/len(test_scenarios):.4f}")
    
    # Show cost optimization metrics
    dashboard = monitored_system.get_monitoring_dashboard()
    decision_stats = dashboard['decision_engine_stats']
    
    print(f"\nCost Optimization Summary:")
    print(f"  Skip rate: {decision_stats.get('skip_rate', 0):.1%}")
    print(f"  Batch rate: {decision_stats.get('batch_rate', 0):.1%}")
    print(f"  Required rate: {decision_stats.get('required_rate', 0):.1%}")
    
    estimated_savings = decision_stats.get('skip_rate', 0) * 0.7  # 70% savings on skipped calls
    print(f"  Estimated savings: ~{estimated_savings:.1%}")


async def main():
    """Run all production monitoring tests."""
    print("PRODUCTION MONITORING AND A/B TESTING TEST SUITE")
    print("=" * 60)
    
    # Test basic monitoring
    await test_basic_monitoring()
    
    # Test A/B testing framework
    await test_ab_testing_framework()
    
    # Test guardrails system
    await test_guardrails_system()
    
    # Test dashboard and reporting
    test_dashboard_and_reporting()
    
    # Demonstrate cost optimization
    await demonstrate_cost_optimization()
    
    print("\n" + "="*60)
    print("PRODUCTION MONITORING TESTING COMPLETE")
    print("="*60)
    print("\nKey Benefits Demonstrated:")
    print("1. ✅ Comprehensive production monitoring")
    print("2. ✅ Safe A/B testing with guardrails")
    print("3. ✅ Real-time performance tracking")
    print("4. ✅ Cost optimization monitoring")
    print("5. ✅ Statistical analysis and reporting")
    print("6. ✅ Merchant-specific performance insights")
    print("\nThis monitoring system provides comprehensive visibility into")
    print("receipt labeling performance and enables safe experimentation")
    print("with new approaches while preventing system degradation.")


if __name__ == "__main__":
    asyncio.run(main())