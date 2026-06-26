# Branch Context

## Workflow Rule For This Branch

For the rest of this branch, work in milestone-sized units. At each
milestone, follow this order:

1. Make the entire intended milestone visible in the diff. For any new
   files, stage them or mark them intent-to-add with `git add -N` before
   review. Then run Codex on the current diff:

   ```bash
   git diff HEAD | codex exec --skip-git-repo-check "<focused review naming the change and the invariant it must hold; ask only for real HIGH/MEDIUM>"
   ```

2. Address every HIGH or MEDIUM finding and re-run Codex until it
   returns none.
3. Only then commit the milestone.
4. Push immediately after each milestone commit.

Never commit or push milestone work that has not passed a clean Codex
review over all staged, unstaged, and new files included in that milestone,
and do not accumulate large uncommitted WIP between milestones. The earlier
one-time WIP commit and push was only a backup and does not replace this
review-first cadence going forward.
