#!/usr/bin/env python3
"""
Parse complete CodeRabbit review data (with inline comments) and generate tracking document.

Usage:
    python scripts/parse-coderabbit-reviews-complete.py pr-review-data/pr-502-complete.json
"""

import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple


def categorize_issue(text: str) -> str:
    """Categorize issue by severity/type."""
    text_lower = text.lower()

    if any(word in text_lower for word in ['critical', 'bug', 'breaks', 'incorrect', 'wrong', 'security']):
        return 'critical'
    elif any(word in text_lower for word in ['deprecated', 'migrate', 'upgrade', 'pydantic']):
        return 'deprecation'
    elif any(word in text_lower for word in ['lgtm', 'good', 'correct', 'improved', 'fixed']):
        return 'resolved'
    elif any(word in text_lower for word in ['consider', 'optional', 'prefer', 'nitpick', 'minor']):
        return 'suggestion'
    else:
        return 'warning'


def extract_issue_title(text: str) -> str:
    """Extract the main issue description from a comment."""
    # Look for bolded text which usually contains the issue summary
    bold_match = re.search(r'\*\*([^*]+)\*\*', text)
    if bold_match:
        return bold_match.group(1).strip()

    # Otherwise take first sentence
    first_line = text.split('\n')[0].strip()
    if len(first_line) > 100:
        return first_line[:97] + '...'
    return first_line


def main():
    if len(sys.argv) < 2:
        print("Usage: python parse-coderabbit-reviews-complete.py <json-file>")
        sys.exit(1)

    input_file = Path(sys.argv[1])
    if not input_file.exists():
        print(f"Error: File not found: {input_file}")
        sys.exit(1)

    with open(input_file) as f:
        data = json.load(f)

    pr_info = data['pr_info']
    reviews = data['reviews']
    summary = data['summary']

    pr_number = pr_info['number']
    pr_title = pr_info['title']
    pr_state = pr_info['state']
    latest_commit = pr_info['commits'][-1]['oid'] if pr_info['commits'] else 'unknown'

    # Filter CodeRabbit reviews
    coderabbit_reviews = [r for r in reviews if r['author']['login'] == 'coderabbitai']

    if not coderabbit_reviews:
        print("No CodeRabbit reviews found")
        sys.exit(0)

    latest_review = max(coderabbit_reviews, key=lambda r: r['submittedAt'])

    print(f"# PR #{pr_number} Review Tracking")
    print(f"\n**Title:** {pr_title}")
    print(f"**State:** {pr_state}")
    print(f"**Latest Commit:** {latest_commit}")
    print(f"**Last CodeRabbit Review:** {latest_review['submittedAt']}")
    print(f"**Fetched:** {data.get('fetched_at', 'unknown')}")
    print("\n---\n")

    # Summary stats
    print(f"## Summary\n")
    print(f"- **Total Reviews:** {summary['total_reviews']}")
    print(f"- **CodeRabbit Reviews:** {summary['coderabbit_reviews']}")
    print(f"- **Inline Comments:** {summary['total_inline_comments']}")
    print(f"- **PR Comments:** {summary['total_pr_comments']}")

    print("\n### Reviews by Author")
    for author in summary['reviews_by_author']:
        print(f"- **{author['author']}**: {author['count']} reviews")

    # Extract all inline comments from CodeRabbit reviews
    print("\n---\n")
    print("## CodeRabbit Inline Comments\n")

    issues_by_file = defaultdict(list)
    issues_by_category = defaultdict(list)

    for review in coderabbit_reviews:
        review_date = review['submittedAt']
        commit = review['commit']['oid'][:8]

        for comment in review.get('comments', {}).get('nodes', []):
            file_path = comment.get('path', 'unknown')
            line = comment.get('line', comment.get('position', 'unknown'))
            body = comment.get('body', '')

            if not body.strip():
                continue

            issue_title = extract_issue_title(body)
            category = categorize_issue(body)

            issue_info = {
                'file': file_path,
                'line': line,
                'title': issue_title,
                'category': category,
                'body': body[:500] + '...' if len(body) > 500 else body,
                'date': review_date,
                'commit': commit
            }

            issues_by_file[file_path].append(issue_info)
            issues_by_category[category].append(issue_info)

    # Print by category
    priority_order = ['critical', 'deprecation', 'warning', 'suggestion', 'resolved']

    for category in priority_order:
        if category not in issues_by_category:
            continue

        issues = issues_by_category[category]
        icon = {
            'critical': '🚨',
            'deprecation': '⚠️',
            'warning': '⚠️',
            'suggestion': '💡',
            'resolved': '✅'
        }.get(category, '📝')

        print(f"### {icon} {category.title()} ({len(issues)})\n")

        for issue in issues[:10]:  # Show top 10 per category
            print(f"**{issue['file']}** (line {issue['line']})")
            print(f"- {issue['title']}")
            print(f"- *{issue['commit']} • {issue['date'][:10]}*")
            print()

    # Print by file
    print("\n---\n")
    print("## Issues by File\n")

    # Sort files by number of issues
    sorted_files = sorted(issues_by_file.items(), key=lambda x: len(x[1]), reverse=True)

    for file_path, issues in sorted_files[:15]:  # Top 15 files
        critical_count = len([i for i in issues if i['category'] == 'critical'])
        warning_count = len([i for i in issues if i['category'] in ['warning', 'deprecation']])
        suggestion_count = len([i for i in issues if i['category'] == 'suggestion'])

        badge = ""
        if critical_count > 0:
            badge = f" 🚨 {critical_count} critical"
        elif warning_count > 0:
            badge = f" ⚠️ {warning_count} warnings"

        print(f"### `{file_path}` ({len(issues)} issues{badge})\n")

        for issue in issues[:5]:  # Top 5 per file
            emoji = {
                'critical': '🚨',
                'deprecation': '⚠️',
                'warning': '⚠️',
                'suggestion': '💡',
                'resolved': '✅'
            }.get(issue['category'], '📝')

            print(f"{emoji} **Line {issue['line']}**: {issue['title']}")

        if len(issues) > 5:
            print(f"\n*... and {len(issues) - 5} more*")
        print()

    # Review history
    print("\n---\n")
    print("## Review History\n")

    for review in sorted(coderabbit_reviews, key=lambda r: r['submittedAt'], reverse=True):
        timestamp = review['submittedAt']
        commit_oid = review['commit']['oid'][:8]
        state = review['state']
        inline_count = len(review.get('comments', {}).get('nodes', []))
        body_length = len(review.get('body', ''))

        # Extract actionable count from body
        actionable_match = re.search(r'Actionable comments posted: (\d+)', review.get('body', ''))
        actionable = actionable_match.group(1) if actionable_match else '?'

        print(f"- **{timestamp}** (commit `{commit_oid}`, state: {state})")
        print(f"  - Actionable: {actionable}, Inline: {inline_count}, Body: {body_length:,} chars")

    print("\n---\n")
    print(f"\n*Generated from: {input_file}*")
    print(f"*Run `gh pr view {pr_number}` to see the full PR*")


if __name__ == '__main__':
    main()

