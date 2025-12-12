# PR Review Tools

This directory contains tools for fetching and analyzing PR comments, particularly from CodeRabbit AI reviews.

## Directory Structure

- `scripts/` - Scripts for fetching and parsing PR comments
- `data/` - Raw JSON data from GitHub API (gitignored)
- `reports/` - Generated review reports and summaries

## Scripts

### fetch-pr-reviews-paginated.py
Fetches complete PR review data using GitHub GraphQL API with pagination support.

**Usage:**
```bash
python .github/pr-reviews/scripts/fetch-pr-reviews-paginated.py <PR_NUMBER>
```

### parse-coderabbit-reviews-complete.py
Parses the fetched PR data and generates a detailed markdown report.

**Usage:**
```bash
python .github/pr-reviews/scripts/parse-coderabbit-reviews-complete.py data/pr-<NUMBER>-complete.json > reports/pr-<NUMBER>-detailed-review.md
```

## Workflow

1. Fetch PR data:
   ```bash
   cd .github/pr-reviews
   python scripts/fetch-pr-reviews-paginated.py 502
   ```

2. Parse and generate report:
   ```bash
   python scripts/parse-coderabbit-reviews-complete.py data/pr-502-complete.json > reports/pr-502-detailed-review.md
   ```

3. Review the generated markdown report to track addressed and outstanding issues.

## Notes

- Raw JSON data files are gitignored to avoid bloating the repository
- Review summaries and the scripts themselves are tracked in git
- Requires GitHub CLI (`gh`) to be installed and authenticated

