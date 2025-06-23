# Receipt Upload Test Organization

This directory contains tests for the receipt upload service's OCR processing and boundary detection functionality.

## Test Files

### Core Functionality Tests

- **`test_boundary_fitting.py`** - Comprehensive boundary fitting tests (NEW - consolidated)
  - `TestBoundaryFitting`: Core boundary fitting tests including Theil-Sen bug demonstration
  - `TestBarReceiptBoundaries`: Bar receipt specific boundary detection tests  
  - `TestProposedFix`: Tests for the proposed horizontal line fix

- **`test_geometry.py`** - Core geometry function tests
  - Tests convex hull computation
  - Tests line intersection calculations
  - Tests boundary line creation functions

- **`test_boundary_equivalence.py`** - Hardcoded boundary tests
  - Ensures Python/TypeScript produce identical results
  - Tests with simple axis-aligned boundaries
  - Tests with slanted boundaries

### Integration Tests

- **`test_bar_receipt_equivalence.py`** - Bar receipt integration test
  - Tests full pipeline from OCR data to boundaries
  - Currently skipped due to coordinate system differences
  - Demonstrates boundary computation on real data

- **`test_process_ocr_results_integration.py`** - OCR processing integration tests
  - Tests processing of OCR results from AWS Textract
  - Tests S3 upload/download functionality
  - Tests image type classification

### Utility Tests

- **`test_utils.py`** - Utility function tests
  - Tests S3 operations
  - Tests image processing utilities

## Test Fixtures

- **`bar_receipt.json`** - Raw OCR data from AWS Textract for a bar receipt

## Key Testing Insights

### The Horizontal Line Bug

The Theil-Sen regression implementation has a bug when handling horizontal lines:

```python
# Current behavior (BUG):
if abs(slope) < 1e-6:
    return {"isVertical": True, "x": intercept, ...}  # WRONG!

# Fixed behavior:
if abs(slope) < 1e-6:
    return {"isVertical": False, "slope": 0.0, "intercept": intercept}
```

### Test Consolidation

Previously had 19 test files with many debug/exploration tests. These have been consolidated into `test_boundary_fitting.py` while maintaining full coverage:
- Removed 12 debug/exploration files
- Kept 7 essential test files
- All 20 tests still pass

## Running Tests

```bash
# Run all tests
python -m pytest test/

# Run specific test file
python -m pytest test/test_boundary_fitting.py

# Run with verbose output
python -m pytest test/ -v

# Run specific test class
python -m pytest test/test_boundary_fitting.py::TestProposedFix

# Run with coverage
python -m pytest test/ --cov=receipt_upload
```