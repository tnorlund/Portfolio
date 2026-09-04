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
   - `ready`: Dependabot-authored PR and verified Dependabot head commits, dependency-manifest-only, `MERGEABLE/CLEAN`, non-major or explicitly approved, and CI green.
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
- Do not merge PRs unless every head commit has a GitHub-mapped Dependabot author and either a GitHub-mapped Dependabot committer or GitHub's verified Dependabot signature shape.
- Do not merge PRs with source changes outside known dependency manifests, lockfiles, or GitHub workflow files.
- Do not merge while checks are queued, in progress, failed, cancelled, timed out, or missing.
- Do not merge unless GitHub reports `MERGEABLE/CLEAN`. Use `rebase`, then wait for the new head SHA and checks.
- Do not run local dependency installs until the same author, state, file, commit-provenance, and major-version guards pass, then re-check that the fetched PR head is still the guarded SHA.
- Do not execute npm verification scripts from a PR that changes those script definitions; route those PRs to manual review.
- Do not treat unclear lockfile version movement, opaque non-JSON lockfile edits, or SHA-only action ref updates as safe. Unknown dependency version diffs require manual review.
- Do not batch unrelated PRs into one local commit. Dependabot PRs should remain individually mergeable and auditable.
- Keep local work in scratch worktrees so unfinished user work in the main checkout is untouched.

## Scheduled Automation (GitHub Actions)

`.github/workflows/dependabot-maintenance.yml` runs the deterministic path on `ubuntu-latest` every Thursday at 13:40 UTC (about an hour after Dependabot's weekly batch) and on demand via `workflow_dispatch` (`mode`: `report` | `merge`, `allow_major`). It drives `scripts/dependabot_maintenance_ci.py`, which reuses `classify` and the other guardrails from `dependabot_maintainer.py`, re-runs the guard immediately before each squash merge, asks Dependabot to rebase PRs left `CONFLICTING`, and writes the result to the job summary. It never runs `verify`; the PR's own CI covers dependency installs.

- Scheduled runs merge only when the repository variable `DEPENDABOT_AUTOMERGE` is exactly `true`; otherwise they only report. Scheduled runs never pass `--allow-major`.
- Merge mode requires the secret `DEPENDABOT_MAINTAINER_TOKEN`, a personal access token of a user with write access (fine-grained: Contents and Pull requests read/write on this repo). GitHub does not start workflow runs for events created with `GITHUB_TOKEN`, so a `GITHUB_TOKEN` merge would land on `main` without triggering the `CI/CD Pipeline` deploy, and Dependabot ignores `@dependabot rebase` comments from `github-actions[bot]`. The workflow fails fast if the secret is missing; report mode works with the read-only `GITHUB_TOKEN`.
- Local dry run (reads only): `python .codex/skills/dependabot-maintainer/scripts/dependabot_maintenance_ci.py --mode merge --dry-run`.

For recurring Codex runs instead of (or alongside) the workflow, read `references/scheduled-task.md` and use its prompt. Scheduled runs should use a new worktree, run the report first, and leave a summary when anything is blocked.
