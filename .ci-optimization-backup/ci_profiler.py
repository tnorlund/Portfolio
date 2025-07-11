#!/usr/bin/env python3
"""
CI Profiler - Captures and analyzes CI test performance data

This script provides comprehensive profiling of CI test execution to identify
bottlenecks and optimization opportunities.

Usage:
    python scripts/ci_profiler.py --collect --job-name "test-python-receipt_dynamo-unit"
    python scripts/ci_profiler.py --analyze --days 7
    python scripts/ci_profiler.py --report --format json
"""

import argparse
import json
import time
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import hashlib


class CIProfiler:
    """Profiles CI test execution and provides optimization insights."""
    
    def __init__(self, data_dir: str = "ci-metrics"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        
        # CI environment detection
        self.ci_environment = self._detect_ci_environment()
        self.job_name = os.environ.get('GITHUB_JOB', 'unknown')
        self.run_id = os.environ.get('GITHUB_RUN_ID', 'local')
        self.run_number = os.environ.get('GITHUB_RUN_NUMBER', '0')
        
    def _detect_ci_environment(self) -> str:
        """Detect the CI environment we're running in."""
        if os.environ.get('GITHUB_ACTIONS'):
            return 'github-actions'
        elif os.environ.get('JENKINS_URL'):
            return 'jenkins'
        elif os.environ.get('GITLAB_CI'):
            return 'gitlab'
        else:
            return 'local'
    
    def _get_system_info(self) -> Dict:
        """Collect system information for profiling context."""
        try:
            import psutil
            cpu_count = psutil.cpu_count(logical=False)
            memory_gb = psutil.virtual_memory().total / (1024**3)
            disk_usage = psutil.disk_usage('/').free / (1024**3)
        except ImportError:
            # Fallback for systems without psutil
            cpu_count = os.cpu_count()
            memory_gb = 0
            disk_usage = 0
        
        return {
            'cpu_count': cpu_count,
            'memory_gb': round(memory_gb, 2),
            'disk_free_gb': round(disk_usage, 2),
            'platform': sys.platform,
            'python_version': sys.version.split()[0],
            'runner_os': os.environ.get('RUNNER_OS', 'unknown'),
            'runner_arch': os.environ.get('RUNNER_ARCH', 'unknown'),
        }
    
    def _run_command_with_timing(self, command: List[str], cwd: Optional[Path] = None) -> Tuple[int, float, str]:
        """Run a command and measure execution time."""
        start_time = time.time()
        
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=1800  # 30 minute timeout
            )
            end_time = time.time()
            
            return result.returncode, end_time - start_time, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            end_time = time.time()
            return 124, end_time - start_time, "Command timed out"
        except Exception as e:
            end_time = time.time()
            return 1, end_time - start_time, str(e)
    
    def profile_dependency_installation(self, package_dir: str) -> Dict:
        """Profile dependency installation time."""
        print(f"📦 Profiling dependency installation for {package_dir}")
        
        # Python dependencies
        pip_commands = [
            (['python3', '-m', 'pip', 'install', '--upgrade', 'pip'], 'pip_upgrade'),
            (['python3', '-m', 'pip', 'install', '-e', f'{package_dir}[test]'], 'package_install'),
        ]
        
        results = {}
        for command, name in pip_commands:
            returncode, duration, output = self._run_command_with_timing(command)
            results[name] = {
                'duration': duration,
                'success': returncode == 0,
                'output_lines': len(output.splitlines()),
                'command': ' '.join(command)
            }
            print(f"  {name}: {duration:.2f}s ({'✅' if returncode == 0 else '❌'})")
        
        return results
    
    def profile_test_execution(self, test_command: List[str], cwd: Optional[Path] = None) -> Dict:
        """Profile test execution with detailed timing."""
        print(f"🧪 Profiling test execution: {' '.join(test_command)}")
        
        # Run the test command with timing
        returncode, duration, output = self._run_command_with_timing(test_command, cwd)
        
        # Parse test output for additional metrics
        test_metrics = self._parse_test_output(output)
        
        result = {
            'duration': duration,
            'success': returncode == 0,
            'command': ' '.join(test_command),
            'returncode': returncode,
            'output_lines': len(output.splitlines()),
            'test_metrics': test_metrics
        }
        
        print(f"  Test execution: {duration:.2f}s ({'✅' if returncode == 0 else '❌'})")
        if test_metrics:
            print(f"  Tests: {test_metrics.get('total', 0)} total, {test_metrics.get('passed', 0)} passed")
        
        return result
    
    def _parse_test_output(self, output: str) -> Dict:
        """Parse test output to extract metrics."""
        metrics = {}
        
        # Parse pytest output
        if 'pytest' in output or 'collected' in output:
            lines = output.splitlines()
            for line in lines:
                if 'collected' in line and 'item' in line:
                    # Extract collected test count
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if part == 'collected' and i + 1 < len(parts):
                            try:
                                metrics['total'] = int(parts[i + 1])
                            except ValueError:
                                pass
                
                # Parse final test results
                if line.startswith('='):
                    if 'passed' in line:
                        # Extract passed/failed counts
                        parts = line.split()
                        for part in parts:
                            if 'passed' in part:
                                try:
                                    metrics['passed'] = int(part.split('passed')[0])
                                except ValueError:
                                    pass
                            if 'failed' in part:
                                try:
                                    metrics['failed'] = int(part.split('failed')[0])
                                except ValueError:
                                    pass
        
        # Parse npm test output
        if 'npm' in output and 'test' in output:
            lines = output.splitlines()
            for line in lines:
                if 'Tests:' in line:
                    # Extract Jest test counts
                    parts = line.split(',')
                    for part in parts:
                        if 'passed' in part:
                            try:
                                metrics['passed'] = int(part.split()[0])
                            except ValueError:
                                pass
                        if 'failed' in part:
                            try:
                                metrics['failed'] = int(part.split()[0])
                            except ValueError:
                                pass
        
        return metrics
    
    def profile_build_process(self, build_command: List[str], cwd: Optional[Path] = None) -> Dict:
        """Profile build process timing."""
        print(f"🏗️  Profiling build process: {' '.join(build_command)}")
        
        returncode, duration, output = self._run_command_with_timing(build_command, cwd)
        
        result = {
            'duration': duration,
            'success': returncode == 0,
            'command': ' '.join(build_command),
            'returncode': returncode,
            'output_lines': len(output.splitlines())
        }
        
        print(f"  Build process: {duration:.2f}s ({'✅' if returncode == 0 else '❌'})")
        
        return result
    
    def collect_ci_metrics(self, job_config: Dict) -> Dict:
        """Collect comprehensive CI metrics for a job."""
        print(f"📊 Collecting CI metrics for job: {self.job_name}")
        
        start_time = time.time()
        
        metrics = {
            'job_name': self.job_name,
            'run_id': self.run_id,
            'run_number': self.run_number,
            'ci_environment': self.ci_environment,
            'timestamp': datetime.now().isoformat(),
            'system_info': self._get_system_info(),
            'git_info': self._get_git_info(),
            'total_duration': 0,
            'stages': {}
        }
        
        # Profile different stages based on job config
        if 'dependencies' in job_config:
            metrics['stages']['dependencies'] = self.profile_dependency_installation(
                job_config['dependencies']
            )
        
        if 'build' in job_config:
            metrics['stages']['build'] = self.profile_build_process(
                job_config['build']['command'],
                job_config['build'].get('cwd')
            )
        
        if 'tests' in job_config:
            metrics['stages']['tests'] = self.profile_test_execution(
                job_config['tests']['command'],
                job_config['tests'].get('cwd')
            )
        
        # Calculate total duration
        metrics['total_duration'] = time.time() - start_time
        
        # Save metrics
        self._save_metrics(metrics)
        
        return metrics
    
    def _get_git_info(self) -> Dict:
        """Get git repository information."""
        try:
            commit_hash = subprocess.check_output(
                ['git', 'rev-parse', 'HEAD'],
                text=True
            ).strip()
            
            branch = subprocess.check_output(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                text=True
            ).strip()
            
            return {
                'commit_hash': commit_hash,
                'branch': branch,
                'short_hash': commit_hash[:7] if commit_hash else 'unknown'
            }
        except subprocess.CalledProcessError:
            return {
                'commit_hash': 'unknown',
                'branch': 'unknown',
                'short_hash': 'unknown'
            }
    
    def _save_metrics(self, metrics: Dict) -> None:
        """Save metrics to file."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{self.job_name}_{timestamp}.json"
        filepath = self.data_dir / filename
        
        with open(filepath, 'w') as f:
            json.dump(metrics, f, indent=2)
        
        print(f"📄 Metrics saved to: {filepath}")
    
    def analyze_metrics(self, days: int = 7) -> Dict:
        """Analyze CI metrics over the specified time period."""
        print(f"📈 Analyzing CI metrics for the last {days} days")
        
        cutoff_date = datetime.now() - timedelta(days=days)
        metrics_files = []
        
        for file_path in self.data_dir.glob("*.json"):
            if file_path.stat().st_mtime > cutoff_date.timestamp():
                metrics_files.append(file_path)
        
        if not metrics_files:
            print("No metrics files found for the specified period")
            return {}
        
        # Load and analyze all metrics
        all_metrics = []
        for file_path in metrics_files:
            try:
                with open(file_path, 'r') as f:
                    metrics = json.load(f)
                    all_metrics.append(metrics)
            except json.JSONDecodeError:
                print(f"Warning: Could not parse {file_path}")
        
        return self._analyze_metrics_data(all_metrics)
    
    def _analyze_metrics_data(self, metrics_list: List[Dict]) -> Dict:
        """Analyze a list of metrics to identify trends and bottlenecks."""
        if not metrics_list:
            return {}
        
        # Group by job name
        jobs = {}
        for metrics in metrics_list:
            job_name = metrics.get('job_name', 'unknown')
            if job_name not in jobs:
                jobs[job_name] = []
            jobs[job_name].append(metrics)
        
        analysis = {
            'summary': {
                'total_runs': len(metrics_list),
                'job_types': len(jobs),
                'time_range': {
                    'start': min(m['timestamp'] for m in metrics_list),
                    'end': max(m['timestamp'] for m in metrics_list)
                }
            },
            'jobs': {}
        }
        
        # Analyze each job type
        for job_name, job_metrics in jobs.items():
            durations = [m['total_duration'] for m in job_metrics]
            
            job_analysis = {
                'runs': len(job_metrics),
                'duration_stats': {
                    'min': min(durations),
                    'max': max(durations),
                    'avg': sum(durations) / len(durations),
                    'median': sorted(durations)[len(durations) // 2]
                },
                'success_rate': sum(1 for m in job_metrics if self._is_successful(m)) / len(job_metrics),
                'bottlenecks': self._identify_bottlenecks(job_metrics)
            }
            
            analysis['jobs'][job_name] = job_analysis
        
        return analysis
    
    def _is_successful(self, metrics: Dict) -> bool:
        """Check if a CI run was successful."""
        if 'stages' not in metrics:
            return False
        
        for stage_name, stage_data in metrics['stages'].items():
            if isinstance(stage_data, dict) and not stage_data.get('success', False):
                return False
        
        return True
    
    def _identify_bottlenecks(self, job_metrics: List[Dict]) -> List[Dict]:
        """Identify performance bottlenecks in a job."""
        bottlenecks = []
        
        # Collect stage durations
        stage_durations = {}
        for metrics in job_metrics:
            if 'stages' in metrics:
                for stage_name, stage_data in metrics['stages'].items():
                    if isinstance(stage_data, dict) and 'duration' in stage_data:
                        if stage_name not in stage_durations:
                            stage_durations[stage_name] = []
                        stage_durations[stage_name].append(stage_data['duration'])
        
        # Find stages that take longer than average
        for stage_name, durations in stage_durations.items():
            if durations:
                avg_duration = sum(durations) / len(durations)
                max_duration = max(durations)
                
                if avg_duration > 60:  # More than 1 minute
                    bottlenecks.append({
                        'stage': stage_name,
                        'avg_duration': avg_duration,
                        'max_duration': max_duration,
                        'severity': 'high' if avg_duration > 300 else 'medium'
                    })
        
        return sorted(bottlenecks, key=lambda x: x['avg_duration'], reverse=True)
    
    def generate_report(self, analysis: Dict, format: str = 'text') -> str:
        """Generate a human-readable report from analysis data."""
        if format == 'json':
            return json.dumps(analysis, indent=2)
        
        report = []
        report.append("# CI Performance Analysis Report")
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        # Summary
        summary = analysis.get('summary', {})
        report.append("## Summary")
        report.append(f"- Total runs analyzed: {summary.get('total_runs', 0)}")
        report.append(f"- Job types: {summary.get('job_types', 0)}")
        
        time_range = summary.get('time_range', {})
        if time_range:
            report.append(f"- Time range: {time_range.get('start', 'unknown')} to {time_range.get('end', 'unknown')}")
        report.append("")
        
        # Job analysis
        jobs = analysis.get('jobs', {})
        if jobs:
            report.append("## Job Performance Analysis")
            
            for job_name, job_data in jobs.items():
                report.append(f"### {job_name}")
                
                duration_stats = job_data.get('duration_stats', {})
                report.append(f"- Runs: {job_data.get('runs', 0)}")
                report.append(f"- Success rate: {job_data.get('success_rate', 0):.1%}")
                report.append(f"- Average duration: {duration_stats.get('avg', 0):.1f}s")
                report.append(f"- Duration range: {duration_stats.get('min', 0):.1f}s - {duration_stats.get('max', 0):.1f}s")
                
                bottlenecks = job_data.get('bottlenecks', [])
                if bottlenecks:
                    report.append("- **Bottlenecks:**")
                    for bottleneck in bottlenecks:
                        severity = bottleneck.get('severity', 'low')
                        stage = bottleneck.get('stage', 'unknown')
                        avg_duration = bottleneck.get('avg_duration', 0)
                        
                        severity_emoji = {'high': '🔴', 'medium': '🟡', 'low': '🟢'}.get(severity, '⚪')
                        report.append(f"  - {severity_emoji} {stage}: {avg_duration:.1f}s avg")
                
                report.append("")
        
        return "\n".join(report)


def main():
    """Main CLI interface."""
    parser = argparse.ArgumentParser(description='CI Performance Profiler')
    parser.add_argument('--collect', action='store_true', help='Collect CI metrics')
    parser.add_argument('--analyze', action='store_true', help='Analyze collected metrics')
    parser.add_argument('--report', action='store_true', help='Generate performance report')
    parser.add_argument('--job-name', help='Job name for collection')
    parser.add_argument('--days', type=int, default=7, help='Days to analyze (default: 7)')
    parser.add_argument('--format', choices=['text', 'json'], default='text', help='Report format')
    parser.add_argument('--config', help='JSON config file for collection')
    
    args = parser.parse_args()
    
    profiler = CIProfiler()
    
    if args.collect:
        if args.config:
            with open(args.config, 'r') as f:
                job_config = json.load(f)
        else:
            # Default config for simple collection
            job_config = {}
        
        metrics = profiler.collect_ci_metrics(job_config)
        print(f"✅ Collected metrics for {metrics['job_name']}")
    
    elif args.analyze:
        analysis = profiler.analyze_metrics(args.days)
        if analysis:
            print("📊 Analysis complete!")
            if args.format == 'json':
                print(json.dumps(analysis, indent=2))
            else:
                print("\n" + profiler.generate_report(analysis))
        else:
            print("❌ No data available for analysis")
    
    elif args.report:
        analysis = profiler.analyze_metrics(args.days)
        if analysis:
            report = profiler.generate_report(analysis, args.format)
            print(report)
        else:
            print("❌ No data available for report generation")
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()