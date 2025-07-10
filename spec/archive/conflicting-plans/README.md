# Conflicting Plans Archive

## Overview

This directory contains planning documents that were created before discovering the core labeling system was broken. These plans are preserved for reference but should NOT be implemented until the core system is fixed.

## Archived Documents

### CLEANUP_ANALYSIS.md
- **Original Location**: `receipt_label/CLEANUP_ANALYSIS.md`
- **Created**: Before understanding that ReceiptAnalyzer and LineItemProcessor were commented out
- **Issue**: Focuses on label expansion when core labeling doesn't work
- **Status**: Superseded by `spec/core-labeling-fix/README.md`

### LABEL_CORPUS_EXPANSION.md
- **Original Location**: `receipt_label/LABEL_CORPUS_EXPANSION.md`
- **Created**: Before understanding that ReceiptAnalyzer and LineItemProcessor were commented out
- **Issue**: Plans to expand to 60+ labels when basic labeling is broken
- **Status**: Superseded by `spec/core-labeling-fix/README.md`

## Why These Plans Were Problematic

1. **Wrong Priority**: Focused on expanding labels instead of fixing core functionality
2. **Misunderstanding**: Assumed the labeling system worked when it doesn't
3. **Premature Optimization**: Planned advanced features before basic functionality
4. **Not Aligned**: Didn't align with the agentic refactor vision in `spec/technical-analysis/labeler-refactor.md`

## Correct Approach

The correct approach is outlined in `spec/core-labeling-fix/README.md`:

1. **Week 1**: Fix broken core components (ReceiptAnalyzer, LineItemProcessor)
2. **Week 2**: Refactor to agentic architecture
3. **Week 3**: Add Pinecone integration
4. **Week 4**: THEN consider label expansion

## Future Reference

Some concepts from these documents may be useful AFTER the core system is fixed:
- Label hierarchy structure
- Migration strategy concepts
- Performance optimization ideas
- Validation approaches

However, they should only be considered after completing the 4-week recovery plan.

---

**Archive Date**: 2025-07-09
**Reason**: Superseded by critical core system fix requirements
