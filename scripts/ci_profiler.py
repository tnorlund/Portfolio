#!/usr/bin/env python3
"""
Simple CI Profiler - Core metrics collection for CI performance monitoring

This is a simplified version focused on basic metrics collection and analysis.
Part of incremental CI optimization implementation (PR 1/4).

Usage:
    python scripts/ci_profiler.py --collect --job-name "test-job"
    python scripts/ci_profiler.py --analyze --days 7
    python scripts/ci_profiler.py --report
"""

import argparse
import json
import os
import sys
import time
import psutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any


class SimpleCIProfiler:
    """Basic CI profiler for collecting and analyzing performance metrics."""
    
    def __init__(self):
        self.metrics_dir = Path('ci-metrics')
        self.metrics_dir.mkdir(exist_ok=True)
        
    def collect_basic_metrics(self, job_name: str) -> Dict[str, Any]:
        """Collect basic system and timing metrics."""
        start_time = datetime.now()
        
        # Basic system info
        system_info = {
            'cpu_count': psutil.cpu_count(),
            'memory_total': psutil.virtual_memory().total,
            'memory_available': psutil.virtual_memory().available,
            'disk_usage': psutil.disk_usage('/').percent,
            'python_version': sys.version,
            'platform': sys.platform
        }
        
        metrics = {
            'job_name': job_name,
            'timestamp': start_time.isoformat(),
            'system_info': system_info,
            'start_time': start_time.isoformat()
        }
        
        return metrics
    
    def save_metrics(self, metrics: Dict[str, Any]) -> None:
        """Save metrics to file."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = self.metrics_dir / f"metrics_{metrics['job_name']}_{timestamp}.json"
        
        with open(filename, 'w') as f:
            json.dump(metrics, f, indent=2)
        
        print(f"✅ Metrics saved to {filename}")
    
    def load_recent_metrics(self, days: int = 7) -> List[Dict[str, Any]]:
        """Load metrics from the last N days."""
        cutoff_date = datetime.now() - timedelta(days=days)
        metrics = []
        
        for file_path in self.metrics_dir.glob("metrics_*.json"):
            try:
                # Extract timestamp from filename
                timestamp_str = file_path.stem.split('_')[-2] + '_' + file_path.stem.split('_')[-1]
                file_time = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                
                if file_time >= cutoff_date:
                    with open(file_path, 'r') as f:
                        metrics.append(json.load(f))
            except (ValueError, json.JSONDecodeError, IndexError):
                continue
        
        return sorted(metrics, key=lambda x: x['timestamp'])
    
    def analyze_metrics(self, days: int = 7) -> Dict[str, Any]:
        """Analyze collected metrics to identify trends and issues."""
        metrics = self.load_recent_metrics(days)
        
        if not metrics:
            return {'error': 'No metrics found'}
        
        # Group by job name
        jobs = {}
        for metric in metrics:
            job_name = metric['job_name']
            if job_name not in jobs:
                jobs[job_name] = []
            jobs[job_name].append(metric)
        
        analysis = {
            'summary': {
                'total_runs': len(metrics),
                'unique_jobs': len(jobs),
                'time_range': {
                    'start': metrics[0]['timestamp'],
                    'end': metrics[-1]['timestamp']
                }
            },
            'jobs': {}
        }
        
        # Basic analysis per job
        for job_name, job_metrics in jobs.items():
            analysis['jobs'][job_name] = {
                'run_count': len(job_metrics),
                'avg_memory_usage': sum(m['system_info']['memory_total'] - m['system_info']['memory_available'] 
                                      for m in job_metrics) / len(job_metrics),
                'avg_cpu_count': sum(m['system_info']['cpu_count'] for m in job_metrics) / len(job_metrics)
            }
        
        return analysis
    
    def generate_simple_report(self, analysis: Dict[str, Any]) -> str:
        """Generate a simple text report."""
        if 'error' in analysis:
            return f"Error: {analysis['error']}"
        
        lines = [
            "# CI Performance Report",
            "",
            f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Analysis Period:** {analysis['summary']['time_range']['start']} to {analysis['summary']['time_range']['end']}",
            "",
            "## Summary",
            f"- Total CI runs: {analysis['summary']['total_runs']}",
            f"- Unique jobs: {analysis['summary']['unique_jobs']}",
            "",
            "## Job Performance",
            ""
        ]
        
        for job_name, job_data in analysis['jobs'].items():
            lines.extend([
                f"### {job_name}",
                f"- Runs: {job_data['run_count']}",
                f"- Avg Memory Used: {job_data['avg_memory_usage'] / (1024**3):.1f} GB",
                f"- Avg CPU Count: {job_data['avg_cpu_count']:.1f}",
                ""
            ])
        
        return "\n".join(lines)


def main():
    """Main CLI interface."""
    parser = argparse.ArgumentParser(description='Simple CI Performance Profiler')
    parser.add_argument('--collect', action='store_true', help='Collect metrics')
    parser.add_argument('--job-name', required='--collect' in sys.argv, help='Job name for metrics collection')
    parser.add_argument('--analyze', action='store_true', help='Analyze collected metrics')
    parser.add_argument('--report', action='store_true', help='Generate performance report')
    parser.add_argument('--days', type=int, default=7, help='Days to analyze (default: 7)')
    
    args = parser.parse_args()
    
    profiler = SimpleCIProfiler()
    
    if args.collect:
        print(f"📊 Collecting metrics for job: {args.job_name}")
        metrics = profiler.collect_basic_metrics(args.job_name)
        profiler.save_metrics(metrics)
        return
    
    if args.analyze:
        print(f"📈 Analyzing metrics from last {args.days} days...")
        analysis = profiler.analyze_metrics(args.days)
        
        # Save analysis
        analysis_file = profiler.metrics_dir / f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(analysis_file, 'w') as f:
            json.dump(analysis, f, indent=2)
        
        print(f"✅ Analysis saved to {analysis_file}")
        return
    
    if args.report:
        print("📋 Generating performance report...")
        analysis = profiler.analyze_metrics(args.days)
        report = profiler.generate_simple_report(analysis)
        
        # Save report
        report_file = profiler.metrics_dir / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        with open(report_file, 'w') as f:
            f.write(report)
        
        print(report)
        print(f"\n📄 Report saved to {report_file}")
        return
    
    parser.print_help()


if __name__ == '__main__':
    main()