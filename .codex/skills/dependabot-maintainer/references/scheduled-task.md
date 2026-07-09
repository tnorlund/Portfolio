# Scheduled Dependabot Maintenance

Use this reference when creating a recurring Codex scheduled task or a Claude routine for Portfolio Dependabot maintenance.

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
5. Merge only Dependabot-authored, Dependabot-committed, manifest-only PRs with green CI, stable head SHAs, and `MERGEABLE/CLEAN` merge state.
6. Do not merge major-version updates unless the skill's guardrails say they are explicitly allowed by the prompt or prior user approval.
7. After any merge, wait for the resulting main-branch CI run and summarize the outcome.

Leave blocked PRs open with a short explanation in the final message.
```

Use the narrowest permissions that still allow GitHub reads, GitHub PR comments, GitHub merge actions, local shell, and network access for dependency installation. Keep the scheduled task in a dedicated worktree so it cannot overwrite unfinished local work.

## Hook Guardrail

Use the script's `guard` command as the deterministic pre-merge hook for either Codex or Claude:

```bash
python .codex/skills/dependabot-maintainer/scripts/dependabot_maintainer.py guard <PR_NUMBER>
```

The hook should run immediately before any merge action. A non-zero exit means the agent must stop and report the reason instead of merging. Keep this guard in the prompt or hook configuration rather than relying on model judgment alone.

Local verification also has a guard before any PR code is fetched into a worktree and installed. The npm verification path uses `npm ci --ignore-scripts` before running trusted project test commands.

## Optional Claude Routine

Use the same workflow prompt with Claude Code if you want a second reviewer. Keep Claude in review/comment mode unless you explicitly want it to merge. A good split is:

- Codex: deterministic report, local verification, rebase requests, merge gate.
- Claude: release-note risk summary and manual review for major-version or broad lockfile updates.

Do not let both agents merge the same PR family concurrently. One agent should own the merge lane for a given Dependabot batch.
