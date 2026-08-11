import Foundation

/// Which producer wrote a row. The cloud's stream stage stamps
/// `upload-determinism-v1`; anything starting with `swift-worker-` came from
/// a Mac worker build and is treated as pre-computed by the ingest handler.
public let swiftWorkerModelSource = "swift-worker-v1"

/// Version of the decode ALGORITHM, kept in lockstep with the canonical
/// Python decoder's `EXTRACTOR_VERSION`
/// (`infra/receipt_line_item_updater/line_item_processor.py`). Bump both
/// together: a mismatch between the two constants in production rows is the
/// signal that the Swift port has fallen behind again (Warning #1).
public let swiftWorkerDecoderVersion = "line-items-blocks-v2"

/// The stamp written to `ReceiptLineItem.extractor_version`. Worker build and
/// decoder version travel together in one queryable field so a divergent row
/// identifies both which producer wrote it and which algorithm it ran.
public let swiftWorkerExtractorVersion =
    "\(swiftWorkerModelSource)+\(swiftWorkerDecoderVersion)"

/// The prefix every worker-written `extractor_version` shares, whatever the
/// build or decoder version. Cloud-written rows carry the bare decoder
/// version (`line-items-blocks-v2`), so a `begins_with` test on this prefix
/// distinguishes "the worker owns this row" from "the cloud owns this row".
public let swiftWorkerExtractorVersionPrefix = "swift-worker"

/// JSON contract for one on-device section prediction.
public struct ReceiptSectionPayload: Codable, Sendable, Equatable {
    public let sectionType: String
    public let lineIds: [Int]
    public let rowIds: [Int]
    public let confidence: Double
    public let modelSource: String
    public let extractorVersion: String

    enum CodingKeys: String, CodingKey {
        case sectionType = "section_type"
        case lineIds = "line_ids"
        case rowIds = "row_ids"
        case confidence
        case modelSource = "model_source"
        case extractorVersion = "extractor_version"
    }
}

/// JSON contract for one on-device line item.
public struct ReceiptLineItemPayload: Codable, Sendable, Equatable {
    public let itemIndex: Int
    public let name: String
    public let price: Double
    public let quantity: Double?
    public let unitPrice: Double?
    public let isDiscount: Bool
    public let nameQuality: String
    public let lineIds: [Int]
    public let reconciliationStatus: String
    public let modelSource: String
    public let extractorVersion: String
    /// The band text the item was parsed from (`ReceiptLineItem.raw_text`).
    /// Optional so payloads from earlier worker builds still decode.
    public let rawText: String?

    public init(
        itemIndex: Int, name: String, price: Double, quantity: Double?,
        unitPrice: Double?, isDiscount: Bool, nameQuality: String,
        lineIds: [Int], reconciliationStatus: String, modelSource: String,
        extractorVersion: String, rawText: String? = nil
    ) {
        self.itemIndex = itemIndex
        self.name = name
        self.price = price
        self.quantity = quantity
        self.unitPrice = unitPrice
        self.isDiscount = isDiscount
        self.nameQuality = nameQuality
        self.lineIds = lineIds
        self.reconciliationStatus = reconciliationStatus
        self.modelSource = modelSource
        self.extractorVersion = extractorVersion
        self.rawText = rawText
    }

    enum CodingKeys: String, CodingKey {
        case itemIndex = "item_index"
        case name
        case price
        case quantity
        case unitPrice = "unit_price"
        case isDiscount = "is_discount"
        case nameQuality = "name_quality"
        case lineIds = "line_ids"
        case reconciliationStatus = "reconciliation_status"
        case modelSource = "model_source"
        case extractorVersion = "extractor_version"
        case rawText = "raw_text"
    }
}

/// JSON contract for the receipt-level reconciliation verdict: the #1324
/// graded result the worker always computed but never shipped.
public struct ReceiptReconciliationPayload: Codable, Sendable, Equatable {
    public let status: String
    public let itemSum: Double?
    public let baseline: Double?
    public let baselineSource: String?
    public let baselineFiguresAgreeing: Int?

    public init(_ result: ReconcileResult) {
        self.status = result.status
        self.itemSum = result.itemSum
        self.baseline = result.baseline
        self.baselineSource = result.baselineSource
        self.baselineFiguresAgreeing = result.baselineFiguresAgreeing
    }

    enum CodingKeys: String, CodingKey {
        case status
        case itemSum = "item_sum"
        case baseline
        case baselineSource = "baseline_source"
        case baselineFiguresAgreeing = "baseline_figures_agreeing"
    }
}

/// Full deterministic structure produced after refinement OCR on a warped
/// receipt. Re-OCR remains a separate worker job; this result exposes the
/// already-ported decision so callers can route it without recomputing.
public struct OnDeviceReceiptStructure: Sendable, Equatable {
    public let sections: [ReceiptSectionPayload]
    public let lineItems: [ReceiptLineItemPayload]
    public let printedSubtotal: Double?
    public let reconciliationStatus: String
    public let shouldReocrItemsZone: Bool
    /// The full graded verdict behind `reconciliationStatus`.
    public let reconciliation: ReceiptReconciliationPayload
}

private enum ReceiptStructureRegex {
    static let subtotal = Rx("\\bSUB\\s*TOTAL\\b", ci: true)
}

private let cachedSectionPriorModel: Result<SectionPriorModel, Error> = Result {
    try loadSectionPriorModel()
}

/// Recover the merchant text already predicted by on-device LayoutLM so the
/// assigner can select its measured merchant prior when one exists.
public func merchantNameFromLayoutPredictions(
    _ predictions: [LinePrediction]?
) -> String? {
    guard let predictions else { return nil }
    let tokens = predictions.flatMap { prediction in
        zip(prediction.tokens, prediction.labels).compactMap { token, label in
            label.uppercased().hasSuffix("MERCHANT_NAME") && !token.isEmpty
                ? token : nil
        }
    }
    let name = tokens.joined(separator: " ")
        .trimmingCharacters(in: .whitespacesAndNewlines)
    return name.isEmpty ? nil : name
}

private func textForRow(
    _ row: ReceiptVisualRow, linesById: [Int: SectionLine]
) -> String {
    row.lineIds.compactMap { linesById[$0]?.text }.joined(separator: " ")
}

/// Find the receipt's own printed subtotal from visual rows. The aligned
/// row-builder amount wins; a rightmost word-level amount is the fallback for
/// receipts whose amount column is too sparse to detect.
public func findPrintedSubtotal(
    rows: [ReceiptVisualRow], lines: [SectionLine]
) -> Double? {
    let linesById = Dictionary(uniqueKeysWithValues: lines.map {
        ($0.lineId, $0)
    })
    for row in rows {
        let text = textForRow(row, linesById: linesById)
        guard ReceiptStructureRegex.subtotal.search(text) != nil else {
            continue
        }
        if let amount = Amounts.parseReceiptAmount(row.amountText), amount > 0 {
            return amount
        }
        let rowIds = Set(row.lineIds)
        let candidates = lines.filter { rowIds.contains($0.lineId) }
            .flatMap(\.words)
            .filter { Amounts.looksLikeReceiptAmount($0.text) }
            .stableSorted {
                if $0.boundingBox.x != $1.boundingBox.x {
                    return $0.boundingBox.x < $1.boundingBox.x
                }
                if $0.lineId != $1.lineId { return $0.lineId < $1.lineId }
                return $0.wordId < $1.wordId
            }
        for word in candidates.reversed() {
            if let amount = Amounts.parseReceiptAmount(word.text), amount > 0 {
                return amount
            }
        }
    }
    return nil
}

/// Run rows -> sections -> line items -> subtotal reconciliation.
///
/// `summaryOverride` (LINE_ITEM_REFINE) substitutes the REAL summary for
/// the worker's own scanned figures: boundary extension, the
/// summary-figure filter and the graded reconciliation all run against
/// it. `printedSubtotal` on the result still reports the scanned figure.
public func buildOnDeviceReceiptStructure(
    lines: [SectionLine], merchantName: String? = nil,
    summaryOverride: LineItemSummary? = nil
) throws -> OnDeviceReceiptStructure {
    guard !lines.isEmpty else {
        let empty = reconcileLineItemsDetailed(items: [], summary: nil)
        return OnDeviceReceiptStructure(
            sections: [], lineItems: [], printedSubtotal: nil,
            reconciliationStatus: "no-baseline",
            shouldReocrItemsZone: false,
            reconciliation: ReceiptReconciliationPayload(empty)
        )
    }
    let rows = buildReceiptRows(lines: lines)
    let model = try cachedSectionPriorModel.get()
    let predictions = sectionsFromAssignments(
        assignRowSections(
            rows: rows, lines: lines, model: model,
            merchantName: merchantName
        )
    )
    var sections = predictions.map { section in
        ReceiptSectionPayload(
            sectionType: section.sectionType,
            lineIds: section.lineIds,
            rowIds: section.rowIds,
            confidence: section.confidence,
            modelSource: swiftWorkerModelSource,
            extractorVersion: swiftWorkerExtractorVersion
        )
    }

    var itemsLineIds = Set(
        sections.first { $0.sectionType == "ITEMS" }?.lineIds ?? []
    )
    let zoneWords = lines.flatMap(\.words).map { word in
        ZoneWord(
            lineId: word.lineId,
            wordId: word.wordId,
            text: word.text,
            x: word.boundingBox.x,
            yMid: word.boundingBox.y + word.boundingBox.height / 2,
            h: word.boundingBox.height
        )
    }
    let printedSubtotal = findPrintedSubtotal(rows: rows, lines: lines)
    // A receipt that prints no SUBTOTAL still prints a TOTAL, and until
    // now the worker ignored it: `PrintedTotals` (the #1321 port of
    // find_printed_grand_total) shipped as dead code, so subtotal-less
    // receipts died as no-baseline even with a total on the paper.
    // Trader Joe's is the canonical case -- "TOTAL PURCHASE $37.51" and
    // "Balance to pay $37.51", no subtotal anywhere.
    //
    // The subtotal still WINS whenever one is printed (reconcile prefers
    // it and only falls back to grand_total - tax), so this can never
    // demote an existing match/near verdict; it only gives the
    // subtotal-less receipts a baseline they never had. The anchor search
    // itself already refuses tender rows, so #1349 ("Total Tender" must
    // never outrank a plain "Total") is unaffected.
    let printedGrandTotal = PrintedTotals.grandTotal(
        words: lines.flatMap(\.words).map(PrintedTotalWord.init)
    )
    // Always construct the summary: an all-nil summary reconciles to
    // no-baseline exactly as nil did, and the summary-figure filter is a
    // no-op without figures, so subtotal-less receipts are unaffected.
    let summary = summaryOverride
        ?? LineItemSummary(
            subtotal: printedSubtotal, tax: nil,
            grandTotal: printedGrandTotal
        )

    // Zone-gap boundary extension (#1329), previously cloud-only: the
    // proposal is accepted only when the arithmetic strictly improves
    // (smaller |delta| AND better status), so against an already-matching
    // zone it always returns nil. The widened ITEMS section ships in the
    // payload, mirroring how the cloud path persists its own extension.
    if !itemsLineIds.isEmpty,
        let proposal = proposeItemsBoundaryExtension(
            words: zoneWords,
            summary: summary,
            currentLineIds: itemsLineIds,
            sections: sections.map(BoundarySection.init),
            rows: rows.map(BoundaryRow.init),
            currentRowIds: sections.first { $0.sectionType == "ITEMS" }?
                .rowIds
        )
    {
        itemsLineIds = Set(proposal.lineIds)
        sections = sections.map { section in
            guard section.sectionType == "ITEMS" else { return section }
            return ReceiptSectionPayload(
                sectionType: section.sectionType,
                lineIds: proposal.lineIds,
                rowIds: proposal.rowIds ?? section.rowIds,
                confidence: section.confidence,
                modelSource: section.modelSource,
                extractorVersion: section.extractorVersion
            )
        }
    }

    // Decode WITH the scanned summary so the summary-figure filter
    // (#1320) runs on device exactly as it does in the cloud recompute.
    let decoded = itemsLineIds.isEmpty
        ? []
        : extractLineItems(
            words: zoneWords, zoneLineIds: itemsLineIds, summary: summary
        )
    let reconciliation = reconcileLineItemsDetailed(
        items: decoded.filter { !$0.isDiscount }, summary: summary
    )
    let lineItems = decoded.enumerated().map { index, item in
        ReceiptLineItemPayload(
            itemIndex: index,
            name: item.name,
            price: item.price,
            quantity: item.quantity,
            unitPrice: item.unitPrice,
            isDiscount: item.isDiscount,
            nameQuality: item.nameQuality ?? "ok",
            lineIds: item.lineIds,
            reconciliationStatus: reconciliation.status,
            modelSource: swiftWorkerModelSource,
            extractorVersion: swiftWorkerExtractorVersion,
            rawText: item.rawText
        )
    }
    return OnDeviceReceiptStructure(
        sections: sections,
        lineItems: lineItems,
        printedSubtotal: printedSubtotal,
        reconciliationStatus: reconciliation.status,
        shouldReocrItemsZone: shouldReocrItemsZone(
            items: decoded, printedSubtotal: printedSubtotal
        ),
        reconciliation: ReceiptReconciliationPayload(reconciliation)
    )
}

#if os(macOS)
/// Production adapter for refinement OCR. Empty lines/words are skipped to
/// mirror the cloud parser while their original 1-based IDs are preserved.
public func buildOnDeviceReceiptStructure(
    lines: [Line], receiptId: Int, merchantName: String? = nil,
    summaryOverride: LineItemSummary? = nil
) throws -> OnDeviceReceiptStructure {
    let sectionLines = makeSectionLines(
        lines, imageId: "", receiptId: receiptId
    )
    return try buildOnDeviceReceiptStructure(
        lines: sectionLines, merchantName: merchantName,
        summaryOverride: summaryOverride
    )
}
#endif
