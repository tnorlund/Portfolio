import Foundation

#if os(macOS)
import CoreGraphics

/// Returns a keep-mask that drops lines whose perpendicular ("cross-axis")
/// offset from the cluster's main column exceeds a robust threshold.
///
/// A receipt is a narrow vertical strip: its text lines stack along the strip's
/// long axis and share a common column. Lines that stick out **sideways** — an
/// adjacent menu pressed against the edge, or a second overlapping receipt — are
/// cross-axis outliers. We project each line centroid onto the cross-axis (the
/// text-baseline direction, perpendicular to the stacking direction), take the
/// median and MAD, and reject lines beyond `k` robust deviations.
///
/// Self-scaling and conservative by construction:
///   - The threshold is `k · 1.4826 · MAD`, i.e. relative to the receipt's own
///     width, so a clean single-column receipt keeps every line (no line is a
///     multi-MAD outlier of its own tight column).
///   - Below a minimum line count, or when the spread is degenerate, it returns
///     all-true and changes nothing.
///
/// - Parameters:
///   - lines: The cluster's lines (normalized, bottom-left-origin coords).
///   - k: Robust-deviation multiplier for the reject distance (default 4.0).
/// - Returns: A Bool per line — true = keep (inlier), false = drop (outlier).
public func crossAxisInlierMask(_ lines: [Line], k: CGFloat = 4.0) -> [Bool] {
    // Too few lines to robustly estimate a column — keep everything.
    guard lines.count >= 6 else {
        return Array(repeating: true, count: lines.count)
    }

    let centroids = lines.map { line -> (CGFloat, CGFloat) in
        let cx =
            (line.topLeft.x + line.topRight.x + line.bottomLeft.x
                + line.bottomRight.x) / 4.0
        let cy =
            (line.topLeft.y + line.topRight.y + line.bottomLeft.y
                + line.bottomRight.y) / 4.0
        return (cx, cy)
    }

    // Cross-axis = text-baseline direction (perpendicular to the vertical
    // stacking of a portrait receipt). For an upright receipt this is ~x.
    let avgAngle =
        lines.reduce(0.0) { $0 + $1.angleRadians } / CGFloat(lines.count)
    let ax = cos(avgAngle)
    let ay = sin(avgAngle)

    let medX = median(centroids.map { $0.0 })
    let medY = median(centroids.map { $0.1 })
    let offsets: [CGFloat] = centroids.map { c in
        let dx: CGFloat = c.0 - medX
        let dy: CGFloat = c.1 - medY
        return dx * ax + dy * ay
    }

    let medOff = median(offsets)
    let mad = median(offsets.map { abs($0 - medOff) })

    // Degenerate spread (near single-point column) — don't trim.
    guard mad > 1e-4 else {
        return Array(repeating: true, count: lines.count)
    }

    // Conservative: drop only lines that are extreme cross-axis outliers of an
    // otherwise-tight column (e.g. a single stray line from a table edge). This
    // deliberately does NOT try to split two overlapping receipts — a gap-based
    // split was tried and regressed clean tilted receipts (it cut real header/
    // footer content), so that case is left to a content-based pass.
    // Interval-based rejection (conservative). Centroid distance alone would drop
    // a short, off-center but legitimate line — e.g. a right-aligned TOTAL whose
    // centroid sits far from the column median even though its text still lies
    // WITHIN the receipt's column, or a centered header on a receipt with a
    // tight body. Instead, reject a line only when its cross-axis EXTENT is
    // wholly DISJOINT from the receipt column envelope: a menu / adjacent-document
    // line pressed sideways against the receipt. A total/header overlaps the
    // column and is kept; a truly sideways line is dropped even when it is the
    // top-most or bottom-most line (which a longitudinal-extreme exception would
    // have wrongly protected, re-admitting menu bleed).
    let intervals = lines.map { line -> (CGFloat, CGFloat) in
        let ps = [
            line.topLeft.x * ax + line.topLeft.y * ay,
            line.topRight.x * ax + line.topRight.y * ay,
            line.bottomLeft.x * ax + line.bottomLeft.y * ay,
            line.bottomRight.x * ax + line.bottomRight.y * ay,
        ]
        return (ps.min() ?? 0, ps.max() ?? 0)
    }
    let medLow = median(intervals.map { $0.0 })
    let medHigh = median(intervals.map { $0.1 })
    let colWidth = max(0, medHigh - medLow)
    // Generous envelope: half the column width on each side, floored at the
    // MAD-based reject distance. A line is an outlier only if its whole cross-
    // axis interval lies beyond this envelope (no overlap).
    let tol = max(0.5 * colWidth, k * 1.4826 * mad)
    let loBound = medLow - tol
    let hiBound = medHigh + tol
    return intervals.map { $0.1 >= loBound && $0.0 <= hiBound }
}

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
