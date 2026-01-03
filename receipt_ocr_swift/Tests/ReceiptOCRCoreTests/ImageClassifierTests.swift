import XCTest
@testable import ReceiptOCRCore

#if os(macOS)
final class ImageClassifierTests: XCTestCase {
    let classifier = ImageClassifier()

    // MARK: - Margin Tests

    func testFindMarginsEmptyLines() {
        let margins = classifier.findMargins(lines: [])
        XCTAssertEqual(margins.left, 1.0)
        XCTAssertEqual(margins.right, 1.0)
        XCTAssertEqual(margins.top, 1.0)
        XCTAssertEqual(margins.bottom, 1.0)
    }

    func testFindMarginsFullCoverage() {
        // Line that fills the entire image
        let line = createLine(
            topLeft: (0.0, 1.0),
            topRight: (1.0, 1.0),
            bottomLeft: (0.0, 0.0),
            bottomRight: (1.0, 0.0)
        )
        let margins = classifier.findMargins(lines: [line])

        XCTAssertEqual(margins.left, 0.0, accuracy: 0.001)
        XCTAssertEqual(margins.right, 0.0, accuracy: 0.001)
        XCTAssertEqual(margins.top, 0.0, accuracy: 0.001)
        XCTAssertEqual(margins.bottom, 0.0, accuracy: 0.001)
    }

    func testFindMarginsWithPadding() {
        // Line with 10% margins on all sides
        let line = createLine(
            topLeft: (0.1, 0.9),
            topRight: (0.9, 0.9),
            bottomLeft: (0.1, 0.1),
            bottomRight: (0.9, 0.1)
        )
        let margins = classifier.findMargins(lines: [line])

        XCTAssertEqual(margins.left, 0.1, accuracy: 0.001)
        XCTAssertEqual(margins.right, 0.1, accuracy: 0.001)
        XCTAssertEqual(margins.top, 0.1, accuracy: 0.001)
        XCTAssertEqual(margins.bottom, 0.1, accuracy: 0.001)
    }

    // MARK: - Classification Tests

    func testClassifyNative() {
        // Lines that fill the image (margins < 1%)
        let line = createLine(
            topLeft: (0.005, 0.995),
            topRight: (0.995, 0.995),
            bottomLeft: (0.005, 0.005),
            bottomRight: (0.995, 0.005)
        )

        let result = classifier.classify(lines: [line], imageWidth: 1000, imageHeight: 800)
        XCTAssertEqual(result.imageType, .native)
    }

    func testClassifyPhoto() {
        // Lines with margins and phone-like dimensions
        let line = createLine(
            topLeft: (0.2, 0.8),
            topRight: (0.8, 0.8),
            bottomLeft: (0.2, 0.2),
            bottomRight: (0.8, 0.2)
        )

        // Phone dimensions: 4032x3024
        let result = classifier.classify(lines: [line], imageWidth: 4032, imageHeight: 3024)
        XCTAssertEqual(result.imageType, .photo)
    }

    func testClassifyScan() {
        // Lines with margins and scanner-like dimensions
        let line = createLine(
            topLeft: (0.2, 0.8),
            topRight: (0.8, 0.8),
            bottomLeft: (0.2, 0.2),
            bottomRight: (0.8, 0.2)
        )

        // Scanner dimensions: 3508x2480
        let result = classifier.classify(lines: [line], imageWidth: 3508, imageHeight: 2480)
        XCTAssertEqual(result.imageType, .scan)
    }

    func testClassifyRotatedScan() {
        // Scanner dimensions rotated 90 degrees
        let line = createLine(
            topLeft: (0.2, 0.8),
            topRight: (0.8, 0.8),
            bottomLeft: (0.2, 0.2),
            bottomRight: (0.8, 0.2)
        )

        let result = classifier.classify(lines: [line], imageWidth: 2480, imageHeight: 3508)
        XCTAssertEqual(result.imageType, .scan)
    }

    // MARK: - Helper Functions

    private func createLine(
        topLeft: (CGFloat, CGFloat),
        topRight: (CGFloat, CGFloat),
        bottomLeft: (CGFloat, CGFloat),
        bottomRight: (CGFloat, CGFloat)
    ) -> Line {
        return Line(
            text: "Test line",
            boundingBox: NormalizedRect(
                x: topLeft.0,
                y: bottomLeft.1,
                width: topRight.0 - topLeft.0,
                height: topLeft.1 - bottomLeft.1
            ),
            topLeft: CodablePoint(x: topLeft.0, y: topLeft.1),
            topRight: CodablePoint(x: topRight.0, y: topRight.1),
            bottomLeft: CodablePoint(x: bottomLeft.0, y: bottomLeft.1),
            bottomRight: CodablePoint(x: bottomRight.0, y: bottomRight.1),
            angleDegrees: 0.0,
            angleRadians: 0.0,
            confidence: 0.95,
            words: []
        )
    }
}
#endif
