#!/usr/bin/env python3
"""
Claude Code review integration script.
Analyzes Cursor bot findings and provides additional architectural review.
"""

import subprocess
import sys
from pathlib import Path


def get_pr_diff(pr_number: str) -> str:
    """Get PR diff for Claude analysis."""
    try:
        result = subprocess.run([
            'gh', 'pr', 'diff', pr_number
        ], capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError:
        return ""


def get_cursor_comments(pr_number: str) -> str:
    """Extract Cursor bot comments from PR."""
    try:
        result = subprocess.run([
            'gh', 'pr', 'view', pr_number, '--json', 'comments'
        ], capture_output=True, text=True, check=True)
        
        # Filter for Cursor bot comments
        # This would need JSON parsing in real implementation
        return result.stdout
    except subprocess.CalledProcessError:
        return ""


def run_claude_review(pr_number: str):
    """Run Claude Code review focusing on Cursor findings + architecture."""
    
    print(f"🔍 Running Claude review for PR #{pr_number}")
    
    # Get context
    pr_diff = get_pr_diff(pr_number)
    cursor_comments = get_cursor_comments(pr_number)
    
    # Claude review prompt
    review_prompt = f"""
    Analyze this PR with focus on:
    
    1. **Cursor Bot Findings**: Review and validate Cursor's identified issues
    2. **Architecture Impact**: How do changes affect overall system design?
    3. **Performance Implications**: Any optimization opportunities?
    4. **Test Strategy**: Are test changes following best practices?
    5. **Documentation**: Is adequate documentation provided?
    
    Cursor Bot Comments:
    {cursor_comments}
    
    PR Diff:
    {pr_diff}
    
    Provide actionable recommendations that complement Cursor's findings.
    """
    
    # In practice, you'd integrate with Claude API here
    print("Claude review prompt prepared. Integration with Claude API needed.")
    
    return review_prompt


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python claude_review.py <pr_number>")
        sys.exit(1)
    
    pr_number = sys.argv[1]
    run_claude_review(pr_number)