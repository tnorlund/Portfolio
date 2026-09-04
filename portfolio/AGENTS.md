# portfolio/ (Next.js 16, React 19, Pages Router)

Deltas to the root `AGENTS.md`.

- Run every npm command from this directory. `jest`, `eslint`, and `tsc` are
  local to `portfolio/node_modules/`; a `command not found` means wrong cwd.
- Checks, in CI order: `npm run lint`, `npm run type-check`, `npm run test:ci`.
  Locally `npm test` (or `npm test -- path/to/file.test.tsx`) is enough.
  Jest enforces global coverage thresholds (40% lines/statements, 35%
  branches/functions); do not lower them.
- Pages live in `pages/`, components in `components/`, hooks in `hooks/`,
  shared logic in `utils/` and `services/`, tests in `__tests__/` or beside the
  source as `*.test.ts(x)`. E2E tests use Playwright (`npm run test:e2e`,
  targets `localhost:3000` locally and `3001` in CI; see `playwright.config.ts`).
- ESLint uses a flat config (`eslint.config.js`) with `react-hooks`,
  `jsx-a11y`, and `@next/eslint-plugin-next`. Common failures: hooks called
  conditionally, unescaped `'`/`"` in JSX text (use `&apos;`/`&quot;`), raw
  `<img>` instead of `next/image`, missing `useEffect` dependencies.
- `npm run dev` and `npm run build` first bundle the rotoscope workers
  (`build:rotoscope-workers`); WASM sources under `wasm/` need `npm run build:wasm`.
- Visual changes need before/after screenshots in the PR: follow the
  `pr-screenshots` skill. Never commit screenshots to `main`.
- In TypeScript switch statements over unions or enums, add a `never` check in
  `default` so new variants fail to compile until handled.
