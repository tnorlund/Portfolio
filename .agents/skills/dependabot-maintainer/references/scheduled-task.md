# Scheduled Dependabot Maintenance

Use this reference when creating a recurring Codex scheduled task or a Claude routine for Portfolio Dependabot maintenance.

## GitHub Actions workflow

The workflow runs on `ubuntu-latest`, using Python 3.13 and Node 22. Its Thursday 13:40 UTC schedule follows the historical weekly batch; Dependabot does not currently pin an exact day/time. Manual dispatch defaults to report mode. Scheduled merges require `DEPENDABOT_AUTOMERGE=true`; that opt-in is separate from merging this workflow.

Merge mode requires a user PAT in `DEPENDABOT_MAINTAINER_TOKEN`: Contents and Pull requests read/write, Actions, Checks and Commit statuses read (or classic `repo`). Its pushes trigger the deployment workflow; pushes made with the built-in token do not. See [GitHub's workflow-trigger documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow). The verifier subprocess receives only the read-only `GITHUB_TOKEN`, never this write token. Report mode requires no PAT.

For each ready PR, the orchestrator requires the current main release to pass, runs local verification, and refuses a changed head or advancing main. After merging it waits up to 30 minutes for that exact commit's `CI/CD Pipeline` push run, requiring both `Deploy` and `Smoke Tests` to succeed. It never cancels a deployment. Local verification has a 20-minute bound; the job allows 210 minutes for up to three serial merges. Any failure stops further merges and rebase requests. Major updates are excluded from this batch workflow, including manual dispatch. Review and verify a specific major update with the single-PR maintainer command instead.

The summary and `dependabot-maintenance-report` artifact identify merged heads, merge commits, verified release run IDs, skipped PRs, rebases, and PRs left by the run limit. Root CI collects `tests/test_dependabot_maintenance_ci.py`; its GitHub and verifier calls are intercepted, so the failure scenarios cannot mutate the live queue.

Read-only local exercise:

```bash
python .agents/skills/dependabot-maintainer/scripts/dependabot_maintenance_ci.py --mode merge --dry-run
```

## Recommended Codex Scheduled Task

Cadence: weekly, 30 to 60 minutes after Dependabot's weekly run.

Project: Portfolio.

Worktree mode: new worktree.

Prompt:

```text
Use $dependabot-maintainer in the Portfolio repo.

Review all open Dependabot PRs. For each PR:
1. Run the Dependabot maintainer report.
2. Inspect the diff and changed files.
3. Run local verification for changed dependency manifests when appropriate.
4. If a PR is conflicting, ask Dependabot to rebase it and wait for the new checks.
5. Merge only Dependabot-authored, GitHub-mapped and verified Dependabot-committed, manifest-only PRs with green CI, stable head SHAs, and `MERGEABLE/CLEAN` merge state.
6. Do not merge major-version updates unless the skill's guardrails say they are explicitly allowed by the prompt or prior user approval.
7. After each merge, require successful Deploy and Smoke Tests on that exact main commit before another merge. Summarize the outcome.

Leave blocked PRs open with a short explanation in the final message.
```

Use the narrowest permissions that still allow GitHub reads, GitHub PR comments, GitHub merge actions, local shell, and network access for dependency installation. Keep the scheduled task in a dedicated worktree so it cannot overwrite unfinished local work.

## Hook Guardrail

Use the script's `guard` command as the deterministic pre-merge hook for either Codex or Claude:

```bash
python .agents/skills/dependabot-maintainer/scripts/dependabot_maintainer.py guard <PR_NUMBER>
```

The hook should run immediately before any merge action. A non-zero exit means the agent must stop and report the reason instead of merging. Keep this guard in the prompt or hook configuration rather than relying on model judgment alone.

Local verification also has a guard before any PR code is fetched into a worktree and installed. After fetching, the resolved ref must still match the guarded head SHA. The npm verification path uses `npm ci --ignore-scripts` and refuses PRs that change the npm script definitions it would run. Opaque non-JSON lockfile edits and SHA-only action ref updates stay manual. `receipt_upload` verification installs the same local sibling package stack used by CI.

## Optional Claude Routine

Use the same workflow prompt with Claude Code if you want a second reviewer. Keep Claude in review/comment mode unless you explicitly want it to merge. A good split is:

- Codex: deterministic report, local verification, rebase requests, merge gate.
- Claude: release-note risk summary and manual review for major-version or broad lockfile updates.

Do not let both agents merge the same PR family concurrently. One agent should own the merge lane for a given Dependabot batch.
