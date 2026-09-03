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
- The receipt page may need `npm install react-markdown` to render in dev.

## Workflow

1. `cd portfolio && npm run dev`
2. Copy `references/screenshot.mjs` to `portfolio/screenshot.mjs`; set `BASE`,
   the target heading/component selectors, and `outDir`.
3. Before: check out `main`, run `node screenshot.mjs before`.
4. After: check out the feature branch, run `node screenshot.mjs after`.
5. Delete `portfolio/screenshot.mjs` before committing.

The script hides the Next.js dev overlay before and after the page settles;
keep that step, otherwise the overlay lands in the capture.

## Hosting screenshots in the PR

Do not commit screenshots to `main`; they bloat history. Instead:

1. Temporarily commit them under `screenshots/` on the feature branch.
2. Reference them by commit SHA in the PR body:

```markdown
| Before | After |
|--------|-------|
| ![before](https://raw.githubusercontent.com/tnorlund/Portfolio/<sha>/screenshots/before-desktop.png) | ![after](https://raw.githubusercontent.com/tnorlund/Portfolio/<sha>/screenshots/after-desktop.png) |
```

3. Add a final commit removing `screenshots/`. The images stay reachable at the
   earlier SHA.
4. Squash-merge so the intermediate commits collapse.

Cursor cloud agents can instead save captures under `/opt/cursor/artifacts/`
and reference them with `<img>` tags; the PR tool uploads them.
