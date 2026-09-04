# Scheduled Dependabot Maintenance

Use this reference when creating a recurring Codex scheduled task or a Claude routine for Portfolio Dependabot maintenance.

## GitHub Actions Workflow (default)

The deterministic part of this routine already runs in CI: `.github/workflows/dependabot-maintenance.yml` executes `scripts/dependabot_maintenance_ci.py` on `ubuntu-latest`.

- Schedule: `40 13 * * 4` (Thursday 13:40 UTC). Dependabot opens this repo's weekly batch on Thursdays at about 12:35 UTC; the offset gives the PRs' `CI/CD Pipeline` runs time to finish. Move the cron if `.github/dependabot.yml` gains an explicit `schedule.day` / `schedule.time`.
- Manual run: Actions -> "Dependabot maintenance" -> Run workflow, with `mode` (`report` or `merge`) and `allow_major` (boolean, default off).
- Gate: scheduled runs merge only when the repository variable `DEPENDABOT_AUTOMERGE` equals `true` (Settings -> Secrets and variables -> Actions -> Variables). Anything else means report-only. Scheduled runs never allow major-version merges.
- Token: merge mode needs the secret `DEPENDABOT_MAINTAINER_TOKEN`, a personal access token for a user with write access to the repo (fine-grained PAT: Contents read/write, Pull requests read/write; or classic `repo`). Reason: GitHub does not create workflow runs for events produced with `GITHUB_TOKEN` ("Triggering a workflow from a workflow"), so a merge made with `GITHUB_TOKEN` would push to `main` without running the push-triggered `CI/CD Pipeline` deploy. Dependabot also rejects `@dependabot rebase` comments from `github-actions[bot]` and GitHub App identities, so the PAT (not an App token) is used for rebase requests too. Report mode needs no secret; the workflow fails fast when merge mode lacks the token.
- What it does per run: classify every open Dependabot PR (`ready` / `wait` / `manual`), and in merge mode re-run the guard right before each `gh pr merge --squash --match-head-commit`, stop on the first merge failure, then post `@dependabot rebase` on PRs that GitHub now reports as `CONFLICTING`. It does not wait for CI; the next run picks those PRs up. It never runs the local `verify` path.
- Output: Markdown job summary (merged PRs with merge SHAs, skipped/rebase-requested PRs, remaining `wait`/`manual` PRs with reasons) plus a `dependabot-maintenance-report` artifact.

Use the Codex task below only for the judgement-heavy remainder (major updates, non-manifest changes, local `verify`), or as a fallback while the workflow is disabled.

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

Local verification also has a guard before any PR code is fetched into a worktree and installed. After fetching, the resolved ref must still match the guarded head SHA. The npm verification path uses `npm ci --ignore-scripts` and refuses PRs that change the npm script definitions it would run. Opaque non-JSON lockfile edits and SHA-only action ref updates stay manual. `receipt_upload` verification installs the same local sibling package stack used by CI.

## Optional Claude Routine

Use the same workflow prompt with Claude Code if you want a second reviewer. Keep Claude in review/comment mode unless you explicitly want it to merge. A good split is:

- Codex: deterministic report, local verification, rebase requests, merge gate.
- Claude: release-note risk summary and manual review for major-version or broad lockfile updates.

Do not let both agents merge the same PR family concurrently. One agent should own the merge lane for a given Dependabot batch.
