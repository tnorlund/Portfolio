#!/usr/bin/env python3
"""
Parse CodeRabbit reviews from GitHub CLI JSON output and generate a tracking document.

Usage:
    python scripts/parse-coderabbit-reviews.py pr-review-data/pr-502-comments.json
"""

import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple


def parse_issue_from_body(body: str) -> List[Dict]:
    """Extract issues from CodeRabbit review body."""
    issues = []

    # Look for "Actionable comments posted"
    match = re.search(r"\*\*Actionable comments posted: (\d+)\*\*", body)
    actionable_count = int(match.group(1)) if match else 0

    # Look for duplicate comments count
    match = re.search(r"♻️ Duplicate comments \((\d+)\)", body)
    duplicate_count = int(match.group(1)) if match else 0

    # Extract file paths and issue titles from markdown
    file_pattern = r"<summary>([^<]+\.(?:py|ts|tsx|js|jsx|md))(?: \((\d+)\))?</summary>"
    files = re.findall(file_pattern, body)

    # Extract critical issues
    critical_pattern = r"\*\*(?:Critical|CRITICAL|Bug|BUG):\s*([^*]+)\*\*"
    critical_issues = re.findall(critical_pattern, body)

    return {
        "actionable_count": actionable_count,
        "duplicate_count": duplicate_count,
        "files": [{"file": f[0], "count": int(f[1]) if f[1] else 1} for f in files],
        "critical_issues": critical_issues,
    }


def extract_code_locations(body: str) -> List[Tuple[str, str, str]]:
    """Extract file paths, line numbers, and issue descriptions."""
    locations = []

    # Pattern: `path/to/file.py (lines)`: **Issue description**
    pattern = (
        r"`([^`]+\.(?:py|ts|tsx|js|jsx))`.*?(?:\((\d+(?:-\d+)?)\))?.*?\*\*([^*]+)\*\*"
    )

    for match in re.finditer(pattern, body):
        file_path = match.group(1)
        lines = match.group(2) or "unknown"
        description = match.group(3).strip()
        locations.append((file_path, lines, description))

    return locations


def categorize_issue(description: str) -> str:
    """Categorize issue by severity/type."""
    desc_lower = description.lower()

    if any(
        word in desc_lower
        for word in ["critical", "bug", "breaks", "incorrect", "wrong"]
    ):
        return "critical"
    elif any(word in desc_lower for word in ["deprecated", "migrate", "upgrade"]):
        return "deprecation"
    elif any(word in desc_lower for word in ["lgtm", "good", "correct", "improved"]):
        return "resolved"
    elif any(
        word in desc_lower for word in ["consider", "optional", "prefer", "nitpick"]
    ):
        return "suggestion"
    else:
        return "warning"


def main():
    if len(sys.argv) < 2:
        print("Usage: python parse-coderabbit-reviews.py <json-file>")
        sys.exit(1)

    input_file = Path(sys.argv[1])
    if not input_file.exists():
        print(f"Error: File not found: {input_file}")
        sys.exit(1)

    with open(input_file) as f:
        data = json.load(f)

    pr_number = data["number"]
    pr_title = data["title"]
    pr_state = data["state"]
    latest_commit = data["commits"][-1]["oid"] if data["commits"] else "unknown"

    # Filter CodeRabbit reviews
    reviews = [r for r in data["reviews"] if r["author"]["login"] == "coderabbitai"]

    # Get latest review
    if not reviews:
        print("No CodeRabbit reviews found")
        sys.exit(0)

    latest_review = max(reviews, key=lambda r: r["submittedAt"])

    print(f"# PR #{pr_number} Review Tracking")
    print(f"\n**Title:** {pr_title}")
    print(f"**State:** {pr_state}")
    print(f"**Latest Commit:** {latest_commit}")
    print(f"**Last CodeRabbit Review:** {latest_review['submittedAt']}")
    print(f"**Total CodeRabbit Reviews:** {len(reviews)}")
    print("\n---\n")

    # Parse latest review
    body = latest_review.get("body", "")
    parsed = parse_issue_from_body(body)

    print(f"## Latest Review Summary\n")
    print(f"- **Actionable Comments:** {parsed['actionable_count']}")
    print(f"- **Duplicate Comments:** {parsed['duplicate_count']}")
    print(f"- **Files Mentioned:** {len(parsed['files'])}")
    print(f"- **Critical Issues Found:** {len(parsed['critical_issues'])}")

    if parsed["critical_issues"]:
        print("\n### Critical Issues")
        for i, issue in enumerate(parsed["critical_issues"], 1):
            print(f"{i}. {issue}")

    if parsed["files"]:
        print("\n### Files with Comments")
        for file_info in parsed["files"][:10]:  # Show top 10
            print(f"- `{file_info['file']}` ({file_info['count']} comments)")

    # Extract code locations from body
    locations = extract_code_locations(body)

    if locations:
        print("\n## Issues by Category\n")

        by_category = defaultdict(list)
        for file_path, lines, description in locations:
            category = categorize_issue(description)
            by_category[category].append((file_path, lines, description))

        # Sort by priority
        priority = ["critical", "deprecation", "warning", "suggestion", "resolved"]

        for category in priority:
            if category in by_category:
                print(f"### {category.title()}\n")
                for file_path, lines, description in by_category[category][:5]:
                    print(f"- **{file_path}** (lines {lines}): {description}")
                print()

    print("\n## Review History\n")
    for review in sorted(reviews, key=lambda r: r["submittedAt"], reverse=True)[:5]:
        timestamp = review["submittedAt"]
        commit_oid = review["commit"]["oid"][:8]
        parsed = parse_issue_from_body(review.get("body", ""))
        print(
            f"- **{timestamp}** (commit `{commit_oid}`): "
            f"{parsed['actionable_count']} actionable, "
            f"{parsed['duplicate_count']} duplicate"
        )

    print("\n---\n")
    print(f"\n*Generated from: {input_file}*")
    print(f"*Run `gh pr view {pr_number}` to see the full PR*")


if __name__ == "__main__":
    main()
