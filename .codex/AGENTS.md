# AGENTS.md

## Code Style

- Use `black` and then `flake8` with a line length of 79 characters.
- Organize imports using `isort` with the `black` profile.
- Enforce type annotations and check with `mypy`.

## Testing

### Python Testing

- Run tests using `pytest`:
  - For `receipt_dynamo`
    - Unit: `pytest -m unit receipt_dynamo`
    - Integration `pytest -m integration receipt_dynamo`
- Ensure code coverage is measured with `pytest-cov`.
- Mock AWS services using `moto` during tests.

### TypeScript/React Testing

- Run tests using `jest` and `@testing-library/react`:
  - All tests: `npm test`
  - Watch mode: `npm run test:watch`
  - Coverage: `npm run test:coverage`
  - CI mode: `npm run test:ci`
  - Specific test file: `npm test -- path/to/test.tsx`
- Run linting with `next lint`:
  - Check all files: `npm run lint`
  - Fix auto-fixable issues: `npm run lint -- --fix`
- Type checking: `npm run type-check`

#### Working Directory Requirements

**CRITICAL**: All npm commands must be run from the `portfolio/` directory, not the workspace root.

- **Correct**: `cd portfolio && npm test`
- **Incorrect**: `npm test` (from workspace root)

**Why**: Jest and other tools are installed locally in `portfolio/node_modules/`, not globally.

**Troubleshooting "command not found" errors**:

- If you see `sh: 1: jest: not found` or similar errors, you're likely in the wrong directory
- Always `cd portfolio` before running npm commands
- Use `pwd` to verify you're in `/path/to/workspace/portfolio`
- Alternatively, use `npx` for direct tool access: `npx jest` instead of `jest`

#### Test Categories

1. **Component Tests** (`*.test.tsx`):

   - Test React components in `portfolio/components/`
   - Focus on rendering, user interactions, and state changes
   - Use `@testing-library/react` for component testing
   - Example: `PhotoReceiptBoundingBox.test.tsx` for testing receipt visualization

2. **Utility Tests** (`*.test.ts`):

   - Test pure functions and utilities
   - Located in `portfolio/utils/` and `portfolio/services/`
   - Focus on input/output behavior and edge cases
   - Example: Testing geometry calculations in `utils/geometry.ts`

3. **Integration Tests** (`*.integration.test.tsx`):
   - Test component interactions and API calls
   - Use `msw` for mocking API responses
   - Located in `portfolio/__tests__/integration/`
   - Example: Testing receipt upload flow

#### Testing Guidelines

- Write tests before implementing new features (TDD)
- **Coverage Requirements**: Maintain 70% coverage for statements, branches, functions, and lines
- Mock external dependencies (API calls, browser APIs)
- Use snapshot testing sparingly, only for stable UI components
- Test error states and edge cases
- Use meaningful test descriptions that explain the behavior being tested
- Place test files in `__tests__/` directories or alongside source files with `.test.ts/.tsx` extension

#### Common ESLint Issues to Fix

- **React Hooks Rules**: Never call hooks conditionally - always call them at the top level
- **Unescaped Entities**: Use `&apos;` for apostrophes and `&quot;` for quotes in JSX text
- **Next.js Image Optimization**: Use `next/image` instead of `<img>` tags for better performance
- **Missing Dependencies**: Include all dependencies in useEffect dependency arrays

#### When to Run Tests

- **Pre-commit**: Run unit tests, type checking, and linting
  ```bash
  npm run lint && npm run type-check && npm test
  ```
- **CI/CD**: Run all tests including integration tests with coverage
  ```bash
  npm run test:ci
  ```
- **Local Development**: Run tests in watch mode
  ```bash
  npm run test:watch
  ```

## Commit and PR Guidelines

- Commit messages should follow the format: `[Component] Short description`
  - Example: `[receipt_upload] Add S3 upload functionality`
- Pull requests must include:
  - A summary of changes.
  - Testing steps and results.
  - Any relevant issue numbers.

## Project Structure

- `receipt_dynamo/`: Interfaces with DynamoDB for receipt data processing.
- `receipt_dynamo/tests`: Contains unit and integration tests for both packages.

## Additional Notes

- Use Python 3.8 or higher.
- The entities are stored in `receipt_dynamo/receipt_dynamo/entities` and the accessors are in `receipt_dynamo/receipt_dynamo/data`. The style and formatting should remain similar in these directories.
