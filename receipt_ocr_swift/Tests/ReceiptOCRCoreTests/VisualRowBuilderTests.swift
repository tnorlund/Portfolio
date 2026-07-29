import CoreGraphics
import XCTest

@testable import ReceiptOCRCore

final class VisualRowBuilderTests: XCTestCase {
  private func point(_ x: CGFloat, _ y: CGFloat) -> CodablePoint {
    CodablePoint(x: x, y: y)
  }

  private func makeWord(
    _ text: String,
    x: CGFloat,
    y: CGFloat = 0,
    width: CGFloat,
    height: CGFloat = 0.04
  ) -> Word {
    Word(
      text: text,
      boundingBox: NormalizedRect(x: x, y: y, width: width, height: height),
      topLeft: point(x, y + height),
      topRight: point(x + width, y + height),
      bottomLeft: point(x, y),
      bottomRight: point(x + width, y),
      angleDegrees: 0,
      angleRadians: 0,
      confidence: 1,
      letters: [],
      extractedData: nil
    )
  }

  private func makeLine(
    _ text: String,
    x: CGFloat,
    y: CGFloat,
    width: CGFloat = 0.2,
    height: CGFloat = 0.1,
    words: [Word] = []
  ) -> Line {
    Line(
      text: text,
      boundingBox: NormalizedRect(x: x, y: y, width: width, height: height),
      topLeft: point(x, y + height),
      topRight: point(x + width, y + height),
      bottomLeft: point(x, y),
      bottomRight: point(x + width, y),
      angleDegrees: 0,
      angleRadians: 0,
      confidence: 1,
      words: words
    )
  }

  func test_emptyInputProducesNoRows() {
    XCTAssertEqual(VisualRowBuilder().build(lines: []), [])
  }

  func test_filteredLineDoesNotReclaimItsOriginalID() {
    let lines = [
      makeLine("", x: 0.1, y: 0.9),
      makeLine("KEPT", x: 0.1, y: 0.5),
    ]
    let predictions = [
      LinePrediction(
        tokens: ["DROPPED"],
        labels: ["O"],
        confidences: [1],
        allProbabilities: nil
      ),
      LinePrediction(
        tokens: ["KEPT"],
        labels: ["B-MERCHANT_NAME"],
        confidences: [1],
        allProbabilities: nil
      ),
    ]

    let rows = VisualRowBuilder().build(lines: lines, predictions: predictions)

    XCTAssertEqual(rows.count, 1)
    XCTAssertEqual(rows[0].rowID, 2)
    XCTAssertEqual(rows[0].lineIDs, [2])
    XCTAssertEqual(rows[0].layoutEvidence.first?.lineID, 2)
  }

  func test_inclusiveCentroidOverlapUsesTransitiveComponents() {
    let lines = [
      makeLine("TOP", x: 0.1, y: 0.9),
      // A and B touch only because A's centroid is exactly B's upper edge.
      makeLine("A", x: 0.8, y: 0.5, height: 0.25),
      // B similarly connects to C, while A and C do not directly overlap.
      makeLine("B", x: 0.4, y: 0.125, height: 0.5),
      makeLine("C", x: 0.1, y: 0, height: 0.375),
    ]

    let rows = VisualRowBuilder().build(lines: lines)

    XCTAssertEqual(rows.count, 2)
    XCTAssertEqual(rows[0].lineIDs, [1])
    XCTAssertEqual(rows[0].embeddingInput, "<EDGE>\nTOP\nC B A")
    XCTAssertEqual(rows[1].rowID, 4)
    XCTAssertEqual(rows[1].lineIDs, [4, 3, 2])
    XCTAssertEqual(rows[1].text, "C B A")
    XCTAssertEqual(rows[1].embeddingInput, "TOP\nC B A\n<EDGE>")
  }

  func test_equalXKeepsOriginalLineOrder() {
    let lines = [
      makeLine("FIRST", x: 0.2, y: 0.5),
      makeLine("SECOND", x: 0.2, y: 0.52),
    ]

    let rows = VisualRowBuilder().build(lines: lines)

    XCTAssertEqual(rows.map(\.lineIDs), [[1, 2]])
    XCTAssertEqual(rows[0].rowID, 1)
    XCTAssertEqual(rows[0].text, "FIRST SECOND")
  }

  func test_priceColumnAndLabelPairingMatchPythonReceiptRows() throws {
    let lines = [
      makeLine(
        "ORGANIC APPLES",
        x: 0.05,
        y: 0.80,
        width: 0.45,
        height: 0.04,
        words: [
          makeWord("ORGANIC", x: 0.05, y: 0.80, width: 0.18),
          makeWord("APPLES", x: 0.25, y: 0.80, width: 0.16),
        ]
      ),
      makeLine(
        "$4.99",
        x: 0.82,
        y: 0.80,
        width: 0.13,
        height: 0.04,
        words: [makeWord("$4.99", x: 0.82, y: 0.80, width: 0.13)]
      ),
      makeLine(
        "TOTAL $4.99",
        x: 0.05,
        y: 0.20,
        width: 0.90,
        height: 0.04,
        words: [
          makeWord("TOTAL", x: 0.05, y: 0.20, width: 0.15),
          makeWord("$4.99", x: 0.82, y: 0.20, width: 0.13),
        ]
      ),
      makeLine(
        "$0.50",
        x: 0.40,
        y: 0.05,
        width: 0.15,
        height: 0.04,
        words: [makeWord("$0.50", x: 0.40, y: 0.05, width: 0.15)]
      ),
    ]

    let rows = VisualRowBuilder().build(lines: lines)
    let itemRow = try XCTUnwrap(rows.first)
    let totalRow = try XCTUnwrap(rows.dropFirst().first)

    XCTAssertEqual(itemRow.rowID, 1)
    XCTAssertEqual(itemRow.lineIDs, [1, 2])
    XCTAssertEqual(
      try XCTUnwrap(itemRow.priceColumnX),
      0.95,
      accuracy: 0.000_001
    )
    XCTAssertEqual(itemRow.labelText, "ORGANIC APPLES")
    XCTAssertEqual(itemRow.amountText, "$4.99")
    XCTAssertEqual(itemRow.bounds.xMin, 0.05, accuracy: 0.000_001)
    XCTAssertEqual(itemRow.bounds.yMin, 0.80, accuracy: 0.000_001)
    XCTAssertEqual(itemRow.bounds.xMax, 0.95, accuracy: 0.000_001)
    XCTAssertEqual(itemRow.bounds.yMax, 0.84, accuracy: 0.000_001)

    XCTAssertEqual(totalRow.rowID, 3)
    XCTAssertEqual(totalRow.labelText, "TOTAL")
    XCTAssertEqual(totalRow.amountText, "$4.99")
    // The isolated amount is not close enough to the dominant price column.
    XCTAssertNil(rows.last?.amountText)
  }

  func test_layoutEvidenceUsesOriginalIDsAfterGeometrySorting() {
    let lines = [
      makeLine("BOTTOM", x: 0.1, y: 0.1),
      makeLine("9.99", x: 0.8, y: 0.8),
      makeLine("PRODUCT", x: 0.1, y: 0.8),
    ]
    let predictions = [
      LinePrediction(
        tokens: ["BOTTOM"],
        labels: ["B-FOOTER"],
        confidences: [0.7],
        allProbabilities: nil
      ),
      LinePrediction(
        tokens: ["9.99"],
        labels: ["B-LINE_TOTAL"],
        confidences: [0.9],
        allProbabilities: nil
      ),
      // The shorter label array maps only word 1 and never shifts line 2.
      LinePrediction(
        tokens: ["PRODUCT", "EXTRA"],
        labels: ["B-PRODUCT_NAME"],
        confidences: [0.8, 0.1],
        allProbabilities: nil
      ),
    ]

    let rows = VisualRowBuilder().build(
      lines: lines,
      predictions: predictions
    )

    XCTAssertEqual(rows[0].lineIDs, [3, 2])
    XCTAssertEqual(rows[0].rowID, 3)
    XCTAssertEqual(rows[0].embeddingInput, "<EDGE>\nPRODUCT 9.99\nBOTTOM")
    XCTAssertEqual(
      rows[0].layoutEvidence,
      [
        LayoutWordEvidence(
          lineID: 3,
          wordID: 1,
          text: "PRODUCT",
          label: "B-PRODUCT_NAME",
          confidence: Double(Float(0.8))
        ),
        LayoutWordEvidence(
          lineID: 2,
          wordID: 1,
          text: "9.99",
          label: "B-LINE_TOTAL",
          confidence: Double(Float(0.9))
        ),
      ]
    )
    XCTAssertEqual(rows[1].layoutEvidence.first?.lineID, 1)
  }

  func test_amountRecognitionMatchesCanonicalCentsPattern() {
    let cases: [(String, Bool)] = [
      ("$12.99", true),
      ("1234.56", true),
      ("$10000.00", true),
      ("1,234.50", true),
      ("(2.00)", true),
      ("12,34.56", false),
      ("12/99", false),
    ]

    for (text, expected) in cases {
      XCTAssertEqual(
        VisualRowBuilder.isAmountText(text),
        expected,
        "unexpected amount recognition for \(text)"
      )
    }
  }

  func testSharedSwiftPythonFixtureHasIdenticalRowsAndContexts() throws {
    struct Envelope: Decodable {
      let receipts: [ReceiptOutput]
    }
    let fixtureURL = URL(fileURLWithPath: #filePath)
      .deletingLastPathComponent()
      .appendingPathComponent("Fixtures/swift_single_pass_contract.json")
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    let receipt = try XCTUnwrap(
      decoder.decode(
        Envelope.self,
        from: Data(contentsOf: fixtureURL)
      ).receipts.first
    )

    let rows = VisualRowBuilder().build(
      lines: try XCTUnwrap(receipt.lines),
      predictions: receipt.layoutlmPredictions ?? []
    )

    XCTAssertEqual(rows.map(\.rowID), [1, 3, 4])
    XCTAssertEqual(
      rows.map(\.text),
      [
        "SPROUTS FARMERS MARKET",
        "ORGANIC BANANAS 3.99",
        "TOTAL 3.99",
      ]
    )
    XCTAssertEqual(
      rows.map(\.embeddingInput),
      [
        "<EDGE>\nSPROUTS FARMERS MARKET\nORGANIC BANANAS 3.99",
        "SPROUTS FARMERS MARKET\nORGANIC BANANAS 3.99\nTOTAL 3.99",
        "ORGANIC BANANAS 3.99\nTOTAL 3.99\n<EDGE>",
      ]
    )
    XCTAssertEqual(rows[1].labelText, "ORGANIC")
    XCTAssertEqual(rows[1].amountText, "3.99")
    XCTAssertEqual(rows[2].labelText, "TOTAL")
    XCTAssertEqual(rows[2].amountText, "3.99")
  }
}
