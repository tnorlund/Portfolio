#!/usr/bin/env python3
"""
Demonstration of latency bucketing metrics.

This example shows how the monitoring system now tracks:
- pattern_time_ms: Time spent in pattern detection
- pinecone_time_ms: Time spent querying Pinecone for merchant patterns  
- gpt_time_ms: Time spent in GPT processing
"""

import sys
from pathlib import Path
import time
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_label.monitoring.production_monitor import ProductionMonitor, LabelingSession, LabelingMethod


def demo_latency_bucketing():
    """Demonstrate latency bucketing with a sample session."""
    
    print("🚀 Latency Bucketing Demo")
    print("=" * 40)
    
    # Create monitor
    monitor = ProductionMonitor()
    
    # Start a session
    session_id = monitor.start_session(
        receipt_id="demo_receipt_001",
        image_id="demo_image_001",
        method=LabelingMethod.AGENT_BASED,
        merchant_name="McDonald's",
        merchant_category="restaurant"
    )
    
    print(f"✅ Started session: {session_id}")
    
    # Simulate timing for different components
    
    # 1. Pattern detection timing
    pattern_time = 15.5  # ms
    pinecone_time = 8.2  # ms
    total_pattern_time = pattern_time + pinecone_time
    
    monitor.update_session_timing(
        session_id=session_id,
        pattern_detection_time_ms=total_pattern_time,  # Legacy metric
        pattern_time_ms=pattern_time,  # New bucketed metric
        pinecone_time_ms=pinecone_time  # New bucketed metric
    )
    
    print(f"\n⏱️  Pattern Detection Timing:")
    print(f"   Total: {total_pattern_time:.1f}ms")
    print(f"   ├─ Pattern time: {pattern_time:.1f}ms")
    print(f"   └─ Pinecone time: {pinecone_time:.1f}ms")
    
    # 2. GPT processing timing (if needed)
    gpt_time = 125.3  # ms
    
    monitor.update_session_timing(
        session_id=session_id,
        gpt_processing_time_ms=gpt_time,  # Legacy metric
        gpt_time_ms=gpt_time  # New bucketed metric
    )
    
    print(f"\n⏱️  GPT Processing Timing:")
    print(f"   GPT time: {gpt_time:.1f}ms")
    
    # Update quality metrics
    monitor.update_session_quality(
        session_id=session_id,
        labels_found=8,
        pattern_labels=5,
        gpt_labels=3,
        essential_labels_coverage=0.95
    )
    
    # Update cost metrics
    monitor.update_session_cost(
        session_id=session_id,
        gpt_tokens_used=150,
        estimated_cost_usd=0.003
    )
    
    # Finish session
    session = monitor.finish_session(session_id)
    
    if session:
        print(f"\n📊 Session Summary:")
        print(f"   Session ID: {session.session_id}")
        print(f"   Total time: {session.total_time_ms:.1f}ms")
        print(f"   Labels found: {session.labels_found}")
        print(f"   Essential coverage: {session.essential_labels_coverage:.1%}")
        print(f"   Estimated cost: ${session.estimated_cost_usd:.4f}")
        
        # Display latency buckets
        print(f"\n📈 Latency Buckets:")
        print(f"   Pattern detection: {session.pattern_time_ms:.1f}ms")
        print(f"   Pinecone queries: {session.pinecone_time_ms:.1f}ms")
        print(f"   GPT processing: {session.gpt_time_ms:.1f}ms")
        
        # Verify timing relationship
        print(f"\n✅ Timing Validation:")
        print(f"   pattern_time ({session.pattern_time_ms:.1f}ms) ≈ "
              f"pinecone_time ({session.pinecone_time_ms:.1f}ms) « "
              f"gpt_time ({session.gpt_time_ms:.1f}ms)")
    
    # Get statistics
    stats = monitor.get_statistics()
    print(f"\n📊 Monitor Statistics:")
    print(f"   Sessions completed: {stats['global_stats']['sessions_completed']}")
    print(f"   Total GPT tokens: {stats['global_stats']['total_gpt_tokens']}")
    print(f"   Total cost: ${stats['global_stats']['total_cost_usd']:.4f}")
    
    return monitor


def demo_multiple_sessions():
    """Demonstrate latency bucketing across multiple sessions."""
    
    print("\n\n🔄 Multiple Sessions Demo")
    print("=" * 40)
    
    monitor = ProductionMonitor()
    
    # Simulate different scenarios
    scenarios = [
        {
            "name": "Fast pattern, no Pinecone",
            "pattern_time": 8.5,
            "pinecone_time": 0.0,
            "gpt_time": 0.0
        },
        {
            "name": "With Pinecone lookup",
            "pattern_time": 12.3,
            "pinecone_time": 6.8,
            "gpt_time": 0.0
        },
        {
            "name": "Full processing with GPT",
            "pattern_time": 10.2,
            "pinecone_time": 7.5,
            "gpt_time": 145.6
        }
    ]
    
    for i, scenario in enumerate(scenarios):
        print(f"\n📄 Scenario {i+1}: {scenario['name']}")
        
        # Start session
        session_id = monitor.start_session(
            receipt_id=f"multi_receipt_{i+1}",
            image_id=f"multi_image_{i+1}",
            method=LabelingMethod.AGENT_BASED
        )
        
        # Update timing
        monitor.update_session_timing(
            session_id=session_id,
            pattern_time_ms=scenario["pattern_time"],
            pinecone_time_ms=scenario["pinecone_time"],
            gpt_time_ms=scenario["gpt_time"]
        )
        
        # Finish session
        session = monitor.finish_session(session_id)
        
        if session:
            print(f"   Pattern: {session.pattern_time_ms:.1f}ms")
            print(f"   Pinecone: {session.pinecone_time_ms:.1f}ms")
            print(f"   GPT: {session.gpt_time_ms:.1f}ms")
    
    # Calculate averages
    print(f"\n📊 Average Latencies Across All Sessions:")
    
    # In a real implementation, we'd calculate from stored metrics
    avg_pattern = sum(s["pattern_time"] for s in scenarios) / len(scenarios)
    avg_pinecone = sum(s["pinecone_time"] for s in scenarios) / len(scenarios)
    avg_gpt = sum(s["gpt_time"] for s in scenarios) / len(scenarios)
    
    print(f"   Pattern detection: {avg_pattern:.1f}ms")
    print(f"   Pinecone queries: {avg_pinecone:.1f}ms")
    print(f"   GPT processing: {avg_gpt:.1f}ms")
    
    return monitor


if __name__ == "__main__":
    print("⏱️  Latency Bucketing Metrics Demo")
    print("=" * 50)
    print("\nThis demonstrates the new latency bucketing feature that tracks")
    print("pattern_time_ms, pinecone_time_ms, and gpt_time_ms separately.")
    print()
    
    # Run demos
    monitor1 = demo_latency_bucketing()
    monitor2 = demo_multiple_sessions()
    
    print("\n\n✅ Demo completed successfully!")
    print("\n📝 Key Takeaways:")
    print("   • Latency is now tracked in separate buckets for better analysis")
    print("   • Pattern detection time excludes Pinecone query time")
    print("   • GPT processing time is tracked independently")
    print("   • Legacy metrics maintained for backward compatibility")
    print("   • Enables validation of expected performance relationships")