#!/usr/bin/env python3
"""
Fetch all PR review data with pagination using GitHub CLI.

Usage:
    python scripts/fetch-pr-reviews-paginated.py <PR_NUMBER>
"""

import json
import subprocess
import sys
from pathlib import Path


def run_gh_api(query, variables):
    """Run GitHub GraphQL API query via gh CLI."""
    cmd = ['gh', 'api', 'graphql', '-f', f'query={query}']

    for key, value in variables.items():
        if isinstance(value, int):
            cmd.extend(['-F', f'{key}={value}'])
        elif value is not None:
            cmd.extend(['-f', f'{key}={value}'])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error running gh api: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    return json.loads(result.stdout)


def fetch_reviews_paginated(owner, repo, pr_number):
    """Fetch all reviews with pagination."""
    query = '''
query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviews(first: 50, after: $cursor) {
        totalCount
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          author { login }
          authorAssociation
          body
          submittedAt
          state
          commit { oid }
          comments(first: 100) {
            totalCount
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              id
              path
              position
              line
              body
              createdAt
              diffHunk
            }
          }
        }
      }
    }
  }
}
'''

    all_reviews = []
    cursor = None
    page = 1

    while True:
        print(f"  📄 Fetching reviews page {page}...", file=sys.stderr)

        variables = {
            'owner': owner,
            'repo': repo,
            'pr': pr_number,
            'cursor': cursor
        }

        result = run_gh_api(query, variables)
        reviews_data = result['data']['repository']['pullRequest']['reviews']

        page_reviews = reviews_data['nodes']
        all_reviews.extend(page_reviews)

        print(f"     ✓ Got {len(page_reviews)} reviews", file=sys.stderr)

        if not reviews_data['pageInfo']['hasNextPage']:
            break

        cursor = reviews_data['pageInfo']['endCursor']
        page += 1

    return all_reviews


def fetch_pr_info(pr_number):
    """Fetch basic PR information via gh CLI."""
    cmd = [
        'gh', 'pr', 'view', str(pr_number), '--json',
        'number,title,url,state,author,createdAt,updatedAt,baseRefName,headRefName,commits,additions,deletions,changedFiles'
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error fetching PR info: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    return json.loads(result.stdout)


def fetch_pr_comments(owner, repo, pr_number):
    """Fetch PR-level comments (not review comments)."""
    query = '''
query($owner: String!, $repo: String!, $pr: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      comments(first: 100) {
        totalCount
        nodes {
          id
          author { login }
          body
          createdAt
          isMinimized
        }
      }
    }
  }
}
'''

    variables = {'owner': owner, 'repo': repo, 'pr': pr_number}
    result = run_gh_api(query, variables)
    return result['data']['repository']['pullRequest']['comments']['nodes']


def main():
    if len(sys.argv) < 2:
        print("Usage: python fetch-pr-reviews-paginated.py <PR_NUMBER>")
        sys.exit(1)

    pr_number = int(sys.argv[1])
    owner = 'tnorlund'
    repo = 'Portfolio'

    output_dir = Path(__file__).parent.parent / 'pr-review-data'
    output_file = output_dir / f'pr-{pr_number}-complete.json'
    output_dir.mkdir(exist_ok=True)

    print(f"🔍 Fetching complete PR data for #{pr_number}...\n", file=sys.stderr)

    # Fetch PR info
    print("1️⃣  Basic PR information...", file=sys.stderr)
    pr_info = fetch_pr_info(pr_number)
    print(f"   ✓ Title: {pr_info['title']}", file=sys.stderr)
    print(f"   ✓ State: {pr_info['state']}", file=sys.stderr)
    print(f"   ✓ Commits: {len(pr_info['commits'])}\n", file=sys.stderr)

    # Fetch reviews with pagination
    print("2️⃣  Reviews with inline comments...", file=sys.stderr)
    reviews = fetch_reviews_paginated(owner, repo, pr_number)
    inline_count = sum(len(r.get('comments', {}).get('nodes', [])) for r in reviews)
    print(f"   ✓ Total reviews: {len(reviews)}", file=sys.stderr)
    print(f"   ✓ Total inline comments: {inline_count}\n", file=sys.stderr)

    # Fetch PR comments
    print("3️⃣  PR-level comments...", file=sys.stderr)
    comments = fetch_pr_comments(owner, repo, pr_number)
    print(f"   ✓ Total PR comments: {len(comments)}\n", file=sys.stderr)

    # Build summary
    reviews_by_author = {}
    for r in reviews:
        author = r['author']['login']
        reviews_by_author[author] = reviews_by_author.get(author, 0) + 1

    coderabbit_reviews = [r for r in reviews if r['author']['login'] == 'coderabbitai']
    latest_review = max(reviews, key=lambda r: r['submittedAt']) if reviews else None

    # Combine all data
    combined = {
        'pr_info': pr_info,
        'reviews': reviews,
        'pr_comments': comments,
        'summary': {
            'total_reviews': len(reviews),
            'total_inline_comments': inline_count,
            'total_pr_comments': len(comments),
            'reviews_by_author': [
                {'author': k, 'count': v}
                for k, v in sorted(reviews_by_author.items(), key=lambda x: x[1], reverse=True)
            ],
            'latest_review': latest_review['submittedAt'] if latest_review else None,
            'coderabbit_reviews': len(coderabbit_reviews)
        },
        'fetched_at': subprocess.run(['date', '-u', '+%Y-%m-%dT%H:%M:%SZ'],
                                     capture_output=True, text=True).stdout.strip()
    }

    # Save to file
    print("4️⃣  Saving data...", file=sys.stderr)
    with open(output_file, 'w') as f:
        json.dump(combined, f, indent=2)
    print(f"   ✓ Saved to: {output_file}\n", file=sys.stderr)

    # Display summary
    print("📊 Summary:", file=sys.stderr)
    print(f"   Reviews: {combined['summary']['total_reviews']}", file=sys.stderr)
    print(f"   Inline comments: {combined['summary']['total_inline_comments']}", file=sys.stderr)
    print(f"   PR comments: {combined['summary']['total_pr_comments']}", file=sys.stderr)
    print(f"   CodeRabbit reviews: {combined['summary']['coderabbit_reviews']}", file=sys.stderr)
    print(f"   Latest review: {combined['summary']['latest_review']}", file=sys.stderr)
    if pr_info['commits']:
        print(f"   Latest commit: {pr_info['commits'][-1]['oid']}", file=sys.stderr)
    print("\n   By author:", file=sys.stderr)
    for item in combined['summary']['reviews_by_author']:
        print(f"     {item['author']}: {item['count']} reviews", file=sys.stderr)

    print(f"\n✅ Complete! Use this file with the parser:", file=sys.stderr)
    print(f"   python3 scripts/parse-coderabbit-reviews-complete.py {output_file}", file=sys.stderr)


if __name__ == '__main__':
    main()

