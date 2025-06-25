#!/usr/bin/env python3
"""
Claude Code Review Analyzer
Analyzes PRs for architectural, performance, and quality issues complementing Cursor bot.
"""

import click
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from github import Github


class PRAnalyzer:
    def __init__(self, github_token: str, repository: str):
        self.github = Github(github_token)
        self.repo = self.github.get_repo(repository)
        self.token = github_token
        
    def get_pr_data(self, pr_number: int) -> Dict:
        """Get comprehensive PR data for analysis."""
        pr = self.repo.get_pull_request(pr_number)
        
        # Get PR diff
        diff = self._get_pr_diff(pr_number)
        
        # Get changed files
        files = list(pr.get_files())
        
        # Get comments (including bot comments)
        comments = list(pr.get_issue_comments())
        review_comments = list(pr.get_review_comments())
        
        return {
            'pr': pr,
            'diff': diff,
            'files': files,
            'comments': comments,
            'review_comments': review_comments,
            'title': pr.title,
            'body': pr.body or '',
            'changed_files': [f.filename for f in files],
            'additions': sum(f.additions for f in files),
            'deletions': sum(f.deletions for f in files)
        }
    
    def _get_pr_diff(self, pr_number: int) -> str:
        """Get PR diff using gh CLI or API."""
        try:
            result = subprocess.run([
                'gh', 'pr', 'diff', str(pr_number), '--repo', self.repo.full_name
            ], capture_output=True, text=True, check=True)
            return result.stdout
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback to API
            pr = self.repo.get_pull_request(pr_number)
            return requests.get(
                pr.diff_url,
                headers={'Authorization': f'token {self.token}'}
            ).text
    
    def extract_cursor_findings(self, pr_data: Dict) -> List[Dict]:
        """Extract Cursor bot findings from PR comments."""
        cursor_findings = []
        
        # Look for Cursor bot comments
        all_comments = pr_data['comments'] + pr_data['review_comments']
        
        for comment in all_comments:
            # Check if comment is from Cursor bot (adjust pattern as needed)
            if (hasattr(comment, 'user') and comment.user and 
                ('cursor' in comment.user.login.lower() or 
                 'bot' in comment.user.login.lower())):
                
                cursor_findings.append({
                    'author': comment.user.login,
                    'body': comment.body,
                    'created_at': comment.created_at.isoformat(),
                    'type': 'cursor_bot'
                })
        
        return cursor_findings
    
    def analyze_architecture_impact(self, pr_data: Dict) -> Dict:
        """Analyze architectural implications of the changes."""
        analysis = {
            'concerns': [],
            'improvements': [],
            'recommendations': []
        }
        
        changed_files = pr_data['changed_files']
        
        # Check for architectural concerns
        if any('conftest.py' in f for f in changed_files):
            analysis['concerns'].append("Global test configuration changes - ensure backward compatibility")
        
        if any('.github/workflows' in f for f in changed_files):
            analysis['concerns'].append("CI/CD workflow changes - test thoroughly before merge")
        
        if any('scripts/' in f for f in changed_files):
            analysis['improvements'].append("Build/deployment script improvements detected")
        
        # Check for test organization patterns
        test_files = [f for f in changed_files if 'test_' in f or f.endswith('_test.py')]
        if test_files:
            if any('/unit/' in f for f in test_files) and any('/integration/' in f for f in test_files):
                analysis['improvements'].append("Following directory-based test organization pattern")
            elif any('@pytest.mark' in pr_data['diff']):
                analysis['recommendations'].append("Consider migrating to directory-based test organization for better CI performance")
        
        return analysis
    
    def analyze_performance_impact(self, pr_data: Dict) -> Dict:
        """Analyze performance implications."""
        analysis = {
            'optimizations': [],
            'concerns': [],
            'metrics': {}
        }
        
        diff = pr_data['diff']
        
        # Look for performance patterns
        if 'pytest-xdist' in diff or '-n auto' in diff:
            analysis['optimizations'].append("Parallel test execution enabled")
        
        if 'cache' in diff.lower():
            analysis['optimizations'].append("Caching improvements detected")
        
        if 'timeout' in diff:
            analysis['optimizations'].append("Test timeout optimizations")
        
        # Check for potential performance issues
        if pr_data['additions'] > 500:
            analysis['concerns'].append(f"Large change set ({pr_data['additions']} additions) - consider splitting PR")
        
        return analysis
    
    def analyze_test_strategy(self, pr_data: Dict) -> Dict:
        """Analyze test coverage and strategy."""
        analysis = {
            'coverage_impact': 'unknown',
            'test_types': [],
            'recommendations': []
        }
        
        test_files = [f for f in pr_data['changed_files'] if 'test_' in f]
        
        if test_files:
            analysis['test_types'] = []
            for test_file in test_files:
                if '/unit/' in test_file:
                    analysis['test_types'].append('unit')
                elif '/integration/' in test_file:
                    analysis['test_types'].append('integration')
                elif '/end_to_end/' in test_file:
                    analysis['test_types'].append('end_to_end')
        
        # Check for test configuration changes
        if any('pytest' in f for f in pr_data['changed_files']):
            analysis['recommendations'].append("Test configuration changed - verify all test types still work")
        
        return analysis
    
    def generate_review_summary(self, pr_data: Dict, cursor_findings: List[Dict], 
                              architecture: Dict, performance: Dict, testing: Dict) -> str:
        """Generate comprehensive review summary."""
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        
        summary = f"""# 🤖 Claude Code Review Summary

**PR #{pr_data['pr'].number}**: {pr_data['title']}
**Analyzed**: {timestamp}
**Changes**: +{pr_data['additions']} -{pr_data['deletions']} lines across {len(pr_data['changed_files'])} files

## 🔍 Cursor Bot Findings Review

"""
        
        if cursor_findings:
            summary += f"Found {len(cursor_findings)} Cursor bot comments to validate:\n\n"
            for i, finding in enumerate(cursor_findings, 1):
                summary += f"### {i}. {finding['author']} Comment\n"
                summary += f"```\n{finding['body'][:500]}{'...' if len(finding['body']) > 500 else ''}\n```\n\n"
        else:
            summary += "✅ No Cursor bot findings detected - either no issues found or Cursor hasn't reviewed yet.\n\n"
        
        summary += f"""## 🏗️ Architecture Analysis

### Concerns:
"""
        for concern in architecture['concerns']:
            summary += f"- ⚠️ {concern}\n"
        
        summary += f"\n### Improvements:\n"
        for improvement in architecture['improvements']:
            summary += f"- ✅ {improvement}\n"
        
        summary += f"\n### Recommendations:\n"
        for rec in architecture['recommendations']:
            summary += f"- 💡 {rec}\n"
        
        summary += f"""

## ⚡ Performance Analysis

### Optimizations Detected:
"""
        for opt in performance['optimizations']:
            summary += f"- 🚀 {opt}\n"
        
        summary += f"\n### Performance Concerns:\n"
        for concern in performance['concerns']:
            summary += f"- ⚠️ {concern}\n"
        
        summary += f"""

## 🧪 Test Strategy Analysis

**Test Types Affected**: {', '.join(testing['test_types']) if testing['test_types'] else 'None detected'}

### Recommendations:
"""
        for rec in testing['recommendations']:
            summary += f"- 📋 {rec}\n"
        
        summary += f"""

## 📋 Action Items

### For Developer:
- [ ] Address any Cursor bot findings above
- [ ] Verify CI/CD pipeline passes with changes
- [ ] Update documentation if architectural changes made
- [ ] Consider performance impact on test execution time

### For Reviewer:
- [ ] Validate Cursor bot findings are addressed
- [ ] Review architectural decisions align with project goals
- [ ] Confirm test coverage is appropriate
- [ ] Check that performance optimizations don't break functionality

## 🔄 Next Steps

1. **Address Critical Issues**: Fix any blocking issues identified by Cursor bot
2. **Test Thoroughly**: Run full test suite to ensure no regressions
3. **Update Docs**: Update relevant documentation for architectural changes
4. **Monitor Performance**: Watch CI/CD performance after merge

---
*Generated by Claude Code Review Integration*
*Repository: {pr_data['pr'].base.repo.full_name}*
"""
        
        return summary


@click.command()
@click.option('--pr-number', required=True, type=int, help='PR number to analyze')
@click.option('--repository', required=True, help='Repository in format owner/repo')
@click.option('--output-file', default='claude_review_results.md', help='Output file for results')
def main(pr_number: int, repository: str, output_file: str):
    """Run Claude Code review analysis on a PR."""
    
    github_token = os.getenv('GITHUB_TOKEN')
    if not github_token:
        click.echo("❌ GITHUB_TOKEN environment variable required", err=True)
        return 1
    
    try:
        analyzer = PRAnalyzer(github_token, repository)
        
        click.echo(f"🔍 Analyzing PR #{pr_number} in {repository}")
        
        # Get PR data
        pr_data = analyzer.get_pr_data(pr_number)
        
        # Extract Cursor findings
        cursor_findings = analyzer.extract_cursor_findings(pr_data)
        
        # Run analyses
        architecture = analyzer.analyze_architecture_impact(pr_data)
        performance = analyzer.analyze_performance_impact(pr_data)
        testing = analyzer.analyze_test_strategy(pr_data)
        
        # Generate summary
        summary = analyzer.generate_review_summary(
            pr_data, cursor_findings, architecture, performance, testing
        )
        
        # Save results
        with open(output_file, 'w') as f:
            f.write(summary)
        
        # Save cursor findings as JSON for further processing
        with open('cursor_findings.json', 'w') as f:
            json.dump(cursor_findings, f, indent=2, default=str)
        
        click.echo(f"✅ Analysis complete! Results saved to {output_file}")
        
        # Print key findings
        click.echo(f"\n📊 Summary:")
        click.echo(f"   • Cursor findings: {len(cursor_findings)}")
        click.echo(f"   • Architecture concerns: {len(architecture['concerns'])}")
        click.echo(f"   • Performance optimizations: {len(performance['optimizations'])}")
        click.echo(f"   • Files changed: {len(pr_data['changed_files'])}")
        
    except Exception as e:
        click.echo(f"❌ Error analyzing PR: {e}", err=True)
        return 1


if __name__ == '__main__':
    main()