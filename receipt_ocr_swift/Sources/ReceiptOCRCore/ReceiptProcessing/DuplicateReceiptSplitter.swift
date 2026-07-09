import Foundation

#if os(macOS)
import CoreGraphics

/// Detects and splits the "two overlapping copies of the same receipt" case —
/// a customer copy laid on top of a merchant copy — which color and naive
/// geometry cannot separate (both are white paper).
///
/// Insight (deterministic): two offset copies of the same purchase have
/// DUPLICATE text lines, and every true duplicate pair shares approximately the
/// SAME displacement vector (a rigid translation). A single receipt with
/// internally repeated text ("TOTAL"/"SUBTOTAL", repeated SKUs) yields
/// INCONSISTENT displacement vectors and fails the consensus, so it is never
/// split. We only split on >= `minConsensusPairs` displacement-consistent
/// duplicate pairs, and only if both resulting halves independently look like a
/// receipt — otherwise the cluster is left intact.
///
/// This runs on OCR text + geometry only (no pixels), is O(n^2) over a cluster's
/// lines, and is fully deterministic / unit-testable.

private let minConsensusPairs = 3
private let minTextSimilarity: CGFloat = 0.85
private let minTextLength = 4
/// Displacement-vector agreement tolerance (normalized image units).
private let dispTolerance: CGFloat = 0.035
/// Each split half must keep at least this many lines AND this fraction.
private let minHalfLines = 5
private let minHalfFraction: CGFloat = 0.20

/// Outcome of examining one cluster for duplicate-copy structure.
enum DuplicateOutcome {
    /// Confidently two overlapping copies — split into these two groups.
    case split([Int], [Int])
    /// Probably multiple receipts (some duplicate-anchor evidence) but below the
    /// confident-split threshold — do not split, flag for review.
    case suspicious(anchorPairs: Int)
    /// A single receipt.
    case single
}

/// Returns a new clustering in which any cluster confidently identified as two
/// overlapping copies of one receipt is split into two clusters, plus the set of
/// cluster IDs that look like multiple receipts but could not be split
/// confidently (flag these for review). All other clusters pass through
/// unchanged.
public func splitDuplicateReceiptClusters(
    _ clustering: ClusteringResult,
    lines: [Line]
) -> (result: ClusteringResult, needsReview: [Int]) {
    var result: [Int: [Int]] = [:]
    var needsReview: [Int] = []
    var nextId = (clustering.clusters.keys.filter { $0 != -1 }.max() ?? 0) + 1

    for (clusterId, indices) in clustering.clusters {
        if clusterId == -1 {
            result[clusterId] = indices
            continue
        }
        let valid = indices.filter { $0 >= 0 && $0 < lines.count }
        guard valid.count >= 2 * minHalfLines else {
            result[clusterId] = indices
            continue
        }
        switch examineDuplicatePair(valid, lines: lines) {
        case .split(let groupA, let groupB):
            result[clusterId] = groupA
            result[nextId] = groupB
            nextId += 1
        case .suspicious:
            result[clusterId] = indices
            needsReview.append(clusterId)
        case .single:
            result[clusterId] = indices
        }
    }
    return (ClusteringResult(clusters: result), needsReview)
}

/// Core detector. Confidently splits two overlapping copies, flags a suspicious
/// (probable-multiple) cluster, or reports a single receipt.
func examineDuplicatePair(
    _ indices: [Int],
    lines: [Line]
) -> DuplicateOutcome {
    let texts = indices.map { normalizedText(lines[$0].text) }
    let cents = indices.map { centroid(lines[$0]) }
    let heights = indices.map { lineHeight(lines[$0]) }

    // 1. Candidate duplicate pairs: similar text, disjoint in space.
    struct Pair { let a: Int; let b: Int; let disp: CGVector }
    var pairs: [Pair] = []
    for i in 0..<indices.count {
        if texts[i].count < minTextLength { continue }
        for j in (i + 1)..<indices.count {
            if texts[j].count < minTextLength { continue }
            if similarity(texts[i], texts[j]) < minTextSimilarity { continue }
            let dx = cents[j].x - cents[i].x
            let dy = cents[j].y - cents[i].y
            let sep = (dx * dx + dy * dy).squareRoot()
            let hRef = max(heights[i], heights[j])
            if sep < 3.0 * hRef { continue }  // must be spatially disjoint
            // Canonicalize direction so mirror pairs agree.
            var vdx = dx, vdy = dy, ai = i, bj = j
            if vdy < 0 || (vdy == 0 && vdx < 0) {
                vdx = -vdx; vdy = -vdy; ai = j; bj = i
            }
            pairs.append(Pair(a: ai, b: bj, disp: CGVector(dx: vdx, dy: vdy)))
        }
    }
    guard pairs.count >= 2 else { return .single }

    // 2. Displacement consensus: the vector most pairs agree on.
    var bestConsensus: [Pair] = []
    for candidate in pairs {
        let agree = pairs.filter {
            let ex = $0.disp.dx - candidate.disp.dx
            let ey = $0.disp.dy - candidate.disp.dy
            return (ex * ex + ey * ey).squareRoot() <= dispTolerance
        }
        if agree.count > bestConsensus.count { bestConsensus = agree }
    }
    // Not even two duplicate anchors agree on a displacement — single receipt.
    guard bestConsensus.count >= 2 else { return .single }

    // Some duplicate-anchor evidence but below the confident-split threshold
    // (e.g. a faded second copy yields only 2 clean anchors, or rotation makes
    // a pure translation cap out). Flag for review; never split on this.
    guard bestConsensus.count >= minConsensusPairs else {
        return .suspicious(anchorPairs: bestConsensus.count)
    }

    // Consensus displacement (median of agreeing vectors).
    let dUnit = normalize(
        CGVector(
            dx: median(bestConsensus.map { $0.disp.dx }),
            dy: median(bestConsensus.map { $0.disp.dy })
        )
    )

    // 3. Seeded assignment: anchors A (tail) and B (head) define the boundary;
    //    assign every line by which side of it (along the displacement) it sits.
    let projA = bestConsensus.map { proj(cents[$0.a], dUnit) }
    let projB = bestConsensus.map { proj(cents[$0.b], dUnit) }
    let boundary = (mean(projA) + mean(projB)) / 2.0

    var groupA: [Int] = []
    var groupB: [Int] = []
    for (k, idx) in indices.enumerated() {
        if proj(cents[k], dUnit) < boundary {
            groupA.append(idx)
        } else {
            groupB.append(idx)
        }
    }

    // 4. Validate: each half must independently look like a receipt.
    let total = indices.count
    let okA =
        groupA.count >= minHalfLines
        && CGFloat(groupA.count) >= minHalfFraction * CGFloat(total)
    let okB =
        groupB.count >= minHalfLines
        && CGFloat(groupB.count) >= minHalfFraction * CGFloat(total)
    guard okA && okB else {
        return .suspicious(anchorPairs: bestConsensus.count)
    }

    return .split(groupA, groupB)
}

// MARK: - Helpers

func normalizedText(_ s: String) -> String {
    let upper = s.uppercased()
    let collapsed = upper.split(whereSeparator: { $0 == " " || $0 == "\t" })
        .joined(separator: " ")
    return collapsed.trimmingCharacters(in: .whitespaces)
}

/// Normalized Levenshtein similarity in [0, 1].
func similarity(_ a: String, _ b: String) -> CGFloat {
    let s = Array(a), t = Array(b)
    if s.isEmpty && t.isEmpty { return 1 }
    let n = s.count, m = t.count
    if n == 0 || m == 0 { return 0 }
    // Quick length-ratio prefilter.
    let lenRatio = CGFloat(min(n, m)) / CGFloat(max(n, m))
    if lenRatio < 0.6 { return 0 }
    var prev = Array(0...m)
    var curr = [Int](repeating: 0, count: m + 1)
    for i in 1...n {
        curr[0] = i
        for j in 1...m {
            let cost = s[i - 1] == t[j - 1] ? 0 : 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        }
        swap(&prev, &curr)
    }
    let dist = prev[m]
    return 1.0 - CGFloat(dist) / CGFloat(max(n, m))
}

func centroid(_ line: Line) -> CGPoint {
    let x =
        (line.topLeft.x + line.topRight.x + line.bottomLeft.x
            + line.bottomRight.x) / 4.0
    let y =
        (line.topLeft.y + line.topRight.y + line.bottomLeft.y
            + line.bottomRight.y) / 4.0
    return CGPoint(x: x, y: y)
}

func lineHeight(_ line: Line) -> CGFloat {
    let leftH = abs(line.topLeft.y - line.bottomLeft.y)
    let rightH = abs(line.topRight.y - line.bottomRight.y)
    return max((leftH + rightH) / 2.0, 1e-4)
}

private func normalize(_ v: CGVector) -> CGVector {
    let mag = (v.dx * v.dx + v.dy * v.dy).squareRoot()
    guard mag > 1e-9 else { return CGVector(dx: 1, dy: 0) }
    return CGVector(dx: v.dx / mag, dy: v.dy / mag)
}

private func proj(_ p: CGPoint, _ unit: CGVector) -> CGFloat {
    return p.x * unit.dx + p.y * unit.dy
}

private func mean(_ values: [CGFloat]) -> CGFloat {
    guard !values.isEmpty else { return 0 }
    return values.reduce(0, +) / CGFloat(values.count)
}
#endif
