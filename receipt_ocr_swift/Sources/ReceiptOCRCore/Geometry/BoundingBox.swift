import Foundation

#if os(macOS)
import CoreGraphics

// MARK: - Minimum Area Rectangle

/// Find the minimum-area bounding rectangle that encloses all points.
///
/// Uses the rotating calipers algorithm on the convex hull to test each
/// hull edge as a potential rectangle side, finding the orientation that
/// minimizes the bounding area.
///
/// - Parameter points: Array of (x, y) coordinate tuples
/// - Returns: Tuple of (center, size, angleDegrees) where:
///   - center: (cx, cy) center of the rectangle
///   - size: (width, height) dimensions
///   - angleDeg: rotation angle in degrees
public func minAreaRect(points: [(CGFloat, CGFloat)]) -> (center: (CGFloat, CGFloat), size: (CGFloat, CGFloat), angleDeg: CGFloat) {
    // Handle degenerate cases
    guard !points.isEmpty else {
        return ((0, 0), (0, 0), 0)
    }
    guard points.count > 1 else {
        return (points[0], (0, 0), 0)
    }

    let hull = convexHull(points: points)

    // Handle degenerate hull (line segment or single point)
    guard hull.count >= 3 else {
        if hull.count == 2 {
            let p0 = hull[0]
            let p1 = hull[1]
            let center = ((p0.0 + p1.0) / 2.0, (p0.1 + p1.1) / 2.0)
            let dist = sqrt(pow(p1.0 - p0.0, 2) + pow(p1.1 - p0.1, 2))
            return (center, (dist, 0), 0)
        }
        return (hull.isEmpty ? (0, 0) : hull[0], (0, 0), 0)
    }

    var minArea = CGFloat.infinity
    var bestRect: (center: (CGFloat, CGFloat), size: (CGFloat, CGFloat), angleDeg: CGFloat) = ((0, 0), (0, 0), 0)

    // Test each edge of the hull as a potential rectangle side
    for i in 0..<hull.count {
        let p1 = hull[i]
        let p2 = hull[(i + 1) % hull.count]

        // Angle to rotate hull so this edge becomes horizontal
        let theta = -atan2(p2.1 - p1.1, p2.0 - p1.0)
        let cosT = cos(theta)
        let sinT = sin(theta)

        // Rotate all hull points
        let rotated = hull.map { p -> (CGFloat, CGFloat) in
            (cosT * p.0 - sinT * p.1, sinT * p.0 + cosT * p.1)
        }

        // Find axis-aligned bounding box in rotated space
        let minX = rotated.map { $0.0 }.min()!
        let maxX = rotated.map { $0.0 }.max()!
        let minY = rotated.map { $0.1 }.min()!
        let maxY = rotated.map { $0.1 }.max()!

        let width = maxX - minX
        let height = maxY - minY
        let area = width * height

        if area < minArea {
            minArea = area

            // Center in rotated space
            let cxR = minX + width / 2.0
            let cyR = minY + height / 2.0

            // Rotate center back to original space
            let cx = cosT * cxR + sinT * cyR
            let cy = -sinT * cxR + cosT * cyR

            // Convert angle to degrees
            let angleDeg = -theta * 180.0 / .pi

            bestRect = ((cx, cy), (width, height), angleDeg)
        }
    }

    return bestRect
}

// MARK: - Box Points

/// Compute the 4 corner coordinates of a rotated rectangle.
///
/// Given the center, size, and rotation angle, calculates the positions
/// of all four corners in the original coordinate space.
///
/// - Parameters:
///   - center: (cx, cy) center of the rectangle
///   - size: (width, height) dimensions
///   - angleDeg: rotation angle in degrees
/// - Returns: Array of 4 (x, y) tuples in order: bottom-left, bottom-right, top-right, top-left
public func boxPoints(center: (CGFloat, CGFloat), size: (CGFloat, CGFloat), angleDeg: CGFloat) -> [(CGFloat, CGFloat)] {
    let angleRad = angleDeg * .pi / 180.0
    let cosA = cos(angleRad)
    let sinA = sin(angleRad)

    let hw = size.0 / 2.0  // half width
    let hh = size.1 / 2.0  // half height

    // Local corners relative to center (before rotation)
    // Order: bottom-left, bottom-right, top-right, top-left
    let localCorners: [(CGFloat, CGFloat)] = [
        (-hw, -hh),
        (hw, -hh),
        (hw, hh),
        (-hw, hh),
    ]

    // Rotate and translate each corner
    return localCorners.map { (lx, ly) in
        let rx = lx * cosA - ly * sinA
        let ry = lx * sinA + ly * cosA
        return (center.0 + rx, center.1 + ry)
    }
}

// MARK: - Reorder Box Points

/// Normalize 4 corner points to consistent order: [top-left, top-right, bottom-right, bottom-left].
///
/// Sorts points by Y coordinate to identify top vs bottom, then by X coordinate
/// within each pair to identify left vs right.
///
/// - Parameter pts: Array of exactly 4 (x, y) tuples in any order
/// - Returns: Array of 4 points in order [TL, TR, BR, BL]
public func reorderBoxPoints(_ pts: [(CGFloat, CGFloat)]) -> [(CGFloat, CGFloat)] {
    guard pts.count == 4 else {
        return pts
    }

    // Sort by Y first, then by X (for tie-breaking)
    // Lower Y values = top (in image coordinates where Y=0 is at top)
    let sorted = pts.sorted { a, b in
        if a.1 != b.1 {
            return a.1 < b.1  // Sort by Y ascending (top first)
        }
        return a.0 < b.0  // Then by X ascending
    }

    // First two are "top" (lowest Y), last two are "bottom" (highest Y)
    let topPair = Array(sorted[0..<2])
    let bottomPair = Array(sorted[2..<4])

    // Within each pair, sort by X to get left/right
    let topSorted = topPair.sorted { $0.0 < $1.0 }
    let bottomSorted = bottomPair.sorted { $0.0 < $1.0 }

    let topLeft = topSorted[0]
    let topRight = topSorted[1]
    let bottomLeft = bottomSorted[0]
    let bottomRight = bottomSorted[1]

    return [topLeft, topRight, bottomRight, bottomLeft]
}

#endif
