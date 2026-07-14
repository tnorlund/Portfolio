import Foundation

#if os(macOS)
import CoreGraphics

// NOTE: This file previously exported `crossAxisInlierMask`, which trimmed
// "sideways" cross-axis outliers from a receipt cluster before warping. It was
// removed: deciding a line is background from geometry ALONE repeatedly proved
// unsafe (a right-aligned TOTAL, or a separately-recognized price/amount column,
// is cross-axis-disjoint from the description column yet is real receipt
// content). The two cases it targeted are handled with actual evidence instead —
// a colored menu/flyer background is dropped pre-clustering by the pixel-based
// BackgroundColorFilter, and two overlapping copies are separated (or flagged)
// by the duplicate splitter. The shared `median` helper below is still used by
// BackgroundColorFilter and DuplicateReceiptSplitter.

/// Median of a non-empty array (returns 0 for empty).
func median(_ values: [CGFloat]) -> CGFloat {
    guard !values.isEmpty else { return 0 }
    let sorted = values.sorted()
    let n = sorted.count
    if n % 2 == 1 {
        return sorted[n / 2]
    }
    return (sorted[n / 2 - 1] + sorted[n / 2]) / 2.0
}
#endif
