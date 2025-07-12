#!/usr/bin/env python3
"""
Load test demo - shows staging load test in action.

This is a shortened version that demonstrates the load testing
framework with a smaller dataset for quick validation.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.staging_load_test import LoadTestConfig, StagingLoadTest


async def demo_load_test():
    """Run a demo load test with reduced scale."""
    
    print("🚀 Staging Load Test Demo")
    print("=" * 50)
    
    # Create demo configuration (1 hour instead of 30 days)
    config = LoadTestConfig(
        duration_days=1/24,  # 1 hour
        base_tps=5.0,        # 5 TPS base
        multiplier=2.0,      # 2x = 10 TPS target
        batch_size=50,
        output_dir=Path("demo_load_test_results")
    )
    
    print("\n📋 Demo Configuration:")
    print(f"   Duration: 1 hour (simulating 30-day patterns)")
    print(f"   Target TPS: {config.base_tps * config.multiplier}")
    print(f"   Expected receipts: ~{int(3600 * config.base_tps * config.multiplier):,}")
    print(f"   Merchant distribution: {list(config.merchant_distribution.keys())}")
    print(f"   Complexity: Simple (60%), Medium (30%), Complex (10%)")
    
    print("\n📊 What this simulates:")
    print("   - Variable load throughout the day")
    print("   - Different merchant types and complexities")
    print("   - Decision engine under sustained load")
    print("   - Cost and performance tracking")
    print("   - Shadow testing for quality metrics")
    
    # Run load test
    print("\n⏳ Starting load test...")
    load_test = StagingLoadTest(config)
    
    # Run for a shorter time in demo
    # Override to process just 1000 receipts for demo
    original_run = load_test.run
    
    async def demo_run():
        # Process limited receipts for demo
        load_test.config.duration_days = 0.001  # Very short duration
        await original_run()
    
    load_test.run = demo_run
    await load_test.run()
    
    # Show results
    print("\n📈 Demo Results:")
    summary = load_test.metrics.get_summary()
    
    print(f"\n   Performance:")
    print(f"   - Receipts processed: {load_test.metrics.total_receipts:,}")
    print(f"   - Success rate: {summary.get('success_rate', 0):.1%}")
    print(f"   - Average latency: {summary.get('average_latency_ms', 0):.1f}ms")
    
    print(f"\n   Decision Distribution:")
    dist = summary.get('decision_distribution', {})
    print(f"   - SKIP: {dist.get('skip', 0):.1%} (no GPT needed)")
    print(f"   - BATCH: {dist.get('batch', 0):.1%} (deferred processing)")
    print(f"   - REQUIRED: {dist.get('required', 0):.1%} (immediate GPT)")
    
    print(f"\n   Cost Analysis:")
    print(f"   - Average cost per receipt: ${summary.get('average_cost_usd', 0):.4f}")
    print(f"   - Projected monthly cost: ${load_test.metrics.total_cost_usd * 30 * 24 * 3600:,.2f}")
    
    print(f"\n   Quality Metrics:")
    print(f"   - False skip rate: {summary.get('false_skip_rate', 0):.2%}")
    print(f"   - Error rate: {summary.get('error_rate', 0):.2%}")
    
    # Check if results were saved
    results_file = config.output_dir / "load_test_results.json"
    if results_file.exists():
        print(f"\n✅ Full results saved to: {results_file}")
        
        report_file = config.output_dir / "load_test_report.md"
        if report_file.exists():
            print(f"📄 Human-readable report: {report_file}")


def explain_load_test_features():
    """Explain the load testing framework features."""
    
    print("\n\n🔧 Load Test Framework Features")
    print("=" * 50)
    
    print("\n1. Realistic Traffic Patterns:")
    print("   - Time-based load variation (peak hours)")
    print("   - Merchant distribution based on production data")
    print("   - Receipt complexity variations")
    
    print("\n2. Comprehensive Metrics:")
    print("   - Performance: latency percentiles, throughput")
    print("   - Cost: token usage, API costs")
    print("   - Quality: false skip rates, error rates")
    print("   - Decision distribution: SKIP/BATCH/REQUIRED ratios")
    
    print("\n3. Staging Environment Safety:")
    print("   - Synthetic receipt generation")
    print("   - No production data exposure")
    print("   - Configurable load levels")
    print("   - Checkpoint/resume capability")
    
    print("\n4. Analysis Outputs:")
    print("   - JSON metrics for automation")
    print("   - Markdown reports for humans")
    print("   - Cost projections")
    print("   - Performance bottleneck identification")


def show_usage_examples():
    """Show example usage scenarios."""
    
    print("\n\n📚 Usage Examples")
    print("=" * 50)
    
    print("\n1. Standard 30-day test:")
    print("   python scripts/staging_load_test.py")
    
    print("\n2. High-load stress test:")
    print("   python scripts/staging_load_test.py --multiplier 5.0")
    
    print("\n3. Quick validation (1 day):")
    print("   python scripts/staging_load_test.py --days 1")
    
    print("\n4. Custom configuration:")
    print("   python scripts/staging_load_test.py \\")
    print("     --days 7 \\")
    print("     --tps 20 \\")
    print("     --multiplier 3.0 \\")
    print("     --output-dir custom_test_results")


if __name__ == "__main__":
    print("📊 Staging Load Test Framework Demo")
    print("=" * 70)
    print("\nThis demo shows the load testing framework that simulates")
    print("30 days of receipt processing at 2x peak load to validate")
    print("system performance, cost, and accuracy.")
    print()
    
    # Run demo
    asyncio.run(demo_load_test())
    
    # Show features and usage
    explain_load_test_features()
    show_usage_examples()
    
    print("\n\n✅ Demo completed!")
    print("\n📝 Key Benefits:")
    print("   • Validates system under sustained load")
    print("   • Identifies performance bottlenecks")
    print("   • Projects costs at scale")
    print("   • Ensures quality metrics are maintained")
    print("   • Provides data for capacity planning")