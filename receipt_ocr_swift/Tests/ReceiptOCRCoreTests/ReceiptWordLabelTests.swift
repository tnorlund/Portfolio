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

    /// The active model (v31) is a merged head: every money field is `AMOUNT`
    /// and address+phone is `ADDRESS`. Neither is in the unmerged core list,
    /// so without the model's own entity types the writer dropped all of
    /// them at ingest -- every amount on every receipt since v31 shipped.
    /// Passing `modelLabels` from the bundle's config.json must keep them,
    /// while still rejecting names the model cannot actually emit.
    func test_fromLinePredictions_keepsMergedLabelsTheModelEmits() {
        let predictions = [
            LinePrediction(
                tokens: ["$13.53", "4190", "visa", "tip"],
                labels: [
                    "B-AMOUNT",
                    "B-ADDRESS",
                    "B-CARD_NUMBER",
                    "B-TIP",
                ],
                confidences: [0.97, 0.94, 0.88, 0.93],
                allProbabilities: nil
            )
        ]

        let labels = ReceiptWordLabel.fromLinePredictions(
            predictions: predictions,
            imageId: "img-1",
            receiptId: 1,
            modelLabels: ["AMOUNT", "ADDRESS", "MERCHANT_NAME", "DATE"]
        )

        XCTAssertEqual(labels.map(\.label), ["AMOUNT", "ADDRESS", "TIP"])
        XCTAssertEqual(labels.map(\.wordId), [1, 2, 4])
    }
}
