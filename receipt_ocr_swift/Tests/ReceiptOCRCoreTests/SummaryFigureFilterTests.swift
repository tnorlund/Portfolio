import Foundation
import Testing

@testable import ReceiptOCRCore

/// Direct ports of the Python summary-figure-filter and fused-tax-flag
/// tests in `receipt_upload/tests/test_line_item_geometry.py`. The guard
/// parity fixture only exercises golden receipts, none of which hit the
/// single-item exception or a fused flag, so these shapes are pinned
/// here with the same words and the same expected outcomes as Python.
@Suite struct SummaryFigureFilterTests {

    /// One visual row: tokens laid out left to right (Python `row`).
    private func row(
        _ lineId: Int, _ y: Double, _ tokens: String...
    ) -> [ZoneWord] {
        tokens.enumerated().map { i, t in
            ZoneWord(
                lineId: lineId, wordId: i + 1, text: t,
                x: 0.1 + 0.2 * Double(i), yMid: y, h: 0.02
            )
        }
    }

    @Test func fusedTaxFlagAmountIsAPrice() {
        // CVS prints "7.32N" with no currency symbol; without the strip
        // its every line price was invisible to the decoder.
        let items = extractLineItems(
            words: row(1, 0.30, "ADVIL", "TABLETS", "7.32N"),
            zoneLineIds: [1]
        )
        #expect(items.count == 1)
        #expect(items.first?.name == "ADVIL TABLETS")
        #expect(items.first?.price == 7.32)
    }

    @Test func trailingLetterIsNotAlwaysATaxFlag() {
        // "14.10Z" is a 14.1-OUNCE product size on Home Depot's
        // BERNZOMATIC row, not $14.10. The flag alphabet stays [TFNOAB].
        let items = extractLineItems(
            words: row(1, 0.30, "BERNZOMATIC", "MAP-PRO", "FUEL", "14.10Z"),
            zoneLineIds: [1]
        )
        #expect(items.isEmpty)
    }

    @Test func originalPriceEchoIsNeverAnItem() {
        // CVS's "ORIGINAL PRICE 49.99" is the same pre-discount echo
        // Target prints as "Regular Price"; it must not decode as a
        // phantom item beside the real one.
        let items = extractLineItems(
            words: row(1, 0.30, "PLAN", "B", "ONE", "STEP", "49.89")
                + row(2, 0.25, "ORIGINAL", "PRICE", "49.99"),
            zoneLineIds: [1, 2]
        )
        #expect(items.map(\.price) == [49.89])
    }

    @Test func namelessSummaryBandDroppedBelowTwoSurvivors() {
        // The self-verifying exception to the two-survivor guard: a band
        // with NO name text, whose removal leaves all-named items that
        // then reconcile EXACTLY (CVS d04dea42 prints its subtotal's
        // amount one OCR line above the "SUBTOTAL" label).
        let items = extractLineItems(
            words: row(1, 0.30, "PLAN", "B", "ONE", "STEP", "49.89")
                + row(2, 0.25, "49.89"),
            zoneLineIds: [1, 2],
            summary: LineItemSummary(subtotal: 49.89, grandTotal: 53.63)
        )
        #expect(items.map(\.price) == [49.89])
        #expect(items.first?.name.contains("PLAN") == true)
    }

    @Test func exceptionNeedsATextedSurvivor() {
        // Stricter than nameIsReal on purpose: CVS 2c9b770c's real item
        // is "RX #: ****2130000" -- not a real NAME but not a bare
        // amount either -- and it must be the survivor when the
        // "20.00 20.00" tender pair drops.
        let items = extractLineItems(
            words: row(1, 0.30, "RX", "#:", "****2130000", "20.00")
                + row(2, 0.25, "20.00", "20.00"),
            zoneLineIds: [1, 2],
            summary: LineItemSummary(
                subtotal: 20.00, tax: 0.0, grandTotal: 20.00
            )
        )
        #expect(items.count == 1)
        #expect(items.first?.name == "RX #: ****2130000")
        #expect(items.first?.price == 20.0)
    }

    @Test func twoSurvivorGuardStillHoldsForTextedBands() {
        // The In-N-Out shape: the candidate band carries name text, so
        // the exception never fires and both items survive.
        let items = extractLineItems(
            words: row(1, 0.30, "2", "Animal", "Fry", "9.20")
                + row(2, 0.25, "DRIVE", "Eat", "In", "9.20"),
            zoneLineIds: [1, 2],
            summary: LineItemSummary(subtotal: 9.2, grandTotal: 9.97)
        )
        #expect(items.map(\.price).sorted() == [9.2, 9.2])
    }
}
