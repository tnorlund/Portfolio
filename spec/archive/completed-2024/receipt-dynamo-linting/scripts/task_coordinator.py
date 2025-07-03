#!/usr/bin/env python3
"""
Task Coordinator for Receipt Dynamo Linting

This script coordinates the parallel execution of linting tasks
and provides progress tracking and reporting.
"""

import concurrent.futures
import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class TaskResult:
    """Result of a linting task"""

    task_id: str
    status: str  # 'success', 'failed', 'error'
    duration: float
    files_processed: int
    issues_fixed: int
    issues_remaining: int
    output: str
    error: str = ""


class TaskCoordinator:
    """Coordinates parallel linting tasks"""

    def __init__(self, working_dir: Path):
        self.working_dir = working_dir
        self.results: List[TaskResult] = []
        self.start_time = time.time()

    def run_task(self, task_config: Dict[str, Any]) -> TaskResult:
        """Run a single linting task"""
        task_id = task_config["id"]
        commands = task_config["commands"]
        target_dir = task_config["target_dir"]

        start_time = time.time()
        output_lines = []
        error_lines = []
        files_processed = 0
        issues_fixed = 0
        issues_remaining = 0

        try:
            for cmd in commands:
                print(f"[{task_id}] Running: {' '.join(cmd)}")

                result = subprocess.run(
                    cmd,
                    cwd=self.working_dir,
                    capture_output=True,
                    text=True,
                    timeout=300,  # 5 minute timeout per command
                )

                output_lines.append(f"Command: {' '.join(cmd)}")
                output_lines.append(f"Return code: {result.returncode}")
                output_lines.append(f"STDOUT: {result.stdout}")

                if result.stderr:
                    error_lines.append(f"STDERR: {result.stderr}")

                # Count files processed
                if target_dir:
                    files_processed = len(list(Path(target_dir).glob("*.py")))

                # Extract metrics from tool output
                if "black" in cmd:
                    issues_fixed += self._parse_black_output(result.stdout)
                elif "pylint" in cmd:
                    issues_remaining += self._parse_pylint_output(result.stdout)
                elif "mypy" in cmd:
                    issues_remaining += self._parse_mypy_output(result.stdout)

            duration = time.time() - start_time
            status = "success"

        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            status = "failed"
            error_lines.append(f"Task {task_id} timed out after 5 minutes")

        except Exception as e:
            duration = time.time() - start_time
            status = "error"
            error_lines.append(f"Task {task_id} failed: {str(e)}")

        return TaskResult(
            task_id=task_id,
            status=status,
            duration=duration,
            files_processed=files_processed,
            issues_fixed=issues_fixed,
            issues_remaining=issues_remaining,
            output="\n".join(output_lines),
            error="\n".join(error_lines),
        )

    def run_parallel_tasks(
        self, task_configs: List[Dict[str, Any]], max_workers: int = 4
    ):
        """Run multiple tasks in parallel"""
        print(f"Starting {len(task_configs)} tasks with {max_workers} workers")

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            futures = {
                executor.submit(self.run_task, config): config["id"]
                for config in task_configs
            }

            # Collect results as they complete
            for future in concurrent.futures.as_completed(futures):
                task_id = futures[future]
                try:
                    result = future.result()
                    self.results.append(result)
                    print(
                        f"[{task_id}] Completed: {result.status} in {result.duration:.1f}s"
                    )
                except Exception as e:
                    print(f"[{task_id}] Exception: {e}")

    def generate_report(self) -> Dict[str, Any]:
        """Generate a comprehensive report"""
        total_duration = time.time() - self.start_time

        success_count = len([r for r in self.results if r.status == "success"])
        failed_count = len([r for r in self.results if r.status == "failed"])
        error_count = len([r for r in self.results if r.status == "error"])

        total_files = sum(r.files_processed for r in self.results)
        total_issues_fixed = sum(r.issues_fixed for r in self.results)
        total_issues_remaining = sum(r.issues_remaining for r in self.results)

        return {
            "summary": {
                "total_duration": total_duration,
                "tasks_completed": len(self.results),
                "success_count": success_count,
                "failed_count": failed_count,
                "error_count": error_count,
                "files_processed": total_files,
                "issues_fixed": total_issues_fixed,
                "issues_remaining": total_issues_remaining,
            },
            "tasks": [asdict(result) for result in self.results],
            "recommendations": self._generate_recommendations(),
        }

    def _parse_black_output(self, output: str) -> int:
        """Parse black output to count reformatted files"""
        if "would reformat" in output:
            lines = output.split("\n")
            for line in lines:
                if "file would be reformatted" in line:
                    return 1
                elif "files would be reformatted" in line:
                    return int(line.split()[0])
        return 0

    def _parse_pylint_output(self, output: str) -> int:
        """Parse pylint output to count remaining issues"""
        issue_count = 0
        for line in output.split("\n"):
            if line.strip() and (line.startswith("E") or line.startswith("F")):
                issue_count += 1
        return issue_count

    def _parse_mypy_output(self, output: str) -> int:
        """Parse mypy output to count type errors"""
        if "Found" in output and "error" in output:
            for line in output.split("\n"):
                if line.startswith("Found") and "error" in line:
                    return int(line.split()[1])
        return 0

    def _generate_recommendations(self) -> List[str]:
        """Generate recommendations based on results"""
        recommendations = []

        failed_tasks = [r for r in self.results if r.status != "success"]
        if failed_tasks:
            recommendations.append(
                f"Retry {len(failed_tasks)} failed tasks individually"
            )

        total_remaining = sum(r.issues_remaining for r in self.results)
        if total_remaining > 0:
            recommendations.append(
                f"Address {total_remaining} remaining issues manually"
            )

        if any(r.duration > 180 for r in self.results):  # > 3 minutes
            recommendations.append("Consider breaking down slow tasks further")

        return recommendations


# Task configurations for each phase
PHASE1_TASKS = [
    {
        "id": "lint-analysis",
        "commands": [
            ["python", "-m", "black", "--check", "--diff", "receipt_dynamo"],
            ["python", "-m", "pylint", "receipt_dynamo", "--errors-only"],
            ["python", "-m", "mypy", "receipt_dynamo", "--no-error-summary"],
        ],
        "target_dir": "receipt_dynamo",
    }
]

PHASE2_TASKS = [
    {
        "id": "format-entities",
        "commands": [
            ["python", "-m", "black", "receipt_dynamo/entities/"],
            ["python", "-m", "isort", "receipt_dynamo/entities/"],
            ["python", "-m", "black", "--check", "receipt_dynamo/entities/"],
        ],
        "target_dir": "receipt_dynamo/entities",
    },
    {
        "id": "format-services",
        "commands": [
            ["python", "-m", "black", "receipt_dynamo/services/"],
            ["python", "-m", "isort", "receipt_dynamo/services/"],
            ["python", "-m", "black", "--check", "receipt_dynamo/services/"],
        ],
        "target_dir": "receipt_dynamo/services",
    },
    {
        "id": "format-tests-unit",
        "commands": [
            ["python", "-m", "black", "tests/unit/"],
            ["python", "-m", "isort", "tests/unit/"],
            ["python", "-m", "black", "--check", "tests/unit/"],
        ],
        "target_dir": "tests/unit",
    },
    {
        "id": "format-tests-integration",
        "commands": [
            ["python", "-m", "black", "tests/integration/"],
            ["python", "-m", "isort", "tests/integration/"],
            ["python", "-m", "black", "--check", "tests/integration/"],
        ],
        "target_dir": "tests/integration",
    },
]


def main():
    """Main execution function"""
    import argparse

    parser = argparse.ArgumentParser(description="Coordinate linting tasks")
    parser.add_argument("phase", choices=["1", "2", "3", "4"], help="Phase to execute")
    parser.add_argument(
        "--working-dir", default="receipt_dynamo", help="Working directory"
    )
    parser.add_argument(
        "--max-workers", type=int, default=4, help="Max parallel workers"
    )
    parser.add_argument(
        "--output", default="linting-report.json", help="Output report file"
    )

    args = parser.parse_args()

    coordinator = TaskCoordinator(Path(args.working_dir))

    if args.phase == "1":
        tasks = PHASE1_TASKS
    elif args.phase == "2":
        tasks = PHASE2_TASKS
    else:
        print(f"Phase {args.phase} tasks not yet implemented")
        return

    # Execute tasks
    coordinator.run_parallel_tasks(tasks, args.max_workers)

    # Generate and save report
    report = coordinator.generate_report()
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2)

    # Print summary
    print(f"\n=== Linting Phase {args.phase} Complete ===")
    print(f"Duration: {report['summary']['total_duration']:.1f}s")
    print(
        f"Tasks: {report['summary']['success_count']}/{report['summary']['tasks_completed']} successful"
    )
    print(f"Files processed: {report['summary']['files_processed']}")
    print(f"Issues fixed: {report['summary']['issues_fixed']}")
    print(f"Issues remaining: {report['summary']['issues_remaining']}")

    if report["recommendations"]:
        print("\nRecommendations:")
        for rec in report["recommendations"]:
            print(f"- {rec}")


if __name__ == "__main__":
    main()
