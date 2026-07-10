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
    let reject = k * 1.4826 * mad
    var mask = offsets.map { abs($0 - medOff) <= reject }

    // Protect the longitudinal extremes. The top-most and bottom-most lines
    // along the receipt's stacking axis anchor the crop's vertical extent — the
    // store header and, critically, the grand TOTAL/TAX. A short, right- or
    // left-aligned total or header line has an off-center cross-axis centroid
    // and can read as an outlier of an otherwise tightly-aligned column; trimming
    // it would exclude it from the hull and shrink the warped crop past real
    // content. Never drop the two extreme lines on the stacking axis. (Sideways
    // background bleed is handled by the color filter and the duplicate splitter,
    // not here.)
    let lx = -ay
    let ly = ax
    let longit = centroids.map { $0.0 * lx + $0.1 * ly }
    if let minIdx = longit.indices.min(by: { longit[$0] < longit[$1] }),
        let maxIdx = longit.indices.max(by: { longit[$0] < longit[$1] }) {
        mask[minIdx] = true
        mask[maxIdx] = true
    }
    return mask
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
