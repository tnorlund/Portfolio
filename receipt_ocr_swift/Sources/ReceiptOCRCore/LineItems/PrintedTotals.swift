import Foundation

/// Swift port of the label-free printed-total fallback (#1321):
/// - `receipt_dynamo/receipt_dynamo/amounts.py` (the shared summary-row
///   keyword vocabulary + `is_grand_total_line`)
/// - `receipt_dynamo/receipt_dynamo/entities/receipt_summary.py`
///   (`find_printed_grand_total`, `find_printed_subtotal`, and the anchored
///   row-band search they share)
///
/// This is the deterministic fallback for receipts whose GRAND_TOTAL /
/// SUBTOTAL labels are missing or attached to the wrong words. Sprouts is
/// the canonical case: it prints "Total:" / "BALANCE DUE" and "USD$ 42.54"
/// as separate OCR lines in the same visual row.
///
/// Named `PrintedTotals.*` rather than free functions because
/// `ReceiptStructurePipeline` already exposes a row-based
/// `findPrintedSubtotal(rows:lines:)` with different semantics.

/// The shared summary-line keyword patterns. These live beside the amount
/// lexer in Python (receipt_dynamo sits at the bottom of the dependency
/// graph) so both the amount classifier and the printed-total fallback can
/// use them without forking the regexes.
enum SummaryKeywordRegex {
    /// TOTAL_KEYWORD_RE
    static let total = Rx(
        "\\b(total|amount\\s+due|balance|authorized)\\b", ci: true
    )
    /// SUBTOTAL_KEYWORD_RE
    static let subtotal = Rx("\\bsub[-\\s]?total\\b", ci: true)
    /// TAX_KEYWORD_RE
    static let tax = Rx("\\b(tax|vat)\\b", ci: true)
    /// NON_PAYMENT_SUMMARY_RE
    static let nonPaymentSummary = Rx(
        "\\b(savings?|discounts?|refunds?|returns?|coupons?|promos?"
            + "|promotion|rewards?|loyalty|cash\\s+back|cashback"
            + "|store\\s+credit)\\b",
        ci: true
    )
    /// TENDER_KEYWORD_RE: settlement/tender vocabulary that collides with
    /// TOTAL_KEYWORD_RE ("Total Tender", "Amount Tendered", "Change Due").
    /// These rows record how the customer PAID -- tender can include tip
    /// and cash-given, change is money returned -- never what the receipt
    /// totals, so they must not anchor a printed grand total (Moody Market
    /// a8d7ab9f r4: "Total Tender 24.67" = total 21.45 + tips 3.22
    /// outranked the plain "Total 21.45" row).
    static let tender = Rx(
        "\\b(tender(?:ed)?|cash|change)\\b", ci: true
    )
    /// Splits a lowercased line into alpha tokens (Python `re.split`).
    static let nonAlphaRun = Rx("[^a-z]+")
}

/// GRAND_TOTAL_DISQUALIFIER_TOKENS: tokens that turn a "total" row into a
/// non-monetary count/summary row ("TOTAL NUMBER OF ITEMS SOLD").
public let grandTotalDisqualifierTokens: Set<String> = [
    "number", "items", "item", "qty", "quantity", "count", "sold",
    "transactions", "transaction", "pieces", "units", "lines", "savings",
    "saved",
]

/// Port of `amounts.is_grand_total_line`.
public func isGrandTotalLine(_ lineText: String) -> Bool {
    if lineText.isEmpty { return false }
    if !SummaryKeywordRegex.total.hasMatch(lineText) { return false }
    if SummaryKeywordRegex.subtotal.hasMatch(lineText) { return false }
    if SummaryKeywordRegex.tax.hasMatch(lineText) { return false }
    if SummaryKeywordRegex.nonPaymentSummary.hasMatch(lineText) {
        return false
    }
    let tokens = Set(
        SummaryKeywordRegex.nonAlphaRun
            .split(lineText.lowercased())
            .filter { !$0.isEmpty }
    )
    return tokens.isDisjoint(with: grandTotalDisqualifierTokens)
}

/// One word as the printed-total search sees it: text plus the normalized
/// bounding box top edge and height (the Python facade reads
/// `bounding_box["y"]` and `["height"]`).
public struct PrintedTotalWord: Sendable {
    public let lineId: Int
    public let wordId: Int
    public let text: String
    public let y: Double
    public let height: Double

    public init(
        lineId: Int, wordId: Int, text: String, y: Double, height: Double
    ) {
        self.lineId = lineId
        self.wordId = wordId
        self.text = text
        self.y = y
        self.height = height
    }

    public init(_ word: SectionWord) {
        self.init(
            lineId: word.lineId, wordId: word.wordId, text: word.text,
            y: word.boundingBox.y, height: word.boundingBox.height
        )
    }

    var yCenter: Double { y + height / 2.0 }
}

public enum PrintedTotals {
    /// `_MIN_ROW_BAND`
    static let minRowBand: Double = 0.005

    /// Port of `_positive_amount`: requires amount-like punctuation so bare
    /// integers such as store numbers never qualify.
    static func positiveAmount(_ text: String) -> Double? {
        guard Amounts.looksLikeReceiptAmount(text),
            let value = Amounts.parseReceiptAmount(text), value > 0
        else { return nil }
        return value
    }

    /// Port of `_is_summary_noise_line`.
    static func isSummaryNoiseLine(_ text: String) -> Bool {
        SummaryKeywordRegex.subtotal.hasMatch(text)
            || SummaryKeywordRegex.tax.hasMatch(text)
            || SummaryKeywordRegex.nonPaymentSummary.hasMatch(text)
            || SummaryKeywordRegex.tender.hasMatch(text)
    }

    /// Port of `_is_grand_total_anchor`: a total row that is not a tender
    /// row, so a plain "Total" always outranks "Total Tender".
    static func isGrandTotalAnchor(_ text: String) -> Bool {
        isGrandTotalLine(text) && !SummaryKeywordRegex.tender.hasMatch(text)
    }

    /// Port of `_is_subtotal_anchor`.
    static func isSubtotalAnchor(_ text: String) -> Bool {
        SummaryKeywordRegex.subtotal.hasMatch(text)
            && !SummaryKeywordRegex.nonPaymentSummary.hasMatch(text)
            && !SummaryKeywordRegex.tender.hasMatch(text)
    }

    /// Port of `_is_subtotal_noise_line`.
    static func isSubtotalNoiseLine(_ text: String) -> Bool {
        SummaryKeywordRegex.total.hasMatch(text)
            || SummaryKeywordRegex.tax.hasMatch(text)
            || SummaryKeywordRegex.nonPaymentSummary.hasMatch(text)
            || SummaryKeywordRegex.tender.hasMatch(text)
    }

    /// Port of `find_printed_grand_total`.
    public static func grandTotal(words: [PrintedTotalWord]) -> Double? {
        anchoredAmount(
            words: words, isAnchor: isGrandTotalAnchor,
            isNoise: isSummaryNoiseLine
        )
    }

    /// Port of `find_printed_subtotal`.
    public static func subtotal(words: [PrintedTotalWord]) -> Double? {
        anchoredAmount(
            words: words, isAnchor: isSubtotalAnchor,
            isNoise: isSubtotalNoiseLine
        )
    }

    /// Port of `_find_anchored_amount`: the largest amount printed on (or
    /// row-banded with) an anchor line. The Python result is `max(...)`, so
    /// the dict-iteration order it collects in is not observable.
    static func anchoredAmount(
        words: [PrintedTotalWord],
        isAnchor: (String) -> Bool,
        isNoise: (String) -> Bool
    ) -> Double? {
        var lines: [Int: [PrintedTotalWord]] = [:]
        for word in words { lines[word.lineId, default: []].append(word) }
        for key in lines.keys {
            lines[key] = lines[key]!.stableSorted { $0.wordId < $1.wordId }
        }
        let lineTexts = lines.mapValues {
            $0.map(\.text).joined(separator: " ")
        }

        let anchorIds = lineTexts.filter { isAnchor($0.value) }.map(\.key)
        if anchorIds.isEmpty { return nil }

        var anchored: [Double] = []
        for anchorId in anchorIds {
            let anchorWords = lines[anchorId]!

            // Amounts printed on the anchor line itself win outright.
            let sameLine = anchorWords.compactMap { positiveAmount($0.text) }
            if !sameLine.isEmpty {
                anchored.append(contentsOf: sameLine)
                continue
            }

            // Pair with amount words in the anchor's y-band on other lines.
            if anchorWords.isEmpty { continue }
            let anchorY =
                anchorWords.reduce(0.0) { $0 + $1.yCenter }
                / Double(anchorWords.count)
            let anchorHeight = anchorWords.map(\.height).max() ?? 0.0
            let band = max(0.6 * anchorHeight, minRowBand)

            for (lineId, lineWords) in lines {
                if lineId == anchorId { continue }
                if isNoise(lineTexts[lineId] ?? "") { continue }
                for word in lineWords {
                    if abs(word.yCenter - anchorY) > band { continue }
                    if let amount = positiveAmount(word.text) {
                        anchored.append(amount)
                    }
                }
            }
        }
        return anchored.max()
    }
}
