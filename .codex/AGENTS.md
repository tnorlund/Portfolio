# AGENTS.md

## Code Style

- Use `black` and then `pylint` with a line length of 79 characters.
- Organize imports using `isort` with the `black` profile.
- Enforce type annotations and check with `mypy`.
- Run code quality analysis with `pylint` for comprehensive linting beyond basic style checks.

## Testing

### Python Testing

- Run tests using `pytest`:
  - For `receipt_dynamo`
    - Unit: `pytest -m unit receipt_dynamo`
    - Integration `pytest -m integration receipt_dynamo`
  - For `receipt_label`
    - All tests: `pytest receipt_label`
    - Specific test: `pytest receipt_label/tests/test_validation.py`
    - With coverage: `pytest --cov=receipt_label receipt_label`
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
- `receipt_label/`: Processes receipts to extract, label, and validate data using ML and external APIs.

## receipt_label Package Development

### Key Concepts

- **Dual Embedding Strategy**: Each receipt word gets word-level (semantic) and context-level (layout) embeddings
- **3-Pass Validation**: Batch GPT → Embedding refinement with Pinecone → Agentic resolution
- **Batch Processing**: Uses OpenAI Batch API for cost-efficient processing at scale

### Important Files

- **Entry Points**:
  - `receipt_label/core/labeler.py`: Main orchestration
  - `receipt_label/submit_line_embedding_batch/submit_line_batch.py`: Batch line embedding submission
- **Models**: `receipt_label/models/` - Receipt, Word, Line, Validation data structures
- **Validators**: `receipt_label/label_validation/` - Field-specific validation logic

### Development Workflow

1. **Before Making Changes**:

   - Review existing patterns in similar files
   - Check test fixtures in `receipt_label/tests/fixtures/`
   - Understand the DynamoDB entity models from `receipt_dynamo`

2. **Common Tasks**:

   - Adding a validator: Create in `label_validation/validate_<field>.py`
   - Modifying batch logic: Review `submit_*_batch/` and `poll_*_batch/` patterns
   - Working with embeddings: Update metadata, never duplicate vectors

3. **Testing Changes**:
   ```bash
   cd receipt_label
   pytest tests/test_<module>.py  # Test specific module
   black receipt_label/           # Format code
   mypy receipt_label/            # Type check
   ```

### Offline Testing Support
**All tests can run completely offline!** The package has comprehensive mocking:
- **AWS Services**: Mocked with `moto` (DynamoDB, S3)
- **OpenAI API**: Mocked with `pytest-mock`
- **Pinecone**: Mocked with `pytest-mock`
- **Google Places API**: Custom mock implementation

No internet connection or API keys needed for testing. The test suite validates business logic while isolating external dependencies.

### Environment Variables

**For Production/Development** (with real API calls):
```bash
OPENAI_API_KEY        # Required for GPT and embeddings
PINECONE_API_KEY      # Required for vector storage
AWS_ACCESS_KEY_ID     # Required for DynamoDB/S3
AWS_SECRET_ACCESS_KEY # Required for DynamoDB/S3
GOOGLE_PLACES_API_KEY # Required for address validation
```

**For Testing**: None required! All external services are mocked.

### Key Patterns to Follow

- Always update entity status in DynamoDB when processing
- Use idempotent operations for retry safety
- Handle rate limits and API errors gracefully
- Log important operations for debugging
- Keep embedding metadata updated instead of creating duplicates

## Additional Notes

- Use Python 3.12 or higher.
- The entities are stored in `receipt_dynamo/receipt_dynamo/entities` and the accessors are in `receipt_dynamo/receipt_dynamo/data`. The style and formatting should remain similar in these directories.
- When working with `receipt_label`, ensure you understand the batch processing flow and status tracking mechanisms.
