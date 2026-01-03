import Foundation

#if os(macOS)
import CoreGraphics

// MARK: - Cluster Merging

/// Merge clusters whose bounding boxes have significant overlap.
///
/// Uses IoU (Intersection over Union) to determine if clusters should be merged.
/// This is primarily used for SCAN images where separate DBSCAN clusters may
/// actually belong to the same receipt.
///
/// - Parameters:
///   - clusters: Dictionary mapping cluster ID to array of line indices
///   - lines: Array of Line objects
///   - imageWidth: Image width in pixels (for coordinate conversion)
///   - imageHeight: Image height in pixels (for coordinate conversion)
///   - iouThreshold: Minimum IoU for clusters to be merged (default 0.01)
/// - Returns: Merged clusters dictionary
public func joinOverlappingClusters(
    clusters: [Int: [Int]],
    lines: [Line],
    imageWidth: CGFloat,
    imageHeight: CGFloat,
    iouThreshold: CGFloat = 0.01
) -> [Int: [Int]] {
    // Filter out noise cluster (-1)
    let validClusterIds = clusters.keys.filter { $0 != -1 }.sorted()

    guard validClusterIds.count > 1 else {
        // Nothing to merge if 0 or 1 valid clusters
        return clusters.filter { $0.key != -1 }
    }

    // Compute bounding box for each cluster
    var clusterBoxes: [Int: [(CGFloat, CGFloat)]] = [:]

    for clusterId in validClusterIds {
        guard let lineIndices = clusters[clusterId] else { continue }

        // Collect all corner points from lines in this cluster
        var allCorners: [(CGFloat, CGFloat)] = []
        for idx in lineIndices {
            guard idx >= 0 && idx < lines.count else { continue }
            let line = lines[idx]

            // Convert normalized coords to absolute and flip Y
            let corners = [
                (line.topLeft.x * imageWidth, (1 - line.topLeft.y) * imageHeight),
                (line.topRight.x * imageWidth, (1 - line.topRight.y) * imageHeight),
                (line.bottomLeft.x * imageWidth, (1 - line.bottomLeft.y) * imageHeight),
                (line.bottomRight.x * imageWidth, (1 - line.bottomRight.y) * imageHeight),
            ]
            allCorners.append(contentsOf: corners)
        }

        guard !allCorners.isEmpty else { continue }

        // Compute minimum area bounding rectangle
        let (center, size, angleDeg) = minAreaRect(points: allCorners)
        let corners = boxPoints(center: center, size: size, angleDeg: angleDeg)
        let orderedCorners = reorderBoxPoints(corners)

        clusterBoxes[clusterId] = orderedCorners
    }

    // Union-Find structure for cluster merging
    var parent: [Int: Int] = [:]
    var rank: [Int: Int] = [:]

    for id in validClusterIds {
        parent[id] = id
        rank[id] = 0
    }

    func find(_ x: Int) -> Int {
        if parent[x] != x {
            parent[x] = find(parent[x]!)  // Path compression
        }
        return parent[x]!
    }

    func union(_ x: Int, _ y: Int) {
        let rootX = find(x)
        let rootY = find(y)
        if rootX != rootY {
            // Union by rank
            if rank[rootX]! < rank[rootY]! {
                parent[rootX] = rootY
            } else if rank[rootX]! > rank[rootY]! {
                parent[rootY] = rootX
            } else {
                parent[rootY] = rootX
                rank[rootX]! += 1
            }
        }
    }

    // Compare all pairs of clusters
    for i in 0..<validClusterIds.count {
        for j in (i + 1)..<validClusterIds.count {
            let idA = validClusterIds[i]
            let idB = validClusterIds[j]

            guard let boxA = clusterBoxes[idA],
                  let boxB = clusterBoxes[idB] else { continue }

            let iou = computeIoU(boxA: boxA, boxB: boxB)
            if iou > iouThreshold {
                union(idA, idB)
            }
        }
    }

    // Group clusters by their root
    var mergedClusters: [Int: [Int]] = [:]
    for clusterId in validClusterIds {
        let root = find(clusterId)
        if mergedClusters[root] == nil {
            mergedClusters[root] = []
        }
        if let lineIndices = clusters[clusterId] {
            mergedClusters[root]!.append(contentsOf: lineIndices)
        }
    }

    return mergedClusters
}

// MARK: - IoU Calculation

/// Compute Intersection over Union (IoU) for two quadrilateral boxes.
///
/// - Parameters:
///   - boxA: First box as 4 corners [TL, TR, BR, BL]
///   - boxB: Second box as 4 corners [TL, TR, BR, BL]
/// - Returns: IoU value between 0 and 1
internal func computeIoU(boxA: [(CGFloat, CGFloat)], boxB: [(CGFloat, CGFloat)]) -> CGFloat {
    // Compute intersection polygon using Sutherland-Hodgman clipping
    let intersection = polygonClip(subject: boxA, clip: boxB)

    guard intersection.count >= 3 else {
        return 0  // No intersection
    }

    let intersectionArea = polygonArea(polygon: intersection)
    let areaA = polygonArea(polygon: boxA)
    let areaB = polygonArea(polygon: boxB)

    let unionArea = areaA + areaB - intersectionArea

    if unionArea < 1e-9 {
        return 0
    }

    return intersectionArea / unionArea
}

// MARK: - Polygon Area

/// Compute the area of a polygon using the shoelace formula.
///
/// - Parameter polygon: Array of (x, y) vertices in order (clockwise or counter-clockwise)
/// - Returns: Absolute area of the polygon
internal func polygonArea(polygon: [(CGFloat, CGFloat)]) -> CGFloat {
    guard polygon.count >= 3 else { return 0 }

    var area: CGFloat = 0
    let n = polygon.count

    for i in 0..<n {
        let j = (i + 1) % n
        area += polygon[i].0 * polygon[j].1
        area -= polygon[j].0 * polygon[i].1
    }

    return abs(area) / 2.0
}

// MARK: - Sutherland-Hodgman Polygon Clipping

/// Clip a polygon against another polygon using the Sutherland-Hodgman algorithm.
///
/// This computes the intersection of two convex polygons.
///
/// - Parameters:
///   - subject: The polygon to be clipped
///   - clip: The clipping polygon
/// - Returns: The intersection polygon, or empty array if no intersection
internal func polygonClip(subject: [(CGFloat, CGFloat)], clip: [(CGFloat, CGFloat)]) -> [(CGFloat, CGFloat)] {
    guard !subject.isEmpty, !clip.isEmpty else { return [] }

    var output = subject

    for i in 0..<clip.count {
        if output.isEmpty {
            return []
        }

        let clipEdgeStart = clip[i]
        let clipEdgeEnd = clip[(i + 1) % clip.count]

        let input = output
        output = []

        for j in 0..<input.count {
            let current = input[j]
            let previous = input[(j + input.count - 1) % input.count]

            let currentInside = isInsideEdge(point: current, edgeStart: clipEdgeStart, edgeEnd: clipEdgeEnd)
            let previousInside = isInsideEdge(point: previous, edgeStart: clipEdgeStart, edgeEnd: clipEdgeEnd)

            if currentInside {
                if !previousInside {
                    // Entering: add intersection point
                    if let intersection = lineSegmentIntersection(
                        p1: previous, p2: current,
                        p3: clipEdgeStart, p4: clipEdgeEnd
                    ) {
                        output.append(intersection)
                    }
                }
                output.append(current)
            } else if previousInside {
                // Leaving: add intersection point
                if let intersection = lineSegmentIntersection(
                    p1: previous, p2: current,
                    p3: clipEdgeStart, p4: clipEdgeEnd
                ) {
                    output.append(intersection)
                }
            }
        }
    }

    return output
}

/// Check if a point is inside (or on) an edge.
///
/// Uses cross product to determine which side of the edge the point is on.
/// Returns true if point is to the left of the edge (inside for CCW polygon).
private func isInsideEdge(point: (CGFloat, CGFloat), edgeStart: (CGFloat, CGFloat), edgeEnd: (CGFloat, CGFloat)) -> Bool {
    let cross = (edgeEnd.0 - edgeStart.0) * (point.1 - edgeStart.1) -
                (edgeEnd.1 - edgeStart.1) * (point.0 - edgeStart.0)
    return cross >= 0  // >= 0 means on or inside
}

/// Find intersection point of two line segments.
///
/// - Parameters:
///   - p1, p2: First line segment endpoints
///   - p3, p4: Second line segment endpoints
/// - Returns: Intersection point, or nil if segments don't intersect
private func lineSegmentIntersection(
    p1: (CGFloat, CGFloat), p2: (CGFloat, CGFloat),
    p3: (CGFloat, CGFloat), p4: (CGFloat, CGFloat)
) -> (CGFloat, CGFloat)? {
    let d1x = p2.0 - p1.0
    let d1y = p2.1 - p1.1
    let d2x = p4.0 - p3.0
    let d2y = p4.1 - p3.1

    let cross = d1x * d2y - d1y * d2x

    if abs(cross) < 1e-10 {
        return nil  // Parallel lines
    }

    let t = ((p3.0 - p1.0) * d2y - (p3.1 - p1.1) * d2x) / cross

    return (p1.0 + t * d1x, p1.1 + t * d1y)
}

#endif
