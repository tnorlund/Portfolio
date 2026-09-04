import XCTest
@testable import ReceiptOCRCore

final class ReceiptWordLabelTests: XCTestCase {
    func test_fromLinePredictions_filtersUnknownButKeepsAmountAndAliases() {
        // Merged-amount models (layoutlm-v31+) emit AMOUNT for currency
        // words and ADDRESS instead of ADDRESS_LINE. Both must survive to
        // DynamoDB — the cloud pipeline reclassifies AMOUNT into specific
        // financial labels, and dropping it strips every currency anchor
        // at ingest (#1466). Truly unknown labels are still filtered.
        let predictions = [
            LinePrediction(
                tokens: ["store", "4.99", "1234", "tip", "main"],
                labels: [
                    "B-MERCHANT_NAME",
                    "B-AMOUNT",
                    "B-CARD_NUMBER",
                    "B-TIP",
                    "B-ADDRESS",
                ],
                confidences: [0.95, 0.91, 0.88, 0.93, 0.9],
                allProbabilities: nil
            )
        ]

        let labels = ReceiptWordLabel.fromLinePredictions(
            predictions: predictions,
            imageId: "img-1",
            receiptId: 1
        )

        XCTAssertEqual(
            labels.map(\.label),
            ["MERCHANT_NAME", "AMOUNT", "TIP", "ADDRESS_LINE"]
        )
        XCTAssertEqual(labels.map(\.wordId), [1, 2, 4, 5])
    }
}
