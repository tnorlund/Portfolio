import XCTest
@testable import ReceiptOCRCore

#if os(macOS)
final class LineClustererTests: XCTestCase {
    let clusterer = LineClusterer()

    // MARK: - DBSCAN 2D Tests

    func testDbscanEmptyLines() {
        let result = clusterer.dbscanLines(lines: [], eps: 0.1, minSamples: 2)
        XCTAssertEqual(result.clusterCount, 0)
        XCTAssertTrue(result.clusters.isEmpty)
    }

    func testDbscanSingleCluster() {
        // Create lines close together (should form one cluster)
        let lines = [
            createLineAt(x: 0.5, y: 0.5),
            createLineAt(x: 0.52, y: 0.52),
            createLineAt(x: 0.48, y: 0.48),
        ]

        let result = clusterer.dbscanLines(lines: lines, eps: 0.1, minSamples: 2)
        XCTAssertEqual(result.clusterCount, 1)
    }

    func testDbscanTwoClusters() {
        // Create two groups of lines far apart
        let lines = [
            // Cluster 1 (left side)
            createLineAt(x: 0.1, y: 0.5),
            createLineAt(x: 0.12, y: 0.52),
            createLineAt(x: 0.08, y: 0.48),
            // Cluster 2 (right side)
            createLineAt(x: 0.9, y: 0.5),
            createLineAt(x: 0.92, y: 0.52),
            createLineAt(x: 0.88, y: 0.48),
        ]

        let result = clusterer.dbscanLines(lines: lines, eps: 0.1, minSamples: 2)
        XCTAssertEqual(result.clusterCount, 2)
    }

    func testDbscanNoisePoints() {
        // One dense cluster and one isolated point
        let lines = [
            // Dense cluster
            createLineAt(x: 0.5, y: 0.5),
            createLineAt(x: 0.52, y: 0.52),
            createLineAt(x: 0.48, y: 0.48),
            // Isolated point (noise)
            createLineAt(x: 0.1, y: 0.1),
        ]

        let result = clusterer.dbscanLines(lines: lines, eps: 0.1, minSamples: 2)
        XCTAssertEqual(result.clusterCount, 1)
        // Noise points should be in cluster -1
        XCTAssertNotNil(result.clusters[-1])
        XCTAssertEqual(result.clusters[-1]?.count, 1, "Expected exactly 1 noise point")
    }

    // MARK: - DBSCAN X-Axis Tests

    func testDbscanXAxisEmptyLines() {
        let result = clusterer.dbscanLinesXAxis(lines: [], eps: 0.08, minSamples: 2)
        XCTAssertEqual(result.clusterCount, 0)
    }

    func testDbscanXAxisSingleColumn() {
        // Create lines in a single vertical column
        let lines = [
            createLineAt(x: 0.5, y: 0.1),
            createLineAt(x: 0.52, y: 0.3),
            createLineAt(x: 0.48, y: 0.5),
            createLineAt(x: 0.51, y: 0.7),
        ]

        let result = clusterer.dbscanLinesXAxis(lines: lines, eps: 0.08, minSamples: 2)
        XCTAssertEqual(result.clusterCount, 1)
    }

    func testDbscanXAxisTwoColumns() {
        // Create lines in two separate columns
        let lines = [
            // Left column
            createLineAt(x: 0.2, y: 0.1),
            createLineAt(x: 0.22, y: 0.3),
            createLineAt(x: 0.18, y: 0.5),
            // Right column
            createLineAt(x: 0.8, y: 0.1),
            createLineAt(x: 0.82, y: 0.3),
            createLineAt(x: 0.78, y: 0.5),
        ]

        let result = clusterer.dbscanLinesXAxis(lines: lines, eps: 0.08, minSamples: 2)
        XCTAssertEqual(result.clusterCount, 2)
    }

    // MARK: - Utility Tests

    func testAverageDiagonalLength() {
        let lines = [
            createLineAt(x: 0.0, y: 0.0),  // Width 0.1, height 0.02
            createLineAt(x: 0.5, y: 0.5),
        ]

        let avgDiagonal = clusterer.averageDiagonalLength(lines: lines)
        XCTAssertGreaterThan(avgDiagonal, 0)
    }

    // MARK: - Helper Functions

    private func createLineAt(x: CGFloat, y: CGFloat) -> Line {
        let width: CGFloat = 0.1
        let height: CGFloat = 0.02

        return Line(
            text: "Test",
            boundingBox: NormalizedRect(x: x, y: y, width: width, height: height),
            topLeft: CodablePoint(x: x, y: y + height),
            topRight: CodablePoint(x: x + width, y: y + height),
            bottomLeft: CodablePoint(x: x, y: y),
            bottomRight: CodablePoint(x: x + width, y: y),
            angleDegrees: 0.0,
            angleRadians: 0.0,
            confidence: 0.95,
            words: []
        )
    }
}
#endif
