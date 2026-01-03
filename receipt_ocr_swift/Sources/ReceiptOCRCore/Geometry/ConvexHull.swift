import Foundation

#if os(macOS)
import CoreGraphics

// MARK: - Convex Hull

/// Compute the convex hull of a set of points using the monotone chain algorithm.
///
/// Returns points in counter-clockwise (CCW) order forming the outer boundary.
///
/// - Parameter points: Array of (x, y) coordinate tuples
/// - Returns: Array of points describing the convex hull in CCW order
public func convexHull(points: [(CGFloat, CGFloat)]) -> [(CGFloat, CGFloat)] {
    // Remove duplicates by converting to set of strings and back
    var uniqueSet = Set<String>()
    var uniquePoints: [(CGFloat, CGFloat)] = []
    for p in points {
        let key = "\(p.0),\(p.1)"
        if !uniqueSet.contains(key) {
            uniqueSet.insert(key)
            uniquePoints.append(p)
        }
    }

    // Sort by x, then by y
    let sorted = uniquePoints.sorted {
        $0.0 == $1.0 ? $0.1 < $1.1 : $0.0 < $1.0
    }

    if sorted.count <= 1 {
        return sorted
    }

    /// Cross product of vectors OA and OB where O is origin point
    /// Returns positive if counter-clockwise, negative if clockwise, 0 if collinear
    func cross(_ o: (CGFloat, CGFloat), _ a: (CGFloat, CGFloat), _ b: (CGFloat, CGFloat)) -> CGFloat {
        return (a.0 - o.0) * (b.1 - o.1) - (a.1 - o.1) * (b.0 - o.0)
    }

    // Build lower hull (left to right)
    var lower: [(CGFloat, CGFloat)] = []
    for p in sorted {
        while lower.count >= 2 && cross(lower[lower.count - 2], lower[lower.count - 1], p) <= 0 {
            lower.removeLast()
        }
        lower.append(p)
    }

    // Build upper hull (right to left)
    var upper: [(CGFloat, CGFloat)] = []
    for p in sorted.reversed() {
        while upper.count >= 2 && cross(upper[upper.count - 2], upper[upper.count - 1], p) <= 0 {
            upper.removeLast()
        }
        upper.append(p)
    }

    // Remove last point of each half (it's repeated at the junction)
    lower.removeLast()
    upper.removeLast()

    return lower + upper
}

// MARK: - Hull Centroid

/// Compute the centroid (geometric center) of a polygon using the shoelace formula.
///
/// The hull must be provided in counter-clockwise order for correct results.
/// Handles degenerate cases: empty hull, single point, two points, and near-zero area polygons.
///
/// - Parameter hull: Array of (x, y) points in CCW order
/// - Returns: The centroid as (x, y) tuple
public func computeHullCentroid(hull: [(CGFloat, CGFloat)]) -> (CGFloat, CGFloat) {
    let n = hull.count

    // Handle degenerate cases
    if n == 0 {
        return (0.0, 0.0)
    }
    if n == 1 {
        return hull[0]
    }
    if n == 2 {
        return ((hull[0].0 + hull[1].0) / 2.0, (hull[0].1 + hull[1].1) / 2.0)
    }

    // Shoelace formula for polygon centroid
    var areaSum: CGFloat = 0.0
    var cx: CGFloat = 0.0
    var cy: CGFloat = 0.0

    for i in 0..<n {
        let p0 = hull[i]
        let p1 = hull[(i + 1) % n]
        let crossVal = p0.0 * p1.1 - p1.0 * p0.1
        areaSum += crossVal
        cx += (p0.0 + p1.0) * crossVal
        cy += (p0.1 + p1.1) * crossVal
    }

    let area = areaSum / 2.0

    // Fallback to simple average if polygon has near-zero area (degenerate case)
    if abs(area) < 1e-14 {
        let xAvg = hull.reduce(0.0) { $0 + $1.0 } / CGFloat(n)
        let yAvg = hull.reduce(0.0) { $0 + $1.1 } / CGFloat(n)
        return (xAvg, yAvg)
    }

    cx /= (6.0 * area)
    cy /= (6.0 * area)

    return (cx, cy)
}

#endif
