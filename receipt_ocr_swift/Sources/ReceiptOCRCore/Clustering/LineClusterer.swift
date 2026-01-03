import Foundation

#if os(macOS)
import CoreGraphics

/// Result of line clustering operation
public struct ClusteringResult: Codable {
    /// Dictionary mapping cluster ID to array of line indices
    /// Cluster ID of -1 indicates noise (unclustered lines)
    public let clusters: [Int: [Int]]

    /// Number of valid clusters (excluding noise)
    public var clusterCount: Int {
        return clusters.keys.filter { $0 != -1 }.count
    }

    private enum CodingKeys: String, CodingKey {
        case clusters
    }

    public init(clusters: [Int: [Int]]) {
        self.clusters = clusters
    }
}

/// DBSCAN-based line clustering for receipt detection
public struct LineClusterer {

    /// Clusters lines using DBSCAN algorithm based on 2D centroid distance.
    /// Used for PHOTO images where receipts can be at any position.
    ///
    /// - Parameters:
    ///   - lines: Array of OCR lines to cluster
    ///   - eps: Maximum distance between two points to be considered neighbors (normalized coords)
    ///   - minSamples: Minimum number of points required to form a dense region
    /// - Returns: ClusteringResult with cluster assignments
    public func dbscanLines(
        lines: [Line],
        eps: CGFloat = 0.1,
        minSamples: Int = 2
    ) -> ClusteringResult {
        guard !lines.isEmpty else {
            return ClusteringResult(clusters: [:])
        }

        // Extract centroids for each line
        let points = lines.map { calculateCentroid(line: $0) }
        let n = points.count

        // Initialize bookkeeping
        var visited = [Bool](repeating: false, count: n)
        var clusterLabels = [Int?](repeating: nil, count: n)  // nil = not assigned, -1 = noise
        var currentCluster = 1

        // Region query: find all points within eps distance
        func regionQuery(_ idx: Int) -> [Int] {
            var neighbors: [Int] = []
            let (x1, y1) = points[idx]
            for j in 0..<n {
                let (x2, y2) = points[j]
                let distance = sqrt(pow(x1 - x2, 2) + pow(y1 - y2, 2))
                if distance <= eps {
                    neighbors.append(j)
                }
            }
            return neighbors
        }

        // Expand cluster from a core point
        func expandCluster(_ pointIdx: Int, _ neighbors: [Int], _ clusterId: Int) {
            clusterLabels[pointIdx] = clusterId
            var seeds = neighbors
            var seedsSet = Set(neighbors)  // O(1) lookup for contains check

            while !seeds.isEmpty {
                let currentPoint = seeds.removeFirst()
                seedsSet.remove(currentPoint)

                // Skip if already visited
                if visited[currentPoint] {
                    // Assign to cluster if not already assigned
                    if clusterLabels[currentPoint] == nil || clusterLabels[currentPoint] == -1 {
                        clusterLabels[currentPoint] = clusterId
                    }
                    continue
                }

                // Mark as visited and check neighbors
                visited[currentPoint] = true
                let newNeighbors = regionQuery(currentPoint)

                // If it's a core point, add unvisited neighbors to seeds
                if newNeighbors.count >= minSamples {
                    for neighbor in newNeighbors where !seedsSet.contains(neighbor) {
                        seeds.append(neighbor)
                        seedsSet.insert(neighbor)
                    }
                }

                // Assign to cluster
                if clusterLabels[currentPoint] == nil || clusterLabels[currentPoint] == -1 {
                    clusterLabels[currentPoint] = clusterId
                }
            }
        }

        // Main DBSCAN loop
        for i in 0..<n {
            if visited[i] {
                continue
            }

            visited[i] = true
            let neighbors = regionQuery(i)

            if neighbors.count < minSamples {
                // Label as noise
                clusterLabels[i] = -1
            } else {
                // Start a new cluster and expand it
                expandCluster(i, neighbors, currentCluster)
                currentCluster += 1
            }
        }

        // Group line indices by cluster label
        var clusters: [Int: [Int]] = [:]
        for (idx, label) in clusterLabels.enumerated() {
            guard let l = label else { continue }
            clusters[l, default: []].append(idx)
        }

        return ClusteringResult(clusters: clusters)
    }

    /// Clusters lines using X-axis based grouping.
    /// Used for SCAN images where receipts are typically side-by-side.
    ///
    /// - Parameters:
    ///   - lines: Array of OCR lines to cluster
    ///   - eps: Maximum X-distance between adjacent lines to be in same cluster (normalized)
    ///   - minSamples: Minimum number of lines required to form a valid cluster
    /// - Returns: ClusteringResult with cluster assignments
    public func dbscanLinesXAxis(
        lines: [Line],
        eps: CGFloat = 0.08,
        minSamples: Int = 2
    ) -> ClusteringResult {
        guard !lines.isEmpty else {
            return ClusteringResult(clusters: [:])
        }

        // Compute X-centroid for each line and sort
        var linesWithX: [(index: Int, x: CGFloat)] = []
        for (idx, line) in lines.enumerated() {
            let (x, _) = calculateCentroid(line: line)
            linesWithX.append((index: idx, x: x))
        }
        linesWithX.sort { $0.x < $1.x }

        // Group into clusters based on X proximity
        var clusterAssignments = [Int](repeating: -1, count: lines.count)
        var currentClusterId = 1
        var currentClusterIndices: [Int] = [linesWithX[0].index]
        var previousX = linesWithX[0].x

        for i in 1..<linesWithX.count {
            let (idx, currentX) = linesWithX[i]

            if abs(currentX - previousX) <= eps {
                // Add to current cluster
                currentClusterIndices.append(idx)
            } else {
                // Finalize current cluster
                if currentClusterIndices.count >= minSamples {
                    for lineIdx in currentClusterIndices {
                        clusterAssignments[lineIdx] = currentClusterId
                    }
                    currentClusterId += 1
                }
                // Start new cluster
                currentClusterIndices = [idx]
            }
            previousX = currentX
        }

        // Finalize last cluster
        if currentClusterIndices.count >= minSamples {
            for lineIdx in currentClusterIndices {
                clusterAssignments[lineIdx] = currentClusterId
            }
        }

        // Build result dictionary
        var clusters: [Int: [Int]] = [:]
        for (idx, clusterId) in clusterAssignments.enumerated() {
            if clusterId != -1 {
                clusters[clusterId, default: []].append(idx)
            } else {
                clusters[-1, default: []].append(idx)
            }
        }

        return ClusteringResult(clusters: clusters)
    }

    /// Calculate the centroid of a line from its corner points
    private func calculateCentroid(line: Line) -> (x: CGFloat, y: CGFloat) {
        let x = (line.topLeft.x + line.topRight.x + line.bottomLeft.x + line.bottomRight.x) / 4.0
        let y = (line.topLeft.y + line.topRight.y + line.bottomLeft.y + line.bottomRight.y) / 4.0
        return (x, y)
    }

    /// Calculate the diagonal length of a line's bounding area
    public func calculateDiagonalLength(line: Line) -> CGFloat {
        let dx = line.topRight.x - line.bottomLeft.x
        let dy = line.topRight.y - line.bottomLeft.y
        return sqrt(dx * dx + dy * dy)
    }

    /// Calculate average diagonal length of all lines (used for dynamic eps calculation)
    public func averageDiagonalLength(lines: [Line]) -> CGFloat {
        guard !lines.isEmpty else { return 0 }
        let total = lines.reduce(0.0) { $0 + calculateDiagonalLength(line: $1) }
        return total / CGFloat(lines.count)
    }
}
#endif
