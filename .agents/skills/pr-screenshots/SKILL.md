---
name: pr-screenshots
description: >-
  Capture before/after desktop and mobile screenshots of the Next.js portfolio
  with Playwright and host them in a PR description without committing them.
  Use when a PR changes anything visual under portfolio/.
---

# PR screenshots for frontend changes

## Prerequisites

- `@playwright/test` in `portfolio/` bundles `playwright-core`; import from
  `playwright-core`, not `playwright`.
- Run the script from `portfolio/` so Node resolves `node_modules/`.
- Install the checked-in lockfile with `npm ci`; diagnose missing dependencies
  without changing the manifest as a screenshot setup step.

## Workflow

1. `cd portfolio && npm run dev`
2. Copy `references/screenshot.mjs` to `portfolio/screenshot.mjs`; set `BASE`,
   the target heading/component selectors, and `outDir`.
3. Before: use a separate worktree at the current base, run `node screenshot.mjs before`.
4. After: use the feature worktree, run `node screenshot.mjs after`.
5. Delete `portfolio/screenshot.mjs` before committing.

The script hides the Next.js dev overlay before and after the page settles;
keep that step, otherwise the overlay lands in the capture.

## Hosting screenshots in the PR

Keep screenshots outside tracked source, including intermediate commits.
Upload them as GitHub PR attachments through an available attachment surface,
or publish a workflow artifact and link its verified Actions artifact page in
the PR. Confirm the upload succeeded before adding its URL; do not invent a
raw-content URL or treat a local path as hosted evidence.

If no attachment/upload surface is available, retain the local before/after
files outside the checkout and report their paths and that hosting remains
incomplete. Do not commit binary evidence to work around missing hosting.
