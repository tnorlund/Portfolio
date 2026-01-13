# Label Evaluator Visualization

## Overview

This document describes a React visualization component for the Label Evaluator pipeline. The visualization shows how receipts are processed through parallel LLM evaluators, with real timing data to demonstrate the relative speed of each evaluation stage.

## Current Architecture

The Label Evaluator uses a two-phase Step Function:

### Phase 1: Pattern Learning (per-merchant)
- `discover_patterns` - LLM learns line item structure
- `compute_patterns` - Build geometric patterns from training receipts

### Phase 2: Per-Receipt Validation

**Parallel (run simultaneously):**
| Lambda | Purpose | Speed |
|--------|---------|-------|
| `evaluate_labels` | Deterministic checks (text conflicts, missing labels) | Fast (~10ms) |
| `evaluate_currency_labels` | LLM reviews currency labels (SUBTOTAL, TAX, etc.) | Medium (~2s) |
| `evaluate_metadata_labels` | LLM reviews metadata labels (MERCHANT_NAME, DATE, etc.) | Medium (~1.5s) |

**Sequential (after parallel completes):**
| Lambda | Purpose | Speed |
|--------|---------|-------|
| `evaluate_financial_labels` | LLM validates math (GRAND_TOTAL = SUBTOTAL + TAX) | Slow (~3s) |

## Visualization Concept: "Triple Scanner"

### Visual Design

Show 3 parallel scanner bars racing down the receipt, each highlighting their relevant labels:

```
┌─────────────────────────────────────────┐
│  RECEIPT                                │
│  ═══════                                │
│  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  ← Metadata (blue)  │
│  The Home Depot #4523                   │
│  123 Main Street                        │
│                                         │
│  ░░░░░░░░░░░░░░░░░░  ← Currency (green) │
│  2x4 Lumber    $4.99                    │
│  Screws        $3.49                    │
│                                         │
│  ▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒  ← Deterministic    │
│  Subtotal      $8.48                    │
│  Tax           $0.72                    │
│  ─────────────────                      │
│  TOTAL         $9.20                    │
└─────────────────────────────────────────┘

         [After parallel phase]
                  ↓
┌─────────────────────────────────────────┐
│  FINANCIAL VALIDATION                   │
│  ════════════════════                   │
│  $8.48 + $0.72 = $9.20  ✓              │
│  (slow scanner bar for math check)      │
└─────────────────────────────────────────┘
```

### Scanner Types

| Scanner | Color | Speed | Labels Highlighted |
|---------|-------|-------|-------------------|
| Metadata | Blue | Fast (~0.5s) | MERCHANT_NAME, ADDRESS_LINE, DATE, TIME, PHONE_NUMBER |
| Currency | Green | Medium (~2s) | SUBTOTAL, TAX, LINE_TOTAL, UNIT_PRICE, QTY |
| Deterministic | Gray | Instant | Any conflicts or missing labels |
| Financial | Orange | Slow (~3s) | Math validation equation |

### Animation Sequence

1. **Scroll trigger**: Animation starts when component enters viewport
2. **Parallel start**: All 3 scanners begin from the top simultaneously
3. **Different speeds**: Scanner speed proportional to actual inference time
4. **Label highlights**: As scanner passes each word, highlight with decision badge:
   - Green border + ✓ for VALID
   - Red border + ✗ for INVALID
   - Yellow border + ? for NEEDS_REVIEW
5. **Wait state**: Financial validation shows "Waiting..." until parallel phase completes
6. **Final validation**: Orange scanner validates the math equation slowly

### Side Panel: Agent Status Cards

Show real-time status for each evaluator:

```
┌──────────────────────┐
│ 🔵 Metadata          │
│ ████████░░ 80%       │
│ Checking: DATE       │
│ 3 VALID, 0 INVALID   │
│ Time: 0.42s          │
└──────────────────────┘

┌──────────────────────┐
│ 🟢 Currency          │
│ ████░░░░░░ 40%       │
│ Checking: LINE_TOTAL │
│ 2 VALID, 1 INVALID   │
│ Time: 0.89s          │
└──────────────────────┘

┌──────────────────────┐
│ ⚪ Deterministic     │
│ ██████████ 100%      │
│ Complete             │
│ 0 conflicts found    │
│ Time: 0.01s          │
└──────────────────────┘
```

## Data Requirements

### Per-Receipt Data Structure

```typescript
interface ReceiptEvaluationData {
  receipt: {
    image_id: string;
    receipt_id: number;
    merchant_name: string;
    image_url?: string;
    words: Array<{
      text: string;
      label: string;
      bbox: { x: number; y: number; width: number; height: number };
    }>;
  };

  evaluations: {
    metadata: EvaluatorResult;
    currency: EvaluatorResult;
    deterministic: EvaluatorResult;
    financial: FinancialResult;
  };

  timing: {
    total_ms: number;
    parallel_phase_ms: number;
    financial_phase_ms: number;
  };
}

interface EvaluatorResult {
  start_ms: number;
  end_ms: number;
  decisions: Array<{
    word_index: number;
    word_text: string;
    label: string;
    decision: 'VALID' | 'INVALID' | 'NEEDS_REVIEW';
    reasoning: string;
    evaluated_at_ms: number;  // When this word was evaluated
  }>;
}

interface FinancialResult {
  start_ms: number;
  end_ms: number;
  equations: Array<{
    expression: string;      // "8.48 + 0.72 = 9.20"
    expected: number;
    actual: number;
    valid: boolean;
    reasoning: string;
  }>;
}
```

### Timing Data Source

Timing data should be captured during Step Function execution and stored in S3:

1. **Option A: Per-Lambda timing in outputs**
   - Each Lambda records its own `start_ms`, `end_ms`, and per-decision timestamps
   - Final aggregation step combines timing from all Lambdas

2. **Option B: Export LangSmith trace to S3**
   - `close_receipt_trace.py` Lambda already runs at end of each receipt
   - Query LangSmith for complete trace data (has all child span timings)
   - Export structured timing data to S3

**Recommended: Option B** - LangSmith already has the timing, just need to export it.

## Component Structure

```
LabelEvaluatorVisualization/
├── index.tsx                 # Main component with scroll trigger
├── ReceiptCanvas.tsx         # Receipt image with word bounding boxes
├── ScannerBars.tsx           # Animated scanner bars (3 parallel + 1 sequential)
├── AgentStatusCards.tsx      # Side panel with evaluator status
├── FinancialValidation.tsx   # Math equation animation
├── useEvaluationAnimation.ts # Animation timing hook
├── types.ts                  # TypeScript interfaces
└── mockData.ts               # Sample data for development
```

## Animation Library

Use `@react-spring/web` (already in project) for:
- Scanner bar position animations
- Label highlight fade-in
- Progress bar updates
- Staggered decision reveals

## API Endpoint

```
GET /api/label_evaluator/receipt_evaluation?receipt_id={id}

Response:
{
  "statusCode": 200,
  "data": ReceiptEvaluationData
}
```

Cache generator Lambda populates S3 with pre-computed visualization data.

## Future Enhancements

1. **Interactive mode**: Click on a word to see full decision reasoning
2. **Comparison view**: Show before/after label corrections
3. **Batch view**: Visualize multiple receipts from same merchant
4. **Performance metrics**: Show aggregate timing across many receipts
