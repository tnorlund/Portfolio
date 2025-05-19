# AGENTS.md

## Code Style

- Use `black` and then `flake8` with a line length of 79 characters.
- Organize imports using `isort` with the `black` profile.
- Enforce type annotations and check with `mypy`.

## Testing

- Run tests using `pytest`:
  - For `receipt_dynamo`
    - Unit: `pytest -m unit receipt_dynamo`
    - Integration `pytest -m integration receipt_dynamo`
- Ensure code coverage is measured with `pytest-cov`.
- Mock AWS services using `moto` during tests.

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
