#!/usr/bin/env python3
"""
Analyze agent trace files to understand tool call patterns and decision making.
"""

import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Any, Tuple
import re


def load_trace_files(trace_dir: Path) -> List[Dict[str, Any]]:
    """Load all trace files from the traces directory."""
    traces = []
    
    for trace_file in trace_dir.glob("trace_*.json"):
        try:
            with open(trace_file) as f:
                trace_data = json.load(f)
                trace_data["filename"] = trace_file.name
                traces.append(trace_data)
        except Exception as e:
            print(f"Error loading {trace_file.name}: {e}")
    
    return traces


def analyze_tool_call_patterns(traces: List[Dict]) -> Dict[str, Any]:
    """Analyze patterns in tool calls across all traces."""
    patterns = {
        "tool_sequences": [],
        "tool_frequency": Counter(),
        "successful_sequences": [],
        "failed_sequences": [],
        "avg_calls_per_trace": 0,
        "common_patterns": Counter()
    }
    
    for trace in traces:
        calls = trace.get("function_calls", [])
        
        # Extract tool sequence
        sequence = [call["name"] for call in calls]
        patterns["tool_sequences"].append(sequence)
        
        # Count tool frequency
        for call in calls:
            patterns["tool_frequency"][call["name"]] += 1
        
        # Check if sequence was successful (ends with tool_return_metadata)
        if sequence and sequence[-1] == "tool_return_metadata":
            patterns["successful_sequences"].append(sequence)
        else:
            patterns["failed_sequences"].append(sequence)
        
        # Track common patterns (first 3 tools)
        if len(sequence) >= 3:
            pattern = tuple(sequence[:3])
            patterns["common_patterns"][pattern] += 1
    
    patterns["avg_calls_per_trace"] = (
        sum(len(seq) for seq in patterns["tool_sequences"]) / len(traces)
        if traces else 0
    )
    
    return patterns


def analyze_api_responses(traces: List[Dict]) -> Dict[str, Any]:
    """Analyze the responses from Google Places API calls."""
    api_stats = {
        "phone_lookups": {"success": 0, "failure": 0},
        "address_lookups": {"success": 0, "failure": 0},
        "text_searches": {"success": 0, "failure": 0},
        "nearby_searches": {"success": 0, "failure": 0},
        "validation_methods_used": Counter()
    }
    
    for trace in traces:
        for call in trace.get("function_calls", []):
            # Extract arguments to understand what was searched
            if call["name"] == "tool_return_metadata":
                try:
                    args = json.loads(call["arguments"])
                    method = args.get("validated_by", "UNKNOWN")
                    api_stats["validation_methods_used"][method] += 1
                except:
                    pass
            
            # Note: We'd need the actual output to determine success/failure
            # For now, we can infer from the sequence
            
    return api_stats


def analyze_decision_reasoning(traces: List[Dict]) -> Dict[str, Any]:
    """Analyze the agent's reasoning patterns."""
    reasoning = {
        "common_keywords": Counter(),
        "avg_reasoning_length": 0,
        "validation_strategies": [],
        "confidence_indicators": []
    }
    
    reasoning_texts = []
    
    for trace in traces:
        for call in trace.get("function_calls", []):
            if call["name"] == "tool_return_metadata":
                try:
                    args = json.loads(call["arguments"])
                    reason = args.get("reasoning", "")
                    reasoning_texts.append(reason)
                    
                    # Extract keywords
                    words = reason.lower().split()
                    for word in words:
                        if len(word) > 4:  # Skip short words
                            reasoning["common_keywords"][word] += 1
                    
                    # Look for confidence indicators
                    if any(word in reason.lower() for word in ["exact match", "confirmed", "verified"]):
                        reasoning["confidence_indicators"].append("high")
                    elif any(word in reason.lower() for word in ["likely", "probably", "appears"]):
                        reasoning["confidence_indicators"].append("medium")
                    elif any(word in reason.lower() for word in ["unsure", "no match", "inference"]):
                        reasoning["confidence_indicators"].append("low")
                except:
                    pass
    
    if reasoning_texts:
        reasoning["avg_reasoning_length"] = (
            sum(len(r) for r in reasoning_texts) / len(reasoning_texts)
        )
    
    return reasoning


def analyze_extracted_data_usage(traces: List[Dict]) -> Dict[str, Any]:
    """Analyze how extracted data is being used in tool calls."""
    usage_stats = {
        "phone_formats": Counter(),
        "address_usage": 0,
        "extracted_vs_raw": {"extracted": 0, "raw": 0}
    }
    
    for trace in traces:
        for call in trace.get("function_calls", []):
            if call["name"] == "search_by_phone":
                try:
                    args = json.loads(call["arguments"])
                    phone = args.get("phone", "")
                    # Categorize phone format
                    if re.match(r'^\d{10}$', phone):
                        usage_stats["phone_formats"]["10_digits"] += 1
                    elif re.match(r'^\d{3}-\d{3}-\d{4}$', phone):
                        usage_stats["phone_formats"]["formatted"] += 1
                    elif len(phone) < 10:
                        usage_stats["phone_formats"]["incomplete"] += 1
                    else:
                        usage_stats["phone_formats"]["other"] += 1
                except:
                    pass
            
            elif call["name"] == "search_by_address":
                usage_stats["address_usage"] += 1
    
    return usage_stats


def find_optimization_opportunities(
    tool_patterns: Dict,
    api_stats: Dict,
    reasoning: Dict,
    usage_stats: Dict
) -> List[str]:
    """Identify opportunities for optimization based on analysis."""
    opportunities = []
    
    # Check for inefficient patterns
    if tool_patterns["avg_calls_per_trace"] > 5:
        opportunities.append(
            "High average tool calls per trace. Consider optimizing the agent's strategy to reduce API calls."
        )
    
    # Check for common failures
    failed_ratio = len(tool_patterns["failed_sequences"]) / len(tool_patterns["tool_sequences"])
    if failed_ratio > 0.2:
        opportunities.append(
            f"{failed_ratio:.1%} of traces don't end with metadata return. Review timeout settings or agent instructions."
        )
    
    # Check phone number issues
    if usage_stats["phone_formats"]["incomplete"] > usage_stats["phone_formats"]["10_digits"]:
        opportunities.append(
            "Many incomplete phone numbers. Consider preprocessing or fallback strategies."
        )
    
    # Check reasoning patterns
    low_confidence = reasoning["confidence_indicators"].count("low")
    total_confidence = len(reasoning["confidence_indicators"])
    if total_confidence > 0 and low_confidence / total_confidence > 0.3:
        opportunities.append(
            "High proportion of low-confidence validations. Consider additional data sources or validation strategies."
        )
    
    # Check for repeated patterns
    most_common_pattern = tool_patterns["common_patterns"].most_common(1)
    if most_common_pattern:
        pattern, count = most_common_pattern[0]
        if count > len(tool_patterns["tool_sequences"]) * 0.5:
            opportunities.append(
                f"Over 50% of traces follow the same pattern: {' → '.join(pattern)}. Consider caching or shortcuts."
            )
    
    return opportunities


def generate_trace_report(
    traces: List[Dict],
    tool_patterns: Dict,
    api_stats: Dict,
    reasoning: Dict,
    usage_stats: Dict,
    opportunities: List[str]
) -> str:
    """Generate a formatted report of trace analysis."""
    
    report = f"""
====================================
    AGENT TRACE ANALYSIS REPORT
====================================

📊 OVERVIEW
-----------
Total traces analyzed: {len(traces)}
Average tool calls per trace: {tool_patterns['avg_calls_per_trace']:.1f}
Successful completions: {len(tool_patterns['successful_sequences'])}/{len(traces)} ({len(tool_patterns['successful_sequences'])/len(traces)*100:.1f}%)

🔧 TOOL USAGE PATTERNS
----------------------
Most frequent tools:
"""
    
    for tool, count in tool_patterns["tool_frequency"].most_common(5):
        report += f"  - {tool}: {count} calls\n"
    
    report += "\nMost common call sequences:\n"
    for pattern, count in tool_patterns["common_patterns"].most_common(3):
        report += f"  - {' → '.join(pattern)}: {count} times\n"
    
    report += f"""

📞 API CALL ANALYSIS
--------------------
Validation methods used:
"""
    
    for method, count in api_stats["validation_methods_used"].most_common():
        report += f"  - {method}: {count}\n"
    
    report += f"""

📱 PHONE NUMBER ANALYSIS
------------------------
Phone formats encountered:
"""
    
    for format_type, count in usage_stats["phone_formats"].most_common():
        report += f"  - {format_type}: {count}\n"
    
    report += f"""
Address searches: {usage_stats['address_usage']}

💭 REASONING ANALYSIS
---------------------
Average reasoning length: {reasoning['avg_reasoning_length']:.0f} characters

Top reasoning keywords:
"""
    
    for word, count in reasoning["common_keywords"].most_common(10):
        report += f"  - {word}: {count}\n"
    
    if reasoning["confidence_indicators"]:
        confidence_dist = Counter(reasoning["confidence_indicators"])
        report += f"""

Confidence distribution:
  - High confidence: {confidence_dist.get('high', 0)}
  - Medium confidence: {confidence_dist.get('medium', 0)}
  - Low confidence: {confidence_dist.get('low', 0)}
"""
    
    report += f"""

🎯 OPTIMIZATION OPPORTUNITIES
-----------------------------
"""
    
    for i, opportunity in enumerate(opportunities, 1):
        report += f"{i}. {opportunity}\n"
    
    report += """

💡 RECOMMENDATIONS
------------------
1. Cache Results: Implement caching for frequently searched merchants
2. Preprocess Data: Clean and normalize phone/address data before validation
3. Fallback Strategy: Use multiple validation approaches in sequence
4. Confidence Scoring: Add confidence thresholds to choose best method
5. Batch Processing: Group similar receipts for efficient processing
"""
    
    return report


def main():
    """Run trace analysis and generate report."""
    trace_dir = Path("dev.traces")
    
    if not trace_dir.exists():
        print(f"Error: {trace_dir} directory not found")
        return
    
    print("Loading trace files...")
    traces = load_trace_files(trace_dir)
    print(f"Loaded {len(traces)} trace files")
    
    if not traces:
        print("No trace files found")
        return
    
    print("Analyzing tool call patterns...")
    tool_patterns = analyze_tool_call_patterns(traces)
    
    print("Analyzing API responses...")
    api_stats = analyze_api_responses(traces)
    
    print("Analyzing decision reasoning...")
    reasoning = analyze_decision_reasoning(traces)
    
    print("Analyzing extracted data usage...")
    usage_stats = analyze_extracted_data_usage(traces)
    
    print("Finding optimization opportunities...")
    opportunities = find_optimization_opportunities(
        tool_patterns, api_stats, reasoning, usage_stats
    )
    
    print("Generating report...")
    report = generate_trace_report(
        traces, tool_patterns, api_stats, reasoning, 
        usage_stats, opportunities
    )
    
    # Save report
    report_path = Path("trace_analysis_report.txt")
    with open(report_path, "w") as f:
        f.write(report)
    
    # Also print to console
    print(report)
    
    print(f"\n✅ Report saved to: {report_path}")


if __name__ == "__main__":
    main()