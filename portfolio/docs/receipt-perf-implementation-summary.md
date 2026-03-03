# Receipt Page Performance Implementation Summary

## Changes Implemented

1. **LazyMountInView** (`components/ui/LazyMountInView.tsx`)
   - Renders a placeholder until the wrapper is near the viewport (IntersectionObserver, rootMargin 200px, threshold 0.1).
   - Mounts children once when in view (`triggerOnce: true`).
   - Supports `minHeight` and optional `placeholder` to avoid layout shift.

2. **Receipt page** (`pages/receipt.tsx`)
   - Wrapped in `ClientOnly` + `LazyMountInView` with fixed-height placeholders:
     - WithinReceiptVerification (480px)
     - FinancialMathOverlay (480px)
     - BetweenReceiptVisualization (480px)
     - TrainingMetricsAnimation (480px)
     - LayoutLMInferenceVisualization (520px)
     - WordSimilarity (520px)
     - CICDLoop (600px)

3. **Heavy figure gating**
   - **LayoutLMBatchVisualization**: Initial fetch runs only when `inView` (ref `hasInitialFetchedRef`), so no API call until the section is visible.
   - **TrainingMetricsAnimation**: Fetch runs only when `inView` (ref `hasFetchedRef`), so no API call until visible.

4. **Bundle / imports**
   - No new eager imports. Receipt page still imports from `Figures` barrel; dynamic components (LayoutLM, TrainingMetrics, BetweenReceipt, FinancialMath, WithinReceipt) remain code-split and load when their wrapper mounts.

## Bundle Analysis (After)

- **Receipt page bundle**: ~237 KB (~71 KB gzip).
- **Total JS**: ~1012 KB (~326 KB gzip).
- Lazy chunks (720, 17, 638, 952, 887, …) remain; heavy figures load on demand when their `LazyMountInView` section enters the viewport.

## How to Validate

- **Initial responsiveness**: Load `/receipt`, scroll slowly — heavy sections should mount (and their chunks load) only as they approach the viewport; first load should feel lighter.
- **Chrome Performance**: Record "Load" then scroll; confirm long tasks and layout are reduced before first scroll into each heavy section.
- **Heap**: Take a snapshot after load (before scrolling); compare DOM/SVG node count to a snapshot after scrolling through the whole page; growth should align with sections that have been in view.

## Success Criteria (from plan)

- Noticeably lower initial main-thread activity on `/receipt`.
- Reduced first-scroll jank.
- No visual regressions in section reveal (placeholders reserve space; content appears when in view).
