#!/usr/bin/env python3
"""
Generate CI optimization recommendations based on performance analysis

This script analyzes CI performance data and generates actionable recommendations
for improving build speed, reducing failures, and optimizing resource usage.

Usage:
    python scripts/generate_ci_recommendations.py analysis.json
"""

import argparse
import json
import sys
from datetime import datetime
from typing import Dict, List, Any


class CIRecommendationEngine:
    """Generates optimization recommendations based on CI performance data."""
    
    def __init__(self, analysis_data: Dict[str, Any]):
        self.analysis = analysis_data
        self.recommendations = []
        
    def analyze_duration_bottlenecks(self) -> List[str]:
        """Analyze job durations to identify bottlenecks."""
        recommendations = []
        
        jobs = self.analysis.get('jobs', {})
        for job_name, job_data in jobs.items():
            duration_stats = job_data.get('duration_stats', {})
            avg_duration = duration_stats.get('avg', 0)
            
            # Long-running jobs (>10 minutes)
            if avg_duration > 600:
                recommendations.append(
                    f"🐌 **{job_name}** takes {avg_duration/60:.1f} minutes on average. "
                    f"Consider breaking into smaller parallel jobs or optimizing dependencies."
                )
            
            # High variance in duration
            max_duration = duration_stats.get('max', 0)
            min_duration = duration_stats.get('min', 0)
            if max_duration > 0 and (max_duration - min_duration) / max_duration > 0.5:
                recommendations.append(
                    f"⚡ **{job_name}** has high duration variance "
                    f"({min_duration:.0f}s - {max_duration:.0f}s). "
                    f"Investigate performance inconsistencies."
                )
            
            # Check for specific bottlenecks
            bottlenecks = job_data.get('bottlenecks', [])
            for bottleneck in bottlenecks:
                stage = bottleneck.get('stage', 'unknown')
                avg_time = bottleneck.get('avg_duration', 0)
                severity = bottleneck.get('severity', 'low')
                
                if severity == 'high':
                    recommendations.append(
                        f"🔴 **{job_name}.{stage}** is a critical bottleneck "
                        f"({avg_time:.1f}s average). Prioritize optimization."
                    )
                elif severity == 'medium':
                    recommendations.append(
                        f"🟡 **{job_name}.{stage}** could be optimized "
                        f"({avg_time:.1f}s average)."
                    )
        
        return recommendations
    
    def analyze_failure_patterns(self) -> List[str]:
        """Analyze failure rates and patterns."""
        recommendations = []
        
        jobs = self.analysis.get('jobs', {})
        for job_name, job_data in jobs.items():
            success_rate = job_data.get('success_rate', 1.0)
            
            if success_rate < 0.95:  # Less than 95% success rate
                failure_rate = (1 - success_rate) * 100
                recommendations.append(
                    f"❌ **{job_name}** has {failure_rate:.1f}% failure rate. "
                    f"Investigate flaky tests or infrastructure issues."
                )
            elif success_rate < 0.98:  # Less than 98% success rate
                failure_rate = (1 - success_rate) * 100
                recommendations.append(
                    f"⚠️ **{job_name}** has {failure_rate:.1f}% failure rate. "
                    f"Monitor for stability issues."
                )
        
        return recommendations
    
    def analyze_resource_utilization(self) -> List[str]:
        """Analyze resource usage patterns."""
        recommendations = []
        
        summary = self.analysis.get('summary', {})
        total_runs = summary.get('total_runs', 0)
        
        # High frequency of runs might indicate inefficient triggering
        if total_runs > 100:  # More than 100 runs in analysis period
            recommendations.append(
                f"🔄 High CI activity detected ({total_runs} runs). "
                f"Consider optimizing triggers to reduce unnecessary builds."
            )
        
        # Check for jobs that could be parallelized
        jobs = self.analysis.get('jobs', {})
        sequential_time = sum(
            job_data.get('duration_stats', {}).get('avg', 0)
            for job_data in jobs.values()
        )
        
        if sequential_time > 1800:  # More than 30 minutes total
            recommendations.append(
                f"⏱️ Total sequential time is {sequential_time/60:.1f} minutes. "
                f"Consider increasing parallelization."
            )
        
        return recommendations
    
    def analyze_caching_opportunities(self) -> List[str]:
        """Identify caching optimization opportunities."""
        recommendations = []
        
        jobs = self.analysis.get('jobs', {})
        for job_name, job_data in jobs.items():
            bottlenecks = job_data.get('bottlenecks', [])
            
            # Look for dependency installation bottlenecks
            for bottleneck in bottlenecks:
                stage = bottleneck.get('stage', '')
                avg_time = bottleneck.get('avg_duration', 0)
                
                if 'dependencies' in stage.lower() and avg_time > 60:
                    recommendations.append(
                        f"📦 **{job_name}** spends {avg_time:.1f}s on dependencies. "
                        f"Implement smart caching or pre-built environments."
                    )
                
                if 'build' in stage.lower() and avg_time > 120:
                    recommendations.append(
                        f"🏗️ **{job_name}** spends {avg_time:.1f}s on builds. "
                        f"Consider incremental builds or build caching."
                    )
        
        return recommendations
    
    def analyze_test_optimization(self) -> List[str]:
        """Identify test optimization opportunities."""
        recommendations = []
        
        jobs = self.analysis.get('jobs', {})
        for job_name, job_data in jobs.items():
            if 'test' in job_name.lower():
                duration_stats = job_data.get('duration_stats', {})
                avg_duration = duration_stats.get('avg', 0)
                
                # Long test suites
                if avg_duration > 300:  # More than 5 minutes
                    recommendations.append(
                        f"🧪 **{job_name}** tests take {avg_duration/60:.1f} minutes. "
                        f"Consider test selection, parallelization, or optimization."
                    )
                
                # Check for test-specific bottlenecks
                bottlenecks = job_data.get('bottlenecks', [])
                for bottleneck in bottlenecks:
                    stage = bottleneck.get('stage', '')
                    if 'test' in stage.lower():
                        avg_time = bottleneck.get('avg_duration', 0)
                        if avg_time > 180:  # More than 3 minutes
                            recommendations.append(
                                f"🔬 **{job_name}.{stage}** is slow ({avg_time:.1f}s). "
                                f"Profile and optimize slow tests."
                            )
        
        return recommendations
    
    def analyze_infrastructure_optimization(self) -> List[str]:
        """Identify infrastructure optimization opportunities."""
        recommendations = []
        
        summary = self.analysis.get('summary', {})
        job_types = summary.get('job_types', 0)
        
        # Many different job types might indicate fragmentation
        if job_types > 10:
            recommendations.append(
                f"🏗️ {job_types} different job types detected. "
                f"Consider consolidating similar jobs to reduce overhead."
            )
        
        # Look for runner efficiency opportunities
        jobs = self.analysis.get('jobs', {})
        github_hosted_jobs = []
        self_hosted_jobs = []
        
        for job_name in jobs.keys():
            if 'github' in job_name.lower() or 'ubuntu' in job_name.lower():
                github_hosted_jobs.append(job_name)
            elif 'self-hosted' in job_name.lower() or 'macos' in job_name.lower():
                self_hosted_jobs.append(job_name)
        
        if len(github_hosted_jobs) > len(self_hosted_jobs) * 2:
            recommendations.append(
                f"💰 Many jobs on GitHub-hosted runners ({len(github_hosted_jobs)}). "
                f"Consider migrating suitable jobs to self-hosted runners for cost savings."
            )
        
        return recommendations
    
    def generate_priority_matrix(self) -> Dict[str, List[str]]:
        """Categorize recommendations by priority."""
        all_recommendations = []
        
        all_recommendations.extend(self.analyze_duration_bottlenecks())
        all_recommendations.extend(self.analyze_failure_patterns())
        all_recommendations.extend(self.analyze_resource_utilization())
        all_recommendations.extend(self.analyze_caching_opportunities())
        all_recommendations.extend(self.analyze_test_optimization())
        all_recommendations.extend(self.analyze_infrastructure_optimization())
        
        # Categorize by priority based on severity indicators
        priority_matrix = {
            'critical': [],
            'high': [],
            'medium': [],
            'low': []
        }
        
        for rec in all_recommendations:
            if any(indicator in rec for indicator in ['🔴', 'critical bottleneck', 'failure rate']):
                priority_matrix['critical'].append(rec)
            elif any(indicator in rec for indicator in ['🐌', '❌', 'takes']):
                priority_matrix['high'].append(rec)
            elif any(indicator in rec for indicator in ['🟡', '⚡', '⚠️']):
                priority_matrix['medium'].append(rec)
            else:
                priority_matrix['low'].append(rec)
        
        return priority_matrix
    
    def generate_implementation_plan(self, priority_matrix: Dict[str, List[str]]) -> List[str]:
        """Generate a practical implementation plan."""
        plan = []
        
        # Immediate actions (Week 1)
        if priority_matrix['critical']:
            plan.append("### 🚨 Immediate Actions (Week 1)")
            plan.extend(f"- {rec}" for rec in priority_matrix['critical'])
            plan.append("")
        
        # Short-term improvements (Weeks 2-4)
        if priority_matrix['high']:
            plan.append("### ⚡ Short-term Improvements (Weeks 2-4)")
            plan.extend(f"- {rec}" for rec in priority_matrix['high'])
            plan.append("")
        
        # Medium-term optimizations (Month 2)
        if priority_matrix['medium']:
            plan.append("### 🔧 Medium-term Optimizations (Month 2)")
            plan.extend(f"- {rec}" for rec in priority_matrix['medium'])
            plan.append("")
        
        # Long-term enhancements (Month 3+)
        if priority_matrix['low']:
            plan.append("### 📈 Long-term Enhancements (Month 3+)")
            plan.extend(f"- {rec}" for rec in priority_matrix['low'])
            plan.append("")
        
        return plan
    
    def generate_metrics_to_track(self) -> List[str]:
        """Generate metrics to track improvement."""
        metrics = [
            "📊 **Key Metrics to Track:**",
            "",
            "- **Average build time** (target: <10 minutes)",
            "- **Success rate** (target: >98%)",
            "- **Queue time** (target: <30 seconds)",
            "- **Cache hit rate** (target: >80%)",
            "- **Test execution time** (target: <5 minutes)",
            "- **Dependency installation time** (target: <2 minutes)",
            "- **Parallel job efficiency** (target: >75%)",
            ""
        ]
        return metrics
    
    def generate_cost_impact_analysis(self) -> List[str]:
        """Estimate cost impact of recommendations."""
        summary = self.analysis.get('summary', {})
        total_runs = summary.get('total_runs', 0)
        
        # Rough cost estimates based on typical CI usage
        avg_build_time = 15  # minutes, estimated
        github_hosted_cost_per_minute = 0.008  # USD for Linux runners
        
        current_monthly_cost = total_runs * avg_build_time * github_hosted_cost_per_minute * 4  # 4 weeks
        
        analysis = [
            "💰 **Cost Impact Analysis:**",
            "",
            f"- Current estimated monthly cost: ~${current_monthly_cost:.2f}",
            f"- Potential savings with optimizations:",
            f"  - 25% faster builds: ~${current_monthly_cost * 0.25:.2f}/month",
            f"  - 50% cache hit rate improvement: ~${current_monthly_cost * 0.3:.2f}/month",
            f"  - Self-hosted runner migration: ~${current_monthly_cost * 0.6:.2f}/month",
            ""
        ]
        return analysis


def generate_recommendations_report(analysis_file: str) -> str:
    """Generate a comprehensive recommendations report."""
    try:
        with open(analysis_file, 'r') as f:
            analysis_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return f"Error loading analysis file: {e}"
    
    engine = CIRecommendationEngine(analysis_data)
    priority_matrix = engine.generate_priority_matrix()
    
    # Check if there are any recommendations
    total_recommendations = sum(len(recs) for recs in priority_matrix.values())
    if total_recommendations == 0:
        return """# CI Performance Optimization Recommendations

## 🎉 Excellent Performance!

No optimization recommendations at this time. Your CI pipeline is performing well within expected parameters.

### Current Status
- Build times are within acceptable ranges
- Success rates are high
- No critical bottlenecks identified

### Maintenance Recommendations
- Continue monitoring performance trends
- Review this analysis monthly
- Keep dependencies up to date
- Consider proactive optimizations as the codebase grows

*Generated on {date}*
""".format(date=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    
    # Generate full report
    report_lines = [
        "# CI Performance Optimization Recommendations",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Analysis Period:** {analysis_data.get('summary', {}).get('time_range', {}).get('start', 'Unknown')} to {analysis_data.get('summary', {}).get('time_range', {}).get('end', 'Unknown')}",
        f"**Total Recommendations:** {total_recommendations}",
        "",
        "## 📋 Executive Summary",
        "",
        f"This analysis identified **{total_recommendations} optimization opportunities** across your CI pipeline. "
        f"Implementing these recommendations could improve build speed, reduce costs, and increase reliability.",
        ""
    ]
    
    # Add implementation plan
    implementation_plan = engine.generate_implementation_plan(priority_matrix)
    if implementation_plan:
        report_lines.append("## 🗓️ Implementation Plan")
        report_lines.append("")
        report_lines.extend(implementation_plan)
    
    # Add metrics to track
    metrics = engine.generate_metrics_to_track()
    report_lines.extend(metrics)
    
    # Add cost analysis
    cost_analysis = engine.generate_cost_impact_analysis()
    report_lines.extend(cost_analysis)
    
    # Add detailed recommendations by category
    if any(priority_matrix.values()):
        report_lines.extend([
            "## 📝 Detailed Recommendations",
            "",
            "*The following recommendations are organized by priority and impact.*",
            ""
        ])
        
        for priority, recommendations in priority_matrix.items():
            if recommendations:
                priority_emoji = {
                    'critical': '🚨',
                    'high': '🔥',
                    'medium': '📈',
                    'low': '💡'
                }
                
                report_lines.append(f"### {priority_emoji.get(priority, '•')} {priority.title()} Priority")
                report_lines.append("")
                report_lines.extend(recommendations)
                report_lines.append("")
    
    # Add footer
    report_lines.extend([
        "---",
        "",
        "💡 **Next Steps:**",
        "1. Review and prioritize recommendations based on your team's capacity",
        "2. Create tracking issues for high-priority items",
        "3. Implement monitoring for key metrics",
        "4. Schedule regular performance reviews",
        "",
        "*This report was automatically generated by the CI Performance Monitor.*"
    ])
    
    return "\n".join(report_lines)


def main():
    """Main CLI interface."""
    parser = argparse.ArgumentParser(description='Generate CI optimization recommendations')
    parser.add_argument('analysis_file', help='Path to CI analysis JSON file')
    parser.add_argument('--output', help='Output file path (default: stdout)')
    
    args = parser.parse_args()
    
    # Generate recommendations
    report = generate_recommendations_report(args.analysis_file)
    
    # Output report
    if args.output:
        with open(args.output, 'w') as f:
            f.write(report)
        print(f"Recommendations saved to {args.output}")
    else:
        print(report)


if __name__ == '__main__':
    main()