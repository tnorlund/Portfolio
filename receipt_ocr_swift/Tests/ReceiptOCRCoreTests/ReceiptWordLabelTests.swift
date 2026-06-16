import XCTest
@testable import ReceiptOCRCore

final class ReceiptWordLabelTests: XCTestCase {
    func test_fromLinePredictions_filtersNonCoreMergePresetLabels() {
        let predictions = [
            LinePrediction(
                tokens: ["store", "123", "visa", "tip"],
                labels: [
                    "B-MERCHANT_NAME",
                    "B-AMOUNT",
                    "B-CARD_NUMBER",
                    "B-TIP",
                ],
                confidences: [0.95, 0.91, 0.88, 0.93],
                allProbabilities: nil
            )
        ]

        let labels = ReceiptWordLabel.fromLinePredictions(
            predictions: predictions,
            imageId: "img-1",
            receiptId: 1
        )

        XCTAssertEqual(labels.map(\.label), ["MERCHANT_NAME", "TIP"])
        XCTAssertEqual(labels.map(\.wordId), [1, 4])
    }
}
