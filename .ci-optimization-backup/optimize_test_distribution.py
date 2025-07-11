#!/usr/bin/env python3
"""
Test Distribution Optimizer - Optimizes test distribution across parallel jobs

This script analyzes test execution times and automatically rebalances test groups
to minimize total CI time through optimal parallelization.

Usage:
    python scripts/optimize_test_distribution.py --analyze package_name
    python scripts/optimize_test_distribution.py --optimize package_name --groups 4
    python scripts/optimize_test_distribution.py --generate-matrix package_name
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Set
import math
import heapq
from datetime import datetime, timedelta
from collections import defaultdict


class TestExecutionProfiler:
    """Profiles test execution times to understand performance characteristics."""
    
    def __init__(self, package_root: Path):
        self.package_root = package_root
        self.execution_history = Path('.test-history')
        self.execution_history.mkdir(exist_ok=True)
    
    def profile_test_file(self, test_file: Path) -> Dict[str, float]:
        """Profile execution time of a single test file."""
        print(f"📊 Profiling {test_file.name}...")
        
        cmd = [
            'python', '-m', 'pytest',
            str(test_file),
            '-v',
            '--tb=no',
            '--quiet',
            '--disable-warnings',
            '--durations=0'
        ]
        
        try:
            start_time = datetime.now()
            result = subprocess.run(
                cmd,
                cwd=self.package_root,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout per file
            )
            end_time = datetime.now()
            
            total_duration = (end_time - start_time).total_seconds()
            
            # Parse pytest durations output for individual test times
            test_times = self._parse_pytest_durations(result.stdout)
            
            return {
                'total_duration': total_duration,
                'individual_tests': test_times,
                'success': result.returncode == 0,
                'test_count': len(test_times)
            }
        
        except subprocess.TimeoutExpired:
            print(f"⏰ Timeout profiling {test_file.name}")
            return {
                'total_duration': 300.0,
                'individual_tests': {},
                'success': False,
                'test_count': 0
            }
        except Exception as e:
            print(f"❌ Error profiling {test_file.name}: {e}")
            return {
                'total_duration': 60.0,  # Default estimate
                'individual_tests': {},
                'success': False,
                'test_count': 0
            }
    
    def _parse_pytest_durations(self, output: str) -> Dict[str, float]:
        """Parse pytest durations output to extract individual test times."""
        test_times = {}
        
        lines = output.split('\n')
        in_durations_section = False
        
        for line in lines:
            if 'slowest durations' in line.lower():
                in_durations_section = True
                continue
            
            if in_durations_section:
                if line.strip() and not line.startswith('='):
                    # Parse lines like: "0.12s call test_file.py::test_function"
                    parts = line.strip().split()
                    if len(parts) >= 3 and parts[0].endswith('s'):
                        try:
                            duration = float(parts[0][:-1])  # Remove 's' suffix
                            test_name = parts[-1]  # Last part is test name
                            test_times[test_name] = duration
                        except ValueError:
                            continue
                elif line.startswith('='):
                    break
        
        return test_times
    
    def profile_all_tests(self, test_pattern: str = "test_*.py") -> Dict[str, Dict]:
        """Profile all test files matching the pattern."""
        test_files = list(self.package_root.glob(f"**/{test_pattern}"))
        
        if not test_files:
            print(f"No test files found matching {test_pattern}")
            return {}
        
        print(f"🔍 Found {len(test_files)} test files to profile")
        
        profiles = {}
        for i, test_file in enumerate(test_files, 1):
            print(f"[{i}/{len(test_files)}] Profiling {test_file.relative_to(self.package_root)}")
            
            profile = self.profile_test_file(test_file)
            relative_path = str(test_file.relative_to(self.package_root))
            profiles[relative_path] = profile
            
            # Save intermediate results
            self._save_profile_data(profiles)
        
        return profiles
    
    def _save_profile_data(self, profiles: Dict[str, Dict]) -> None:
        """Save profiling data to disk."""
        profile_file = self.execution_history / f"test_profiles_{self.package_root.name}.json"
        
        with open(profile_file, 'w') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'package': self.package_root.name,
                'profiles': profiles
            }, f, indent=2)
    
    def load_historical_data(self) -> Dict[str, Dict]:
        """Load historical profiling data."""
        profile_file = self.execution_history / f"test_profiles_{self.package_root.name}.json"
        
        if not profile_file.exists():
            return {}
        
        try:
            with open(profile_file, 'r') as f:
                data = json.load(f)
                return data.get('profiles', {})
        except (json.JSONDecodeError, KeyError):
            return {}


class TestDistributionOptimizer:
    """Optimizes distribution of tests across parallel jobs."""
    
    def __init__(self, test_profiles: Dict[str, Dict]):
        self.test_profiles = test_profiles
        self.total_time = sum(profile['total_duration'] for profile in test_profiles.values())
    
    def calculate_optimal_groups(self, target_groups: int) -> List[List[str]]:
        """Calculate optimal test distribution using bin packing algorithm."""
        if not self.test_profiles:
            return []
        
        # Sort tests by duration (largest first) for better bin packing
        sorted_tests = sorted(
            self.test_profiles.items(),
            key=lambda x: x[1]['total_duration'],
            reverse=True
        )
        
        # Initialize groups with their current total time
        groups = [[] for _ in range(target_groups)]
        group_times = [0.0] * target_groups
        
        # Assign each test to the group with least total time (First Fit Decreasing)
        for test_name, profile in sorted_tests:
            # Find group with minimum time
            min_group_idx = min(range(target_groups), key=lambda i: group_times[i])
            
            # Assign test to this group
            groups[min_group_idx].append(test_name)
            group_times[min_group_idx] += profile['total_duration']
        
        return groups
    
    def calculate_load_balance_score(self, groups: List[List[str]]) -> float:
        """Calculate how well-balanced the test distribution is (0-1, higher is better)."""
        if not groups:
            return 0.0
        
        group_times = []
        for group in groups:
            total_time = sum(
                self.test_profiles.get(test, {}).get('total_duration', 0)
                for test in group
            )
            group_times.append(total_time)
        
        if not group_times or max(group_times) == 0:
            return 1.0
        
        # Calculate coefficient of variation (lower is better)
        avg_time = sum(group_times) / len(group_times)
        variance = sum((t - avg_time) ** 2 for t in group_times) / len(group_times)
        std_dev = math.sqrt(variance)
        
        if avg_time == 0:
            return 1.0
        
        cv = std_dev / avg_time
        # Convert to score (0-1, higher is better)
        return max(0.0, 1.0 - cv)
    
    def analyze_current_distribution(self, current_groups: List[List[str]]) -> Dict:
        """Analyze the current test distribution."""
        analysis = {
            'group_count': len(current_groups),
            'total_tests': sum(len(group) for group in current_groups),
            'groups': []
        }
        
        for i, group in enumerate(current_groups):
            group_time = sum(
                self.test_profiles.get(test, {}).get('total_duration', 0)
                for test in group
            )
            
            group_analysis = {
                'group_id': i + 1,
                'test_count': len(group),
                'total_time': group_time,
                'avg_time_per_test': group_time / len(group) if group else 0,
                'tests': group
            }
            
            analysis['groups'].append(group_analysis)
        
        # Calculate overall metrics
        group_times = [g['total_time'] for g in analysis['groups']]
        analysis['max_time'] = max(group_times) if group_times else 0
        analysis['min_time'] = min(group_times) if group_times else 0
        analysis['avg_time'] = sum(group_times) / len(group_times) if group_times else 0
        analysis['load_balance_score'] = self.calculate_load_balance_score(current_groups)
        analysis['time_efficiency'] = analysis['min_time'] / analysis['max_time'] if analysis['max_time'] > 0 else 1.0
        
        return analysis
    
    def suggest_optimal_group_count(self, target_time_per_group: float = 300.0) -> int:
        """Suggest optimal number of groups based on target time per group."""
        if self.total_time == 0:
            return 1
        
        optimal_groups = max(1, math.ceil(self.total_time / target_time_per_group))
        
        # Test different group counts to find the best balance
        best_score = 0
        best_count = optimal_groups
        
        for count in range(max(1, optimal_groups - 2), optimal_groups + 3):
            groups = self.calculate_optimal_groups(count)
            score = self.calculate_load_balance_score(groups)
            
            if score > best_score:
                best_score = score
                best_count = count
        
        return best_count
    
    def generate_optimization_report(self, current_groups: List[List[str]], optimized_groups: List[List[str]]) -> str:
        """Generate a report comparing current and optimized distributions."""
        current_analysis = self.analyze_current_distribution(current_groups)
        optimized_analysis = self.analyze_current_distribution(optimized_groups)
        
        report = [
            "# Test Distribution Optimization Report",
            "",
            f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Total Tests:** {current_analysis['total_tests']}",
            f"**Total Execution Time:** {self.total_time:.1f}s",
            "",
            "## Current Distribution",
            "",
            f"- **Groups:** {current_analysis['group_count']}",
            f"- **Load Balance Score:** {current_analysis['load_balance_score']:.2f}/1.00",
            f"- **Time Efficiency:** {current_analysis['time_efficiency']:.2f} (min/max ratio)",
            f"- **Max Group Time:** {current_analysis['max_time']:.1f}s",
            f"- **Min Group Time:** {current_analysis['min_time']:.1f}s",
            "",
            "### Current Group Breakdown",
            "",
            "| Group | Tests | Time (s) | Time (min) |",
            "|-------|-------|----------|------------|"
        ]
        
        for group in current_analysis['groups']:
            report.append(
                f"| {group['group_id']} | {group['test_count']} | "
                f"{group['total_time']:.1f} | {group['total_time']/60:.1f} |"
            )
        
        report.extend([
            "",
            "## Optimized Distribution",
            "",
            f"- **Groups:** {optimized_analysis['group_count']}",
            f"- **Load Balance Score:** {optimized_analysis['load_balance_score']:.2f}/1.00",
            f"- **Time Efficiency:** {optimized_analysis['time_efficiency']:.2f} (min/max ratio)",
            f"- **Max Group Time:** {optimized_analysis['max_time']:.1f}s",
            f"- **Min Group Time:** {optimized_analysis['min_time']:.1f}s",
            "",
            "### Optimized Group Breakdown",
            "",
            "| Group | Tests | Time (s) | Time (min) |",
            "|-------|-------|----------|------------|"
        ])
        
        for group in optimized_analysis['groups']:
            report.append(
                f"| {group['group_id']} | {group['test_count']} | "
                f"{group['total_time']:.1f} | {group['total_time']/60:.1f} |"
            )
        
        # Calculate improvements
        time_improvement = current_analysis['max_time'] - optimized_analysis['max_time']
        balance_improvement = optimized_analysis['load_balance_score'] - current_analysis['load_balance_score']
        
        report.extend([
            "",
            "## Optimization Results",
            "",
            f"- **Time Reduction:** {time_improvement:.1f}s ({time_improvement/60:.1f} min)",
            f"- **Balance Improvement:** {balance_improvement:+.3f}",
            f"- **Efficiency Improvement:** {optimized_analysis['time_efficiency'] - current_analysis['time_efficiency']:+.3f}",
            "",
            "## Recommendations",
            ""
        ])
        
        if time_improvement > 30:  # More than 30 seconds improvement
            report.append("✅ **Recommended:** Apply optimization - significant time savings achieved")
        elif balance_improvement > 0.1:  # Significant balance improvement
            report.append("✅ **Recommended:** Apply optimization - better load balancing")
        elif time_improvement > 0:
            report.append("📈 **Consider:** Apply optimization - modest improvements")
        else:
            report.append("ℹ️ **Current distribution is already well-optimized**")
        
        return "\n".join(report)


class GitHubActionsMatrixGenerator:
    """Generates GitHub Actions matrix configurations for optimized test distribution."""
    
    def __init__(self, package_name: str):
        self.package_name = package_name
    
    def generate_matrix_config(self, optimized_groups: List[List[str]]) -> Dict:
        """Generate GitHub Actions matrix configuration."""
        matrix_entries = []
        
        for i, group in enumerate(optimized_groups, 1):
            if not group:  # Skip empty groups
                continue
            
            # Create space-separated test paths for GitHub Actions
            test_paths = " ".join(group)
            
            matrix_entry = {
                'package': self.package_name,
                'test_type': 'integration',
                'test_group': f'group-{i}',
                'test_path': test_paths
            }
            
            matrix_entries.append(matrix_entry)
        
        return {
            'include': matrix_entries
        }
    
    def generate_workflow_snippet(self, optimized_groups: List[List[str]]) -> str:
        """Generate a workflow snippet for the optimized test distribution."""
        matrix_config = self.generate_matrix_config(optimized_groups)
        
        snippet = f"""# Optimized test distribution for {self.package_name}
# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

strategy:
  matrix: {json.dumps(matrix_config, indent=4)}

steps:
  - name: Run tests (${{{{ matrix.test_group }}}})
    working-directory: ${{{{ matrix.package }}}}
    run: |
      python -m pytest ${{{{ matrix.test_path }}}} -n auto --timeout=600
"""
        return snippet
    
    def save_matrix_file(self, optimized_groups: List[List[str]], output_file: Path) -> None:
        """Save the matrix configuration to a file."""
        matrix_config = self.generate_matrix_config(optimized_groups)
        
        with open(output_file, 'w') as f:
            json.dump(matrix_config, f, indent=2)
        
        print(f"📄 Matrix configuration saved to {output_file}")


def parse_current_groups_from_workflow(workflow_file: Path, package_name: str) -> List[List[str]]:
    """Parse current test groups from a GitHub Actions workflow file."""
    if not workflow_file.exists():
        print(f"Workflow file {workflow_file} not found")
        return []
    
    try:
        with open(workflow_file, 'r') as f:
            content = f.read()
        
        # Simple parsing - look for test_path entries in the matrix
        import re
        
        # Find matrix entries for the specific package
        pattern = rf'package:\s*{re.escape(package_name)}.*?test_path:\s*([^\n]+)'
        matches = re.findall(pattern, content, re.DOTALL)
        
        current_groups = []
        for match in matches:
            # Clean up the test path
            test_path = match.strip().strip('"\'')
            tests = test_path.split()
            current_groups.append(tests)
        
        return current_groups
    
    except Exception as e:
        print(f"Error parsing workflow file: {e}")
        return []


def main():
    """Main CLI interface."""
    parser = argparse.ArgumentParser(description='Optimize test distribution for parallel execution')
    parser.add_argument('package', help='Package name to optimize')
    parser.add_argument('--analyze', action='store_true', help='Analyze current test distribution')
    parser.add_argument('--profile', action='store_true', help='Profile test execution times')
    parser.add_argument('--optimize', action='store_true', help='Generate optimized distribution')
    parser.add_argument('--groups', type=int, help='Target number of groups (auto-detected if not specified)')
    parser.add_argument('--generate-matrix', action='store_true', help='Generate GitHub Actions matrix')
    parser.add_argument('--workflow-file', help='Path to workflow file to analyze current groups')
    parser.add_argument('--output', help='Output file for results')
    parser.add_argument('--target-time', type=float, default=300.0, 
                        help='Target time per group in seconds (default: 300)')
    
    args = parser.parse_args()
    
    package_root = Path(args.package)
    if not package_root.exists():
        print(f"❌ Package directory {package_root} not found")
        sys.exit(1)
    
    # Initialize profiler
    profiler = TestExecutionProfiler(package_root)
    
    if args.profile:
        print(f"🔍 Profiling tests in {args.package}...")
        profiles = profiler.profile_all_tests()
        print(f"✅ Profiled {len(profiles)} test files")
        return
    
    # Load historical data
    profiles = profiler.load_historical_data()
    if not profiles:
        print("❌ No test profile data found. Run with --profile first.")
        sys.exit(1)
    
    # Initialize optimizer
    optimizer = TestDistributionOptimizer(profiles)
    
    if args.analyze:
        # Analyze current distribution
        workflow_file = Path(args.workflow_file) if args.workflow_file else Path('.github/workflows/main.yml')
        current_groups = parse_current_groups_from_workflow(workflow_file, args.package)
        
        if not current_groups:
            print("⚠️ No current groups found. Using all tests as one group.")
            current_groups = [list(profiles.keys())]
        
        analysis = optimizer.analyze_current_distribution(current_groups)
        
        print(f"\n📊 Current Test Distribution Analysis")
        print(f"Groups: {analysis['group_count']}")
        print(f"Load Balance Score: {analysis['load_balance_score']:.2f}/1.00")
        print(f"Time Efficiency: {analysis['time_efficiency']:.2f}")
        print(f"Max Group Time: {analysis['max_time']:.1f}s ({analysis['max_time']/60:.1f} min)")
        
        return
    
    if args.optimize:
        # Generate optimized distribution
        target_groups = args.groups or optimizer.suggest_optimal_group_count(args.target_time)
        
        print(f"🎯 Optimizing for {target_groups} groups...")
        optimized_groups = optimizer.calculate_optimal_groups(target_groups)
        
        # Get current groups for comparison
        workflow_file = Path(args.workflow_file) if args.workflow_file else Path('.github/workflows/main.yml')
        current_groups = parse_current_groups_from_workflow(workflow_file, args.package)
        
        if not current_groups:
            current_groups = [list(profiles.keys())]
        
        # Generate report
        report = optimizer.generate_optimization_report(current_groups, optimized_groups)
        
        if args.output:
            with open(args.output, 'w') as f:
                f.write(report)
            print(f"📄 Optimization report saved to {args.output}")
        else:
            print(report)
        
        return
    
    if args.generate_matrix:
        # Generate GitHub Actions matrix
        target_groups = args.groups or optimizer.suggest_optimal_group_count(args.target_time)
        optimized_groups = optimizer.calculate_optimal_groups(target_groups)
        
        generator = GitHubActionsMatrixGenerator(args.package)
        
        if args.output:
            output_file = Path(args.output)
            generator.save_matrix_file(optimized_groups, output_file)
        else:
            snippet = generator.generate_workflow_snippet(optimized_groups)
            print(snippet)
        
        return
    
    # Default: show optimization suggestions
    suggested_groups = optimizer.suggest_optimal_group_count(args.target_time)
    print(f"💡 Suggested optimal group count: {suggested_groups}")
    print(f"📊 Current total test time: {optimizer.total_time:.1f}s ({optimizer.total_time/60:.1f} min)")
    print(f"🎯 Target time per group: {args.target_time:.1f}s ({args.target_time/60:.1f} min)")
    
    print("\nNext steps:")
    print("1. Run with --profile to collect test timing data")
    print("2. Run with --optimize to see optimization recommendations")
    print("3. Run with --generate-matrix to create GitHub Actions configuration")


if __name__ == '__main__':
    main()