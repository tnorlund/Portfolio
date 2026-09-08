---
name: codex-diff-review
description: >-
  Gate each milestone commit on a clean Codex review of the full diff. Use when
  the user asks for review-first, milestone-sized work, a "codex review loop",
  or to run codex exec over the current changes before committing or pushing.
---

# Codex diff review gate

Work in milestone-sized units. Before each commit:

1. Make the whole milestone visible in the diff. Stage new files or mark them
   intent-to-add with `git add -N` so they appear in `git diff HEAD`.
2. Run Codex over the diff with a focused prompt that names the change and the
   invariant it must hold, and asks only for real HIGH/MEDIUM findings:

   ```bash
   git diff HEAD | codex exec --skip-git-repo-check \
     "Review this diff for <change>. It must keep <invariant>. Report only HIGH or MEDIUM findings."
   ```

3. Address every HIGH or MEDIUM finding and re-run until Codex returns none.
4. Commit the milestone, then push immediately.

Do not commit or push milestone work that has not passed a clean review over all
staged, unstaged, and new files in that milestone, and do not accumulate large
uncommitted WIP between milestones. A one-off WIP backup commit does not replace
this cadence.
