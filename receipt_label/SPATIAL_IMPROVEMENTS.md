# Spatial Receipt Processing Improvements

## Problem Statement
The original receipt processing relied heavily on Pinecone semantic understanding for every receipt, which was:
- Expensive (full semantic analysis for every receipt)
- Slow (multiple API calls per receipt)
- Sometimes inaccurate on receipts with poor OCR or complex layouts

The key insight was that Y-coordinate proximity is crucial - OCR coordinates start at y=0 at the bottom, so we were incorrectly grouping prices from different receipt sections (e.g., store address "1012" at top vs year "2024" at bottom).

## Implemented Solutions

### 1. Y-Coordinate Clustering in Vertical Alignment
**File**: `receipt_label/spatial/vertical_alignment_detector.py`

**Key Changes**:
- Added `_cluster_by_y_coordinate()` method to group prices by vertical proximity
- Simple approach for small groups (≤3 items): keep as single cluster
- Line-based clustering for larger groups with configurable gap tolerance (20 lines max)
- Dynamic Y-span tracking and proximity scoring

**Result**: Successfully distinguishes real price columns from coincidentally aligned values at different receipt sections.

### 2. Coordinate System Bug Fix
**Problem**: OCR data had "bottom_left" and "bottom_right" coordinates swapped
**Solution**: Use min/max of all X coordinates instead of relying on coordinate naming
**Impact**: Improved spatial alignment accuracy

### 3. Reasonable Value Filtering
**Addition**: Filter currency values to reasonable grocery range ($0.01 to $999.99)
**Purpose**: Exclude OCR errors like $1008000000.00 and $9980866666686.00
**Location**: Applied in both math solver and Pinecone detectors

### 4. Performance Optimizations
**Math Solver**: Limited to 50 solutions max to prevent combinatorial explosion
**Pinecone Scoring**: Limited to 20 solutions with early termination at 70% confidence
**Strategy**: Sort solutions by confidence to check best ones first

## Tiered Processing Approach

### Current Strategy
1. **Primary**: Math + Spatial approach (no Pinecone needed)
2. **Fallback**: Pinecone semantic validation only when math/spatial fails

### Confidence Classification
- **High Confidence**: Single solution with good alignment → Use directly, no Pinecone
- **Medium Confidence**: Few solutions with tax structure → Quick Pinecone validation
- **Low Confidence**: Many solutions or poor alignment → Full Pinecone analysis
- **No Solution**: Math failed completely → Full semantic approach

## Analysis Results (In Progress)

### Current Status
- Script `analyze_fallback_needs.py` is analyzing all receipts with pure math+spatial
- Processed 62+ receipts so far, still running
- Y-coordinate clustering working well with high confidence scores
- Finding solid price columns with confidence scores >1.0

### Performance Issues Discovered
- Script taking >5 minutes for ~300 receipts
- Potential inefficiency in complex receipts with many currency values
- Math solver may be slow with large combination spaces

## Files Modified
1. `receipt_label/spatial/vertical_alignment_detector.py` - Y-coordinate clustering
2. `receipt_label/spatial/pinecone_line_item_detector.py` - Value filtering, performance limits
3. `receipt_label/spatial/math_solver_detector.py` - Solution limits
4. `analyze_fallback_needs.py` - Full analysis script (new)

## Next Steps
1. Complete fallback analysis to quantify Pinecone cost reduction
2. Optimize math solver for better performance on complex receipts
3. Implement tiered processing in main detector based on analysis results
4. Consider alternative validation methods that don't require Pinecone