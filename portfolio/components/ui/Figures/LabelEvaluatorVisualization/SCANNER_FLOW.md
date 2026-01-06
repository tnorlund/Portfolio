# Label Evaluator Scanner Flow

This document describes the execution order and dependencies of the label evaluator scanners.

## Overview

The label evaluator runs multiple scanners to validate receipt labels. These scanners have specific dependencies that determine their execution order.

## Scanner Types

| Scanner | Type | Purpose |
|---------|------|---------|
| Line Item | LLM | Learns line item structure (single/multi-line, positions) |
| Geometric | Deterministic | Fast pattern analysis to flag anomalies |
| Metadata | LLM | Validates metadata labels (merchant name, address, date) |
| Currency | LLM | Validates currency labels (prices, totals) using line item patterns |
| Financial | LLM | Validates math relationships (subtotal + tax = total) |
| Review | LLM + ChromaDB | Reviews geometrically-flagged words with similarity evidence |

## Execution Flow

There are two independent branches:

### Branch 1: Currency/Metadata/Financial Chain

```
t=0 ─────────────────────────────────────────────────────────────►

    Line Item ████████████┐
                          └──► Currency ██████──┐
                                                ├──► Financial ████
    Metadata  ████████████──────────────────────┘
```

**Dependencies:**
- **Currency** waits for **Line Item** (needs line item patterns to validate prices)
- **Financial** waits for **Currency + Metadata** (needs all corrections before validating math)

### Branch 2: Geometric/Review Chain

```
t=0 ─────────────────────────────────────────────────────────────►

    Geometric ████──► [if issues found] Review ████
```

**Dependencies:**
- **Review** only runs if **Geometric** found issues
- **Review** only uses **Geometric** results (flagged words)

## Combined Flow Diagram

```
t=0 ─────────────────────────────────────────────────────────────►

    ┌─ Line Item ████████████┐
    │                        └──► Currency ██████──┐
    │                                              ├──► Financial ████
    │  Metadata  ████████████──────────────────────┘
    │
    └─ Geometric ████──► [if issues] Review ████
```

## Animation Timing

For visualization purposes, the scanners are animated with durations based on actual execution times from the cache data:

| Scanner | Duration Source | Notes |
|---------|----------------|-------|
| Line Item | `line_item_duration_seconds` | From merchant pattern file |
| Geometric | ~0.3s (fixed) | Deterministic, very fast |
| Metadata | `metadata.duration_seconds` | LLM call duration |
| Currency | `currency.duration_seconds` | LLM call duration, starts after Line Item |
| Financial | `financial.duration_seconds` | LLM call duration, starts after Currency+Metadata |
| Review | ~1s (fixed) | Only shown if geometric issues found |

## Decision Types

Each LLM scanner produces decisions for the words it evaluates:

| Decision | Color | Icon | Meaning |
|----------|-------|------|---------|
| VALID | Green | ✓ | Label is correct |
| INVALID | Red | ✗ | Label is incorrect |
| NEEDS_REVIEW | Orange | Person | Requires human review |

## Scanner Colors (Grouped by Dependency)

Scanners that depend on each other share the same color:

| Chain | Scanners | Color | CSS Variable |
|-------|----------|-------|--------------|
| Chain 1 | Line Item → Currency | Purple | --color-purple |
| Independent | Metadata | Blue | --color-blue |
| After Chain 1 | Financial | Yellow | --color-yellow |
| Chain 2 | Geometric → Review | Orange | --color-orange |

| Decision | Color | CSS Variable |
|----------|-------|--------------|
| VALID | Green | --color-green |
| INVALID | Red | --color-red |
| NEEDS_REVIEW | Yellow | --color-yellow |
