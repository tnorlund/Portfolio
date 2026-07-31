import Foundation

public let swiftWorkerModelSource = "swift-worker-v1"

/// JSON contract for one on-device section prediction.
public struct ReceiptSectionPayload: Codable, Sendable, Equatable {
    public let sectionType: String
    public let lineIds: [Int]
    public let rowIds: [Int]
    public let confidence: Double
    public let modelSource: String

    enum CodingKeys: String, CodingKey {
        case sectionType = "section_type"
        case lineIds = "line_ids"
        case rowIds = "row_ids"
        case confidence
        case modelSource = "model_source"
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
public func buildOnDeviceReceiptStructure(
    lines: [SectionLine], merchantName: String? = nil
) throws -> OnDeviceReceiptStructure {
    guard !lines.isEmpty else {
        return OnDeviceReceiptStructure(
            sections: [], lineItems: [], printedSubtotal: nil,
            reconciliationStatus: "no-baseline",
            shouldReocrItemsZone: false
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
    let sections = predictions.map { section in
        ReceiptSectionPayload(
            sectionType: section.sectionType,
            lineIds: section.lineIds,
            rowIds: section.rowIds,
            confidence: section.confidence,
            modelSource: swiftWorkerModelSource
        )
    }

    let itemsLineIds = Set(
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
    let decoded = itemsLineIds.isEmpty
        ? [] : extractLineItems(words: zoneWords, zoneLineIds: itemsLineIds)
    let printedSubtotal = findPrintedSubtotal(rows: rows, lines: lines)
    let reconciliation = reconcileLineItems(
        items: decoded.filter { !$0.isDiscount },
        subtotal: printedSubtotal, grandTotal: nil, tax: nil
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
            modelSource: swiftWorkerModelSource
        )
    }
    return OnDeviceReceiptStructure(
        sections: sections,
        lineItems: lineItems,
        printedSubtotal: printedSubtotal,
        reconciliationStatus: reconciliation.status,
        shouldReocrItemsZone: shouldReocrItemsZone(
            items: decoded, printedSubtotal: printedSubtotal
        )
    )
}

#if os(macOS)
/// Production adapter for refinement OCR. Empty lines/words are skipped to
/// mirror the cloud parser while their original 1-based IDs are preserved.
public func buildOnDeviceReceiptStructure(
    lines: [Line], receiptId: Int, merchantName: String? = nil
) throws -> OnDeviceReceiptStructure {
    let sectionLines = makeSectionLines(
        lines, imageId: "", receiptId: receiptId
    )
    return try buildOnDeviceReceiptStructure(
        lines: sectionLines, merchantName: merchantName
    )
}
#endif
