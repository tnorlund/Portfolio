# AI Assistant Context

This document provides context for AI assistants working with this repository.

## Project Overview

This repository contains:
1. **Portfolio Website** (`portfolio/`) - React/Next.js personal portfolio
2. **Receipt Processing System** (`receipt_*`) - Python-based OCR and ML pipeline
3. **Infrastructure** (`infra/`) - Pulumi IaC for AWS deployment

## Key Conventions

### Python Development
- Python 3.12 required
- Use virtual environments (`.venv`)
- Format with `black` (79 char limit) and `isort`
- Test with `pytest`
- Packages use editable installs: `pip install -e .`

### JavaScript/TypeScript
- Node.js 18+
- Next.js 14 with App Router
- TypeScript for type safety
- ESLint and Prettier for formatting

### Testing
```bash
# Install test dependencies first (required for async tests)
pip install -e "receipt_label[test]"

# Python tests
pytest receipt_label/tests/ -v
pytest receipt_label/tests/ -m "not integration"

# For CI environments - ensure async plugin is available
pip install pytest-asyncio>=0.24.0

# JavaScript tests
cd portfolio && npm test
```

### Common Tasks

**Format code:**
```bash
make format  # Runs black and isort
```

**Run tests:**
```bash
./scripts/test_runner.sh receipt_dynamo
```

**Deploy infrastructure:**
```bash
cd infra && pulumi up
```

## Project Structure

```
/
├── portfolio/          # Frontend application
├── infra/             # AWS infrastructure
├── receipt_dynamo/    # DynamoDB operations
├── receipt_label/     # ML labeling logic
├── receipt_upload/    # OCR processing
├── scripts/           # Utility scripts
└── docs/              # Documentation
```

## Important Notes

- **Package Separation**: Each `receipt_*` package has specific responsibilities. Don't mix concerns.
- **AWS Resources**: Most operations use DynamoDB, S3, and Lambda
- **Cost Optimization**: Keep AWS costs under $5/month
- **CI/CD**: GitHub Actions with self-hosted runners for cost savings

## Documentation

- Development setup: `docs/development/setup.md`
- Architecture: `docs/architecture/overview.md`
- Testing guide: `docs/development/testing.md`