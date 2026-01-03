import Foundation

#if os(macOS)
import CoreGraphics

// MARK: - Circular Mean Angle

/// Compute the circular mean of two angles, handling wraparound at ±π.
///
/// For example, averaging +179° and -179° gives ±180° instead of 0°.
/// This is essential for averaging line angles that may cross the ±180° boundary.
///
/// - Parameters:
///   - angle1: First angle in radians
///   - angle2: Second angle in radians
/// - Returns: Mean angle in radians
public func circularMeanAngle(_ angle1: CGFloat, _ angle2: CGFloat) -> CGFloat {
    let sinSum = sin(angle1) + sin(angle2)
    let cosSum = cos(angle1) + cos(angle2)
    return atan2(sinSum, cosSum)
}

// MARK: - Line Intersection

/// Find the intersection point of two lines defined by point + direction vectors.
///
/// Each line is defined parametrically as P(t) = point + t * direction.
/// Returns nil if the lines are parallel (or nearly parallel).
///
/// - Parameters:
///   - p1: A point on the first line
///   - d1: Direction vector of the first line
///   - p2: A point on the second line
///   - d2: Direction vector of the second line
/// - Returns: The intersection point, or nil if lines are parallel
public func lineIntersection(
    p1: (CGFloat, CGFloat),
    d1: (CGFloat, CGFloat),
    p2: (CGFloat, CGFloat),
    d2: (CGFloat, CGFloat)
) -> (CGFloat, CGFloat)? {
    // Cross product of direction vectors
    let cross = d1.0 * d2.1 - d1.1 * d2.0

    // Check for parallel lines (near-zero cross product)
    if abs(cross) < 1e-9 {
        return nil
    }

    // Solve for parameter t: p1 + t*d1 = intersection point
    let dp = (p2.0 - p1.0, p2.1 - p1.1)
    let t = (dp.0 * d2.1 - dp.1 * d2.0) / cross

    return (p1.0 + t * d1.0, p1.1 + t * d1.1)
}

// MARK: - Rotated Bounding Box Corners

/// Compute receipt corners using the rotated bounding box approach.
///
/// This algorithm derives the receipt quadrilateral by:
/// 1. Computing the average angle of top/bottom text line edges (using circular mean)
/// 2. Finding left/right hull extremes along the perpendicular axis
/// 3. Intersecting boundary lines to get the 4 corners
///
/// The result is a perspective-aware quadrilateral that follows the natural tilt of the receipt.
///
/// - Parameters:
///   - hull: Convex hull points of all word corners
///   - topLineCorners: Corners from the top text line [TL, TR, BL, BR]
///   - bottomLineCorners: Corners from the bottom text line [TL, TR, BL, BR]
/// - Returns: Receipt corners as [top_left, top_right, bottom_right, bottom_left], or empty if invalid
public func computeRotatedBoundingBoxCorners(
    hull: [(CGFloat, CGFloat)],
    topLineCorners: [(CGFloat, CGFloat)],
    bottomLineCorners: [(CGFloat, CGFloat)]
) -> [(CGFloat, CGFloat)] {
    // Validate inputs
    guard hull.count >= 3,
          topLineCorners.count >= 4,
          bottomLineCorners.count >= 4 else {
        return []
    }

    // Extract key points from line corners
    // Corner ordering: [TL, TR, BL, BR]
    let topLeftPt = topLineCorners[0]    // Top-left of top line
    let topRightPt = topLineCorners[1]   // Top-right of top line
    let bottomLeftPt = bottomLineCorners[2]   // Bottom-left of bottom line
    let bottomRightPt = bottomLineCorners[3]  // Bottom-right of bottom line

    // Compute angle of top edge
    let topDx = topRightPt.0 - topLeftPt.0
    let topDy = topRightPt.1 - topLeftPt.1
    let topAngle = atan2(topDy, topDx)

    // Compute angle of bottom edge
    let bottomDx = bottomRightPt.0 - bottomLeftPt.0
    let bottomDy = bottomRightPt.1 - bottomLeftPt.1
    let bottomAngle = atan2(bottomDy, bottomDx)

    // Average angle using circular mean (handles wraparound at ±π)
    let avgAngle = circularMeanAngle(topAngle, bottomAngle)

    // Left/right edge direction: perpendicular to horizontal edges
    let leftEdgeAngle = avgAngle + .pi / 2.0
    let leftDx = cos(leftEdgeAngle)
    let leftDy = sin(leftEdgeAngle)

    // Direction vectors for edges
    let topDir = (topDx, topDy)
    let bottomDir = (bottomDx, bottomDy)
    let leftDir = (leftDx, leftDy)

    // Compute hull centroid
    let centroid = computeHullCentroid(hull: hull)

    // Horizontal direction (along receipt tilt)
    let horizDir = (cos(avgAngle), sin(avgAngle))

    // Project hull points onto horizontal axis to find left/right extremes
    var perpProjections: [(proj: CGFloat, point: (CGFloat, CGFloat))] = []
    for p in hull {
        let rel = (p.0 - centroid.0, p.1 - centroid.1)
        let proj = rel.0 * horizDir.0 + rel.1 * horizDir.1
        perpProjections.append((proj, p))
    }

    perpProjections.sort { $0.proj < $1.proj }

    guard let leftmostHullPt = perpProjections.first?.point,
          let rightmostHullPt = perpProjections.last?.point else {
        return []
    }

    // Compute final corners by intersecting edges
    let topLeft = lineIntersection(p1: topLeftPt, d1: topDir, p2: leftmostHullPt, d2: leftDir)
    let topRight = lineIntersection(p1: topLeftPt, d1: topDir, p2: rightmostHullPt, d2: leftDir)
    let bottomLeft = lineIntersection(p1: bottomLeftPt, d1: bottomDir, p2: leftmostHullPt, d2: leftDir)
    let bottomRight = lineIntersection(p1: bottomLeftPt, d1: bottomDir, p2: rightmostHullPt, d2: leftDir)

    // If any intersection failed (parallel lines), fall back to axis-aligned bounds
    guard let tl = topLeft, let tr = topRight, let bl = bottomLeft, let br = bottomRight else {
        let hullXs = hull.map { $0.0 }
        let hullYs = hull.map { $0.1 }
        guard let minX = hullXs.min(),
              let maxX = hullXs.max(),
              let minY = hullYs.min(),
              let maxY = hullYs.max() else {
            return []
        }
        // Return axis-aligned bounding box
        // In normalized coords: higher Y = top of image
        return [
            (minX, maxY),  // top-left
            (maxX, maxY),  // top-right
            (maxX, minY),  // bottom-right
            (minX, minY),  // bottom-left
        ]
    }

    return [tl, tr, br, bl]
}

#endif
