#!/usr/bin/env python3
"""
Claude Cost Optimizer - Smart model selection based on PR characteristics.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import click
from github import Github


class CostOptimizer:
    """Smart model selection and cost tracking for Claude reviews."""

    MODELS = {
        "haiku": {
            "name": "claude-3-5-haiku-20241022",
            "input_cost": 0.25,  # per 1M tokens
            "output_cost": 1.25,  # per 1M tokens
            "speed": "fastest",
            "use_case": "Quick reviews, syntax checks",
        },
        "sonnet": {
            "name": "claude-3-5-sonnet-20241022",
            "input_cost": 3.00,
            "output_cost": 15.00,
            "speed": "fast",
            "use_case": "Balanced reviews",
        },
        "opus": {
            "name": "claude-3-opus-20240229",
            "input_cost": 15.00,
            "output_cost": 75.00,
            "speed": "thorough",
            "use_case": "Deep analysis",
        },
    }

    def __init__(self, config_path: Optional[str] = None):
        self.config = self.load_config(config_path)
        self.cost_log_path = Path(".github/claude_costs.json")

    def load_config(self, config_path: Optional[str]) -> Dict:
        """Load cost optimization configuration."""
        default_config = {
            "monthly_budget": 25.0,  # USD
            "daily_limit": 5.0,  # USD
            "default_model": "haiku",
            "model_selection": {
                "lines_threshold_sonnet": 200,
                "lines_threshold_opus": 1000,
                "security_files_model": "opus",
                "test_files_model": "haiku",
            },
            "file_patterns": {
                "security": [
                    "**/auth*.py",
                    "**/security*.py",
                    "**/crypto*.py",
                    "**/login*.py",
                    "**/password*.py",
                    "**/token*.py",
                ],
                "architecture": [
                    "**/models/*.py",
                    "**/core/*.py",
                    "**/base*.py",
                    "**/abstract*.py",
                    "**/interface*.py",
                ],
                "tests": ["**/test_*.py", "**/*_test.py", "**/tests/*.py"],
            },
        }

        if config_path and Path(config_path).exists():
            with open(config_path) as f:
                user_config = json.load(f)
                default_config.update(user_config)

        return default_config

    def analyze_pr_characteristics(self, pr_data: Dict) -> Dict:
        """Analyze PR to determine appropriate model."""
        characteristics = {
            "lines_changed": pr_data["additions"] + pr_data["deletions"],
            "files_changed": len(pr_data["changed_files"]),
            "is_security_related": False,
            "is_architecture_change": False,
            "is_test_only": False,
            "complexity_score": 0,
        }

        # Check file patterns
        changed_files = pr_data["changed_files"]

        # Security files
        security_patterns = self.config["file_patterns"]["security"]
        characteristics["is_security_related"] = any(
            self._matches_pattern(f, security_patterns) for f in changed_files
        )

        # Architecture files
        arch_patterns = self.config["file_patterns"]["architecture"]
        characteristics["is_architecture_change"] = any(
            self._matches_pattern(f, arch_patterns) for f in changed_files
        )

        # Test files
        test_patterns = self.config["file_patterns"]["tests"]
        test_files = [
            f for f in changed_files if self._matches_pattern(f, test_patterns)
        ]
        characteristics["is_test_only"] = len(test_files) == len(changed_files)

        # Complexity scoring
        characteristics["complexity_score"] = self._calculate_complexity(
            pr_data
        )

        return characteristics

    def _matches_pattern(self, filename: str, patterns: List[str]) -> bool:
        """Check if filename matches any pattern."""
        import fnmatch

        return any(fnmatch.fnmatch(filename, pattern) for pattern in patterns)

    def _calculate_complexity(self, pr_data: Dict) -> int:
        """Calculate PR complexity score (0-100)."""
        score = 0

        # Lines changed (up to 40 points)
        lines = pr_data["additions"] + pr_data["deletions"]
        score += min(40, lines // 10)

        # Files changed (up to 30 points)
        score += min(30, len(pr_data["changed_files"]) * 3)

        # PR description quality (up to 20 points)
        description = pr_data.get("body", "")
        if len(description) > 100:
            score += 10
        if any(
            word in description.lower()
            for word in ["breaking", "major", "refactor"]
        ):
            score += 10

        # File type diversity (up to 10 points)
        extensions = set(Path(f).suffix for f in pr_data["changed_files"])
        score += min(10, len(extensions) * 2)

        return min(100, score)

    def select_model(self, pr_data: Dict) -> Tuple[str, str]:
        """Select optimal model based on PR characteristics."""
        chars = self.analyze_pr_characteristics(pr_data)
        config = self.config["model_selection"]

        # Security files always get best model
        if chars["is_security_related"]:
            model = config.get("security_files_model", "opus")
            reason = "Security-related files detected"
            return model, reason

        # Test-only changes get lightest model
        if chars["is_test_only"]:
            model = config.get("test_files_model", "haiku")
            reason = "Test files only"
            return model, reason

        # Architecture changes get thorough review
        if chars["is_architecture_change"]:
            model = "opus"
            reason = "Architecture changes detected"
            return model, reason

        # Size-based selection
        lines = chars["lines_changed"]
        if lines >= config.get("lines_threshold_opus", 1000):
            model = "opus"
            reason = f"Large PR ({lines} lines)"
        elif lines >= config.get("lines_threshold_sonnet", 200):
            model = "sonnet"
            reason = f"Medium PR ({lines} lines)"
        else:
            model = "haiku"
            reason = f"Small PR ({lines} lines)"

        # Complexity override
        if chars["complexity_score"] > 80 and model == "haiku":
            model = "sonnet"
            reason = f"High complexity score ({chars['complexity_score']})"

        return model, reason

    def estimate_cost(
        self, model: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Estimate cost for a review."""
        model_info = self.MODELS[model]

        input_cost = (input_tokens / 1_000_000) * model_info["input_cost"]
        output_cost = (output_tokens / 1_000_000) * model_info["output_cost"]

        return input_cost + output_cost

    def check_budget_limits(self) -> Dict:
        """Check current usage against budget limits."""
        usage = self.get_current_usage()
        config = self.config

        return {
            "monthly_used": usage["monthly_cost"],
            "monthly_limit": config["monthly_budget"],
            "monthly_remaining": config["monthly_budget"]
            - usage["monthly_cost"],
            "daily_used": usage["daily_cost"],
            "daily_limit": config["daily_limit"],
            "daily_remaining": config["daily_limit"] - usage["daily_cost"],
            "can_proceed": (
                usage["monthly_cost"] < config["monthly_budget"]
                and usage["daily_cost"] < config["daily_limit"]
            ),
        }

    def get_current_usage(self) -> Dict:
        """Get current cost usage from log."""
        if not self.cost_log_path.exists():
            return {"monthly_cost": 0.0, "daily_cost": 0.0}

        with open(self.cost_log_path) as f:
            logs = [json.loads(line) for line in f if line.strip()]

        now = datetime.now()
        monthly_cost = sum(
            log["cost"]
            for log in logs
            if datetime.fromisoformat(log["timestamp"]).month == now.month
        )
        daily_cost = sum(
            log["cost"]
            for log in logs
            if datetime.fromisoformat(log["timestamp"]).date() == now.date()
        )

        return {"monthly_cost": monthly_cost, "daily_cost": daily_cost}

    def log_usage(
        self,
        model: str,
        pr_number: int,
        cost: float,
        input_tokens: int,
        output_tokens: int,
        reason: str,
    ):
        """Log usage for cost tracking."""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "pr_number": pr_number,
            "cost": cost,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reason": reason,
        }

        # Ensure directory exists
        self.cost_log_path.parent.mkdir(exist_ok=True)

        # Append to log file
        with open(self.cost_log_path, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

    def generate_cost_report(self) -> str:
        """Generate cost usage report."""
        usage = self.get_current_usage()
        budget = self.check_budget_limits()

        if not self.cost_log_path.exists():
            return "No usage data available yet."

        with open(self.cost_log_path) as f:
            logs = [json.loads(line) for line in f if line.strip()]

        # Model usage breakdown
        model_usage = {}
        for log in logs:
            model = log["model"]
            if model not in model_usage:
                model_usage[model] = {"count": 0, "cost": 0.0}
            model_usage[model]["count"] += 1
            model_usage[model]["cost"] += log["cost"]

        report = f"""# 💰 Claude Review Cost Report
        
## Current Usage
- **Monthly**: ${usage['monthly_cost']:.2f} / ${budget['monthly_limit']:.2f} ({usage['monthly_cost']/budget['monthly_limit']*100:.1f}%)
- **Daily**: ${usage['daily_cost']:.2f} / ${budget['daily_limit']:.2f} ({usage['daily_cost']/budget['daily_limit']*100:.1f}%)
- **Remaining Budget**: ${budget['monthly_remaining']:.2f}

## Model Usage Breakdown
"""

        for model, stats in model_usage.items():
            report += f"- **{model.title()}**: {stats['count']} reviews, ${stats['cost']:.2f}\n"

        # Recent reviews
        recent_logs = sorted(logs, key=lambda x: x["timestamp"], reverse=True)[
            :5
        ]
        report += f"\n## Recent Reviews\n"
        for log in recent_logs:
            report += f"- PR #{log['pr_number']}: {log['model']} (${log['cost']:.3f}) - {log['reason']}\n"

        return report


@click.command()
@click.option("--pr-number", type=int, help="PR number to analyze")
@click.option("--repository", help="Repository in format owner/repo")
@click.option("--config", help="Path to cost optimization config")
@click.option("--report", is_flag=True, help="Generate cost report")
@click.option("--check-budget", is_flag=True, help="Check budget status")
def main(
    pr_number: Optional[int],
    repository: Optional[str],
    config: Optional[str],
    report: bool,
    check_budget: bool,
):
    """Claude cost optimization tool."""

    optimizer = CostOptimizer(config)

    if report:
        print(optimizer.generate_cost_report())
        return

    if check_budget:
        budget = optimizer.check_budget_limits()
        status = (
            "✅ WITHIN BUDGET"
            if budget["can_proceed"]
            else "❌ BUDGET EXCEEDED"
        )
        print(
            f"""
Budget Status: {status}

Monthly: ${budget['monthly_used']:.2f} / ${budget['monthly_limit']:.2f}
Daily: ${budget['daily_used']:.2f} / ${budget['daily_limit']:.2f}
Remaining: ${budget['monthly_remaining']:.2f}
"""
        )
        return

    if not pr_number or not repository:
        print("❌ --pr-number and --repository required for PR analysis")
        return

    # Analyze PR for model selection
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        print("❌ GITHUB_TOKEN environment variable required")
        return

    github = Github(github_token)
    repo = github.get_repo(repository)
    pr = repo.get_pull_request(pr_number)

    pr_data = {
        "additions": pr.additions,
        "deletions": pr.deletions,
        "changed_files": [f.filename for f in pr.get_files()],
        "body": pr.body or "",
    }

    # Get model recommendation
    model, reason = optimizer.select_model(pr_data)
    budget = optimizer.check_budget_limits()

    # Estimate cost (rough approximation)
    lines = pr_data["additions"] + pr_data["deletions"]
    estimated_input_tokens = min(100000, lines * 50)  # Rough estimate
    estimated_output_tokens = min(10000, lines * 5)  # Rough estimate
    estimated_cost = optimizer.estimate_cost(
        model, estimated_input_tokens, estimated_output_tokens
    )

    print(
        f"""
🤖 Claude Model Recommendation for PR #{pr_number}

Recommended Model: {model.upper()}
Reason: {reason}
Estimated Cost: ${estimated_cost:.3f}

Budget Status: {"✅ OK" if budget['can_proceed'] else "❌ EXCEEDED"}
Monthly Remaining: ${budget['monthly_remaining']:.2f}

Model Details:
- {optimizer.MODELS[model]['name']}
- {optimizer.MODELS[model]['use_case']}
- Speed: {optimizer.MODELS[model]['speed']}
"""
    )


if __name__ == "__main__":
    main()
