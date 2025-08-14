# Portfolio Test Organization

This directory contains tests for the portfolio application's receipt processing functionality.

## Test Files

### Core Algorithm Tests

- **`boundary-fitting.test.ts`** - Comprehensive boundary fitting tests
  - Demonstrates the Theil-Sen horizontal line bug
  - Shows the proposed fix for horizontal lines
  - Tests hardcoded boundary values
  - Tests exact boundary values from bar receipt fixture
  - Ensures Python/TypeScript parity

- **`boundary_equivalence.test.ts`** - Unit tests for boundary box computation
  - Tests `computeReceiptBoxFromBoundaries` with simple cases
  - Tests axis-aligned boundaries
  - Tests slanted boundaries

- **`bar_receipt.integration.test.ts`** - Integration test for full receipt processing
  - Uses real OCR data from bar receipt fixture
  - Tests the complete pipeline from OCR data to receipt boundaries
  - Currently skipped due to coordinate system differences

### API Tests

- **`receipt.test.ts`** - API tests for receipt fetching
  - Tests `api.fetchReceipts` function
  - Uses mock data from fixtures

## Test Fixtures

The `fixtures/` directory contains JSON test data:
- `bar_receipt.json` - OCR data from a bar receipt image
- `receipts.json` - Multiple receipt examples
- `stanley_receipt.json` - Receipt from Stanley marketplace
- `target_receipt.json` - Receipt from Target store

## Key Testing Insights

### The Horizontal Line Bug

Both Python and TypeScript implementations have the same bug in `createBoundaryLineFromTheilSen`:
- When Theil-Sen regression encounters horizontal lines, it returns `slope=0`
- This gets misinterpreted as a vertical line
- The fix is to correctly interpret `slope=0` as a horizontal line

### Python/TypeScript Parity

With the proposed fix, both implementations produce nearly identical results:
- Corner coordinates match within 0.00002 (floating-point precision)
- All corners stay within [0,1] normalized bounds
- The problematic bottom_right corner is fixed (was at y=7.916, now at y=0.180)

## Running Tests

```bash
# Run all tests
npm test

# Run specific test file
npm test -- boundary-fitting.test.ts

# Run with coverage
npm test -- --coverage
```