import Foundation

#if os(macOS)
import CoreGraphics

/// Detects and splits the "two overlapping copies of the same receipt" case —
/// a customer copy laid on top of a merchant copy — which color and naive
/// geometry cannot separate (both are white paper).
///
/// Insight (deterministic): two offset copies of the same purchase have
/// DUPLICATE text lines related by a single RIGID transform — a rotation θ plus
/// a translation t (two copies tossed on a table are offset by translation AND a
/// small rotation). Every true duplicate pair a_i (copy A point) -> b_i (copy B
/// point) then satisfies b_i ≈ R_θ·a_i + t. We recover (θ, t) with a RANSAC over
/// the duplicate-line correspondences and count inliers; a single receipt with
/// internally repeated text ("TOTAL"/"SUBTOTAL", repeated SKUs) yields
/// INCONSISTENT correspondences and fails the consensus, so it is never split.
/// (A pure-translation consensus caps out under rotation because the
/// displacement vector b_i - a_i grows down the receipt, so real anchors at
/// opposite ends disagree; the rigid model removes that drift.) We only split on
/// >= `minConsensusPairs` rigid-consistent duplicate pairs, and only if both
/// resulting halves independently look like a receipt — otherwise the cluster is
/// left intact.
///
/// This runs on OCR text + geometry only (no pixels), is O(n^2) over a cluster's
/// lines, and is fully deterministic / unit-testable.

private let minConsensusPairs = 3
private let minTextSimilarity: CGFloat = 0.85
private let minTextLength = 4
/// Positional agreement tolerance (normalized image units). Used both as the
/// rigid-transform inlier residual threshold and for harmonic-family detection.
private let dispTolerance: CGFloat = 0.035
/// Maximum |θ| for a rigid two-copy model (radians, ≈15°). Two copies dropped on
/// a table have a small relative rotation; a larger estimated rotation is
/// spurious (e.g. a coincidental match between distant lines) and is rejected.
private let maxRotation: CGFloat = 15.0 * CGFloat.pi / 180.0
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

    // Pathological-explosion guard (bounds the O(P^2) consensus search below).
    // A long list of near-identical rows (e.g. 60 repeated SKU lines) yields
    // O(n^2) candidate pairs — C(60,2)=1770 — and the pairwise model search is
    // O(P^2) hypotheses x O(P) inliers = O(P^3), which would stall an OCR worker
    // for seconds. Rather than discard the decision entirely (a huge pair count
    // is USUALLY a periodic single receipt, but two copies of a long repeated
    // list can also overflow), deterministically keep the most INFORMATIVE
    // candidates — distinctive-text anchors first (they carry the real two-copy
    // signal, not repeated prices), then the widest displacements (spread) — and
    // run the normal consensus + guards on that bounded set. A periodic list's
    // pairs still trip the harmonic guard (-> .single); a genuine split's
    // distinctive anchors survive the cull (-> .split / .suspicious).
    let maxPairs = 300
    if pairs.count > maxPairs {
        pairs.sort { lhs, rhs in
            let ld = isDistinctiveAnchorText(texts[lhs.a]) ? 1 : 0
            let rd = isDistinctiveAnchorText(texts[rhs.a]) ? 1 : 0
            if ld != rd { return ld > rd }
            let lm = lhs.disp.dx * lhs.disp.dx + lhs.disp.dy * lhs.disp.dy
            let rm = rhs.disp.dx * rhs.disp.dx + rhs.disp.dy * rhs.disp.dy
            return lm > rm
        }
        pairs = Array(pairs.prefix(maxPairs))
    }

    // 2. Rigid-transform consensus (RANSAC). Each pair is a correspondence
    //    a_i (copy A point) -> b_i (copy B point). From any two correspondences
    //    estimate a rotation θ and translation t, reject models whose |θ| is too
    //    large to be two dropped copies, then count inlier correspondences k for
    //    which |R_θ·a_k + t - b_k| <= dispTolerance. The model with the most
    //    inliers wins, and those inliers are the anchor set. This subsumes the
    //    old pure-translation consensus (a periodic list still fits a θ≈0 model),
    //    but recovers the rotated two-copy case a translation-only model misses.
    let aPts = pairs.map { cents[$0.a] }
    let bPts = pairs.map { cents[$0.b] }
    var bestConsensus: [Pair] = []
    for i in 0..<pairs.count {
        for j in 0..<pairs.count where j != i {
            let vAx = aPts[j].x - aPts[i].x
            let vAy = aPts[j].y - aPts[i].y
            let vBx = bPts[j].x - bPts[i].x
            let vBy = bPts[j].y - bPts[i].y
            let magA = (vAx * vAx + vAy * vAy).squareRoot()
            let magB = (vBx * vBx + vBy * vBy).squareRoot()
            if magA < 1e-6 || magB < 1e-6 { continue }
            // θ = atan2(b_j - b_i) - atan2(a_j - a_i), wrapped to (-π, π].
            var theta = atan2(vBy, vBx) - atan2(vAy, vAx)
            while theta > CGFloat.pi { theta -= 2 * CGFloat.pi }
            while theta <= -CGFloat.pi { theta += 2 * CGFloat.pi }
            if abs(theta) > maxRotation { continue }
            let c = cos(theta), s = sin(theta)
            // t = b_i - R_θ·a_i.
            let tx = bPts[i].x - (c * aPts[i].x - s * aPts[i].y)
            let ty = bPts[i].y - (s * aPts[i].x + c * aPts[i].y)
            var inliers: [Pair] = []
            for k in 0..<pairs.count {
                let rx = c * aPts[k].x - s * aPts[k].y + tx
                let ry = s * aPts[k].x + c * aPts[k].y + ty
                let ex = rx - bPts[k].x
                let ey = ry - bPts[k].y
                if (ex * ex + ey * ey).squareRoot() <= dispTolerance {
                    inliers.append(pairs[k])
                }
            }
            if inliers.count > bestConsensus.count { bestConsensus = inliers }
        }
    }
    // Not even two duplicate anchors fit one rigid transform — single receipt.
    guard bestConsensus.count >= 2 else { return .single }

    // Representative translation of the winning model (median inlier
    // displacement). With the small rotations seen between two copies this is
    // close to t, and for a periodic list (θ≈0) it is the single pitch — which
    // keeps the harmonic guard below working unchanged.
    let bestVec = CGVector(
        dx: median(bestConsensus.map { $0.disp.dx }),
        dy: median(bestConsensus.map { $0.disp.dy })
    )
    let dUnit = normalize(bestVec)

    // --- FIX 1 guards: reject a false split of a SINGLE receipt whose
    // internally repeated rows (6x "TACO 3.50", identical SKUs) forge an
    // evenly spaced, single-pitch "consensus". A genuine pair of overlapping
    // copies has exactly ONE rigid displacement, anchored by DISTINCTIVE text
    // SCATTERED across the whole receipt; a periodic item list does not. All
    // three guards must pass before we may split. ---
    //
    // The distinctive-anchor and anchor-spread predicates are hoisted here so
    // the sub-threshold (2-inlier) review flag can be gated on the SAME quality
    // bar as the confident-split path (see the minConsensusPairs branch below).

    // Distinctive anchors: at least two consensus pairs must be anchored by real
    // alphabetic text, NOT a pure price / quantity / money token ("3.50",
    // "$5.00", "2 X", "#4"). Repeated SKUs and prices do not count toward this.
    let distinctiveAnchors = bestConsensus.filter {
        isDistinctiveAnchorText(texts[$0.a])
    }.count
    let anchorsDistinctive = distinctiveAnchors >= 2

    // Anchor spatial spread: the consensus anchors must be scattered across the
    // receipt's long axis (the displacement direction, ~vertical for stacked
    // copies), not locally consecutive. Require both the "A" and "B" members to
    // span >= 40% of the whole cluster's extent on that axis.
    let projA = bestConsensus.map { proj(cents[$0.a], dUnit) }
    let projB = bestConsensus.map { proj(cents[$0.b], dUnit) }
    let clusterProj = cents.map { proj($0, dUnit) }
    let clusterExtent = (clusterProj.max() ?? 0) - (clusterProj.min() ?? 0)
    let spanA = (projA.max() ?? 0) - (projA.min() ?? 0)
    let spanB = (projB.max() ?? 0) - (projB.min() ?? 0)
    let minSpread: CGFloat = 0.40 * clusterExtent
    let anchorsSpread =
        clusterExtent > 1e-6 && spanA >= minSpread && spanB >= minSpread

    // Some duplicate-anchor evidence but below the confident-split threshold
    // (e.g. a faded second copy yields only 2 clean anchors). Flag for review
    // ONLY when those anchors are themselves a plausible two-copy signal, held
    // to the SAME distinctive-anchor / anchor-spread guards the split path uses
    // and with the split path's OWN outcomes:
    //   - non-distinctive anchors (a single clean receipt's repeated money/qty
    //     tokens, "$20.00"/"TOTAL", which form rigid-consistent but meaningless
    //     correspondences) -> .single, exactly as the split path's distinctive
    //     guard returns .single;
    //   - distinctive anchors that are merely bunched (not spread) -> the split
    //     path already downgrades that from a split to .suspicious, so a 2-inlier
    //     distinctive consensus is at most .suspicious here too.
    // This removes the old blanket needs_review on any 2-inlier consensus (which
    // fired on every receipt that repeats a price) while keeping genuine
    // distinctive partial-duplicate evidence flagged. Never split on this.
    guard bestConsensus.count >= minConsensusPairs else {
        guard anchorsDistinctive else { return .single }
        return .suspicious(anchorPairs: bestConsensus.count)
    }

    // Guard 3 (cheapest disqualifier, checked first): reject a
    // harmonic/periodic displacement family. Evenly spaced repeats yield not
    // only a 1-pitch consensus but also a 2-pitch (3-pitch, ...) family. Two
    // real copies have exactly ONE displacement and no ~2x harmonic. If a
    // second consensus vector (>= 2 agreeing pairs) sits at ~2x the best
    // vector, this is a list, not two copies.
    let twoVec = CGVector(dx: 2 * bestVec.dx, dy: 2 * bestVec.dy)
    let harmonicCount = pairs.filter {
        let ex = $0.disp.dx - twoVec.dx
        let ey = $0.disp.dy - twoVec.dy
        return (ex * ex + ey * ey).squareRoot() <= 2 * dispTolerance
    }.count
    if harmonicCount >= 2 {
        return .single
    }

    // Guard 2: distinctive anchors (computed above).
    guard anchorsDistinctive else {
        // Only prices / quantities line up: no evidence of duplicated content.
        return .single
    }

    // Guard 1: anchor spatial spread (computed above).
    guard anchorsSpread else {
        // Distinctive, non-harmonic anchors exist but are bunched together, not
        // clearly two copies. Some evidence: flag for review, but do not split.
        return .suspicious(anchorPairs: bestConsensus.count)
    }

    // 3. Seeded assignment: anchors A (tail copy) and B (head copy) define the
    //    boundary; assign every line by which side of it (along the displacement)
    //    it sits. When the two copies are DISJOINT along the axis (max A-anchor
    //    below min B-anchor) place the boundary in that gap, so every anchor
    //    straddles it. Only when they interleave (overlapping copies) fall back
    //    to the mean — where the straddle check below will (correctly) reject the
    //    split as unseparable. Using the mean unconditionally would misplace the
    //    boundary for a clean but asymmetric stacking and reject a valid split.
    let maxProjA = projA.max() ?? 0
    let minProjB = projB.min() ?? 0
    let boundary =
        maxProjA < minProjB
        ? (maxProjA + minProjB) / 2.0
        : (mean(projA) + mean(projB)) / 2.0

    // The boundary can only separate two copies that are largely DISJOINT along
    // the displacement axis (one copy stacked below the other). If the copies
    // overlap so heavily that the midpoint slices through both, a positional
    // split scatters each copy's lines across both halves, yielding two mixed
    // partial crops rather than the two copies. Detect that: for a clean stacked
    // pair every anchor's A-member (tail copy) sits below the boundary and its
    // B-member (head copy) above it. If the anchor correspondences don't cleanly
    // straddle the boundary, don't split — flag for review.
    let straddling = zip(projA, projB).filter {
        $0.0 < boundary && $0.1 >= boundary
    }.count
    guard CGFloat(straddling) >= 0.75 * CGFloat(bestConsensus.count) else {
        return .suspicious(anchorPairs: bestConsensus.count)
    }

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

/// True if `t` (already normalized / uppercased) carries real alphabetic
/// content and is NOT a pure price / quantity / money token. The money alphabet
/// is digits, spaces, '.', ',', '$', 'X'/'x' (a quantity multiplier), and '#'.
/// Repeated SKUs and prices ("3.50", "$5.00", "2 X", "#4") therefore do not
/// qualify as distinctive anchors, while item names ("TACO 3.50") do.
func isDistinctiveAnchorText(_ t: String) -> Bool {
    var hasLetter = false
    var hasNonMoney = false
    for c in t.unicodeScalars {
        if c.value >= 65 && c.value <= 90 { hasLetter = true }  // A-Z (uppercased)
        switch c {
        case "0"..."9", " ", ".", ",", "$", "X", "x", "#":
            continue
        default:
            hasNonMoney = true
        }
    }
    return hasLetter && hasNonMoney
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
