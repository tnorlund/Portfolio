---
name: dependabot-maintainer
description: Review, verify, rebase, and merge Dependabot pull requests in the Portfolio repo. Use when the user asks Codex to handle Dependabot PRs, automate dependency update review, run dependency-update guardrails, set up recurring Dependabot maintenance, or merge safe bot-authored dependency updates.
---

# Dependabot Maintainer

## Overview

Use this skill to turn Dependabot PR handling into a repeatable workflow. Prefer the bundled script for deterministic checks, then use GitHub tooling only after the script and CI agree the PR is safe.

## Workflow

1. Start with a clean view of open Dependabot PRs:

   ```bash
   python .codex/skills/dependabot-maintainer/scripts/dependabot_maintainer.py report
   ```

2. For each PR, inspect the changed files and risk class from the report.
   - `ready`: bot-authored, dependency-manifest-only, mergeable, and CI green.
   - `wait`: CI or mergeability is still settling.
   - `manual`: changed files or version movement need human review before merge.

3. Run local verification for dependency manifests when the update is not obviously docs-only or workflow-only:

   ```bash
   python .codex/skills/dependabot-maintainer/scripts/dependabot_maintainer.py verify <PR_NUMBER>
   ```

4. If a PR is dirty or conflicting after another Dependabot PR merges, ask Dependabot to rebase it:

   ```bash
   python .codex/skills/dependabot-maintainer/scripts/dependabot_maintainer.py rebase <PR_NUMBER>
   ```

   Wait for the head SHA to change and for post-rebase CI to complete before merging.

5. Merge only after the PR is `ready`, local verification passed when relevant, and the head SHA is stable:

   ```bash
   python .codex/skills/dependabot-maintainer/scripts/dependabot_maintainer.py merge <PR_NUMBER> --yes
   ```

   Major-version updates are blocked by default. Use `--allow-major` only when the user explicitly approves the specific major update or local/manual review has already covered the risk.

6. After merging, poll `main` CI until it completes. Report merged PR numbers, merge commits, checks run, and any remaining open Dependabot PRs.

## Guardrails

- Do not merge PRs unless the author is a Dependabot bot identity.
- Do not merge PRs with source changes outside known dependency manifests, lockfiles, or GitHub workflow files.
- Do not merge while checks are queued, in progress, failed, cancelled, timed out, or missing.
- Do not merge when GitHub reports conflicts. Use `rebase`, then wait for the new head SHA and checks.
- Do not batch unrelated PRs into one local commit. Dependabot PRs should remain individually mergeable and auditable.
- Keep local work in scratch worktrees so unfinished user work in the main checkout is untouched.

## Scheduled Automation

For recurring Codex runs, read `references/scheduled-task.md` and use its prompt. Scheduled runs should use a new worktree, run the report first, and leave a summary when anything is blocked.
