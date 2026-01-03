import XCTest
@testable import ReceiptOCRCore

#if os(macOS)
import CoreGraphics

final class GeometryTests: XCTestCase {

    // MARK: - Convex Hull Tests

    func testConvexHullTriangle() {
        // Three points forming a triangle
        let points: [(CGFloat, CGFloat)] = [
            (0, 0),
            (1, 0),
            (0.5, 1),
        ]

        let hull = convexHull(points: points)
        XCTAssertEqual(hull.count, 3)
    }

    func testConvexHullSquare() {
        // Four points forming a square
        let points: [(CGFloat, CGFloat)] = [
            (0, 0),
            (1, 0),
            (1, 1),
            (0, 1),
        ]

        let hull = convexHull(points: points)
        XCTAssertEqual(hull.count, 4)
    }

    func testConvexHullWithInteriorPoints() {
        // Square with interior point that should be excluded
        let points: [(CGFloat, CGFloat)] = [
            (0, 0),
            (1, 0),
            (1, 1),
            (0, 1),
            (0.5, 0.5),  // Interior point
        ]

        let hull = convexHull(points: points)
        XCTAssertEqual(hull.count, 4)  // Interior point excluded
    }

    func testConvexHullDuplicatePoints() {
        // Duplicate points should be handled
        let points: [(CGFloat, CGFloat)] = [
            (0, 0),
            (1, 0),
            (1, 0),  // Duplicate
            (1, 1),
            (0, 1),
        ]

        let hull = convexHull(points: points)
        XCTAssertGreaterThanOrEqual(hull.count, 3)
    }

    func testConvexHullCentroid() {
        // Unit square hull
        let hull: [(CGFloat, CGFloat)] = [
            (0, 0),
            (1, 0),
            (1, 1),
            (0, 1),
        ]

        let centroid = computeHullCentroid(hull: hull)
        XCTAssertEqual(centroid.0, 0.5, accuracy: 0.01)
        XCTAssertEqual(centroid.1, 0.5, accuracy: 0.01)
    }

    // MARK: - Minimum Area Rectangle Tests

    func testMinAreaRectSquare() {
        // Axis-aligned square
        let points: [(CGFloat, CGFloat)] = [
            (0, 0),
            (100, 0),
            (100, 100),
            (0, 100),
        ]

        let (center, size, _) = minAreaRect(points: points)

        XCTAssertEqual(center.0, 50, accuracy: 1.0)
        XCTAssertEqual(center.1, 50, accuracy: 1.0)
        XCTAssertEqual(min(size.0, size.1), 100, accuracy: 1.0)
        XCTAssertEqual(max(size.0, size.1), 100, accuracy: 1.0)
    }

    func testMinAreaRectRectangle() {
        // Axis-aligned rectangle (wider than tall)
        let points: [(CGFloat, CGFloat)] = [
            (0, 0),
            (200, 0),
            (200, 100),
            (0, 100),
        ]

        let (center, size, _) = minAreaRect(points: points)

        XCTAssertEqual(center.0, 100, accuracy: 1.0)
        XCTAssertEqual(center.1, 50, accuracy: 1.0)
        XCTAssertEqual(min(size.0, size.1), 100, accuracy: 1.0)
        XCTAssertEqual(max(size.0, size.1), 200, accuracy: 1.0)
    }

    func testMinAreaRectRotated() {
        // Diamond shape (45-degree rotated square)
        let sqrt2 = sqrt(2.0) as CGFloat
        let points: [(CGFloat, CGFloat)] = [
            (50, 0),       // Bottom
            (100, 50),     // Right
            (50, 100),     // Top
            (0, 50),       // Left
        ]

        let (center, size, angleDeg) = minAreaRect(points: points)

        XCTAssertEqual(center.0, 50, accuracy: 1.0)
        XCTAssertEqual(center.1, 50, accuracy: 1.0)
        // Should be approximately 70.7 x 70.7 (50 * sqrt(2))
        let expectedSize = 50.0 * sqrt2
        XCTAssertEqual(size.0, expectedSize, accuracy: 2.0)
        XCTAssertEqual(size.1, expectedSize, accuracy: 2.0)
    }

    // MARK: - Box Points Tests

    func testBoxPointsAxisAligned() {
        // Axis-aligned rectangle (0 degrees)
        let center: (CGFloat, CGFloat) = (50, 50)
        let size: (CGFloat, CGFloat) = (100, 50)
        let angleDeg: CGFloat = 0

        let corners = boxPoints(center: center, size: size, angleDeg: angleDeg)

        XCTAssertEqual(corners.count, 4)
        // Check that corners form expected rectangle
        let xs = corners.map { $0.0 }
        let ys = corners.map { $0.1 }
        XCTAssertEqual(xs.max()! - xs.min()!, 100, accuracy: 1.0)
        XCTAssertEqual(ys.max()! - ys.min()!, 50, accuracy: 1.0)
    }

    func testBoxPointsRotated45() {
        // 45-degree rotated square
        let center: (CGFloat, CGFloat) = (50, 50)
        let size: (CGFloat, CGFloat) = (70.7, 70.7)  // ~50 * sqrt(2)
        let angleDeg: CGFloat = 45

        let corners = boxPoints(center: center, size: size, angleDeg: angleDeg)

        XCTAssertEqual(corners.count, 4)
        // All corners should be equidistant from center
        for corner in corners {
            let dist = sqrt(pow(corner.0 - 50, 2) + pow(corner.1 - 50, 2))
            XCTAssertEqual(dist, 50, accuracy: 2.0)
        }
    }

    // MARK: - Reorder Box Points Tests

    func testReorderBoxPointsAlreadyOrdered() {
        // Points already in TL, TR, BR, BL order
        let points: [(CGFloat, CGFloat)] = [
            (0, 100),    // TL
            (100, 100),  // TR
            (100, 0),    // BR
            (0, 0),      // BL
        ]

        let reordered = reorderBoxPoints(points)

        XCTAssertEqual(reordered.count, 4)
        // Top-left should have smallest X among top points
        XCTAssertLessThan(reordered[0].0, reordered[1].0)
        // Top points should have larger Y than bottom points
        XCTAssertGreaterThan(reordered[0].1, reordered[3].1)
    }

    func testReorderBoxPointsScrambled() {
        // Points in random order
        let points: [(CGFloat, CGFloat)] = [
            (100, 0),    // BR
            (0, 100),    // TL
            (0, 0),      // BL
            (100, 100),  // TR
        ]

        let reordered = reorderBoxPoints(points)

        XCTAssertEqual(reordered.count, 4)
        // Verify TL, TR, BR, BL order
        // TL: smallest sum (x+y among top)
        // TR: largest X among top
        // BR: largest sum
        // BL: smallest X among bottom
        XCTAssertLessThan(reordered[0].0, reordered[1].0)  // TL.x < TR.x
        XCTAssertLessThan(reordered[3].0, reordered[2].0)  // BL.x < BR.x
    }

    // MARK: - IoU Tests

    func testComputeIoUIdenticalBoxes() {
        // Two identical boxes should have IoU of 1.0
        let box: [(CGFloat, CGFloat)] = [
            (0, 100),
            (100, 100),
            (100, 0),
            (0, 0),
        ]

        let iou = computeIoU(boxA: box, boxB: box)
        XCTAssertEqual(iou, 1.0, accuracy: 0.01)
    }

    func testComputeIoUNonOverlapping() {
        // Two non-overlapping boxes should have IoU of 0
        let boxA: [(CGFloat, CGFloat)] = [
            (0, 100),
            (100, 100),
            (100, 0),
            (0, 0),
        ]
        let boxB: [(CGFloat, CGFloat)] = [
            (200, 100),
            (300, 100),
            (300, 0),
            (200, 0),
        ]

        let iou = computeIoU(boxA: boxA, boxB: boxB)
        XCTAssertEqual(iou, 0, accuracy: 0.01)
    }

    func testComputeIoUPartialOverlap() {
        // Two boxes with 50% overlap
        let boxA: [(CGFloat, CGFloat)] = [
            (0, 100),
            (100, 100),
            (100, 0),
            (0, 0),
        ]
        let boxB: [(CGFloat, CGFloat)] = [
            (50, 100),
            (150, 100),
            (150, 0),
            (50, 0),
        ]

        let iou = computeIoU(boxA: boxA, boxB: boxB)
        // Intersection = 50x100 = 5000
        // Union = 100x100 + 100x100 - 5000 = 15000
        // IoU = 5000/15000 = 0.333
        XCTAssertEqual(iou, 1.0/3.0, accuracy: 0.05)
    }

    // MARK: - Polygon Area Tests

    func testPolygonAreaSquare() {
        let square: [(CGFloat, CGFloat)] = [
            (0, 0),
            (100, 0),
            (100, 100),
            (0, 100),
        ]

        let area = polygonArea(polygon: square)
        XCTAssertEqual(area, 10000, accuracy: 1.0)
    }

    func testPolygonAreaTriangle() {
        let triangle: [(CGFloat, CGFloat)] = [
            (0, 0),
            (100, 0),
            (50, 100),
        ]

        let area = polygonArea(polygon: triangle)
        XCTAssertEqual(area, 5000, accuracy: 1.0)
    }

    // MARK: - Circular Mean Angle Tests

    func testCircularMeanAngleSameAngles() {
        let angle1 = CGFloat.pi / 4  // 45 degrees
        let angle2 = CGFloat.pi / 4

        let mean = circularMeanAngle(angle1, angle2)
        XCTAssertEqual(mean, CGFloat.pi / 4, accuracy: 0.01)
    }

    func testCircularMeanAngleOppositeAngles() {
        let angle1: CGFloat = 0
        let angle2 = CGFloat.pi  // 180 degrees

        let mean = circularMeanAngle(angle1, angle2)
        // Mean of 0 and 180 degrees is either 90 or -90 degrees
        XCTAssertEqual(abs(mean), CGFloat.pi / 2, accuracy: 0.01)
    }

    func testCircularMeanAngleWraparound() {
        // Near 180 degrees (should handle wraparound correctly)
        let angle1 = CGFloat.pi - 0.1   // Just below 180
        let angle2 = -CGFloat.pi + 0.1  // Just above -180 (same direction)

        let mean = circularMeanAngle(angle1, angle2)
        // Mean should be close to ±π
        XCTAssertGreaterThan(abs(mean), CGFloat.pi - 0.2)
    }
}
#endif
