import Foundation

/// Swift port of the ITEMS zone-gap boundary extension (#1329) from
/// `receipt_upload/receipt_upload/line_items/geometry.py`:
/// `evaluate_items_zone`, `items_boundary_extension_guard`,
/// `_is_non_product_row`, `_is_priced_product_row` and
/// `propose_items_boundary_extension`.
///
/// This is the self-repair half of the decoder: when a receipt's ITEMS
/// section stops short of a printed product row, the arithmetic (not a
/// heuristic) decides whether pulling the adjacent row in is correct. Every
/// proposal must strictly shrink |delta| AND improve the reconciliation
/// status, so an extension can never be accepted on a hunch.

/// One decoded-and-reconciled ITEMS zone (Python `evaluate_items_zone`).
public struct ItemsZoneEvaluation: Sendable, Equatable {
    public let status: String
    public let itemsSum: Double?
    public let baseline: Double?
    public let delta: Double?
    public let nItems: Int
    /// The Python decoder always reports False here since the band-block
    /// swap; kept so the payload shape does not drift.
    public let collapsedBanding: Bool
}

/// Reconciliation rank for the boundary repair guard. `no-baseline` is
/// deliberately absent: an extension cannot be arithmetic-verified when
/// either side has no comparable baseline.
let boundaryReconRank: [String: Int] = [
    "match": 0, "near": 1, "mismatch": 2,
]

/// Port of `geometry.evaluate_items_zone`. Discounts are excluded from the
/// arithmetic, matching every canonical line-item writer.
public func evaluateItemsZone(
    words: [ZoneWord], summary: LineItemSummary?, lineIds: Set<Int>
) -> ItemsZoneEvaluation {
    let items = extractLineItems(
        words: words, zoneLineIds: lineIds, summary: summary
    )
    let result = reconcileLineItemsDetailed(
        items: items.filter { !$0.isDiscount }, summary: summary
    )
    var delta: Double?
    if let sum = result.itemSum, let base = result.baseline {
        delta = pythonRound2(sum - base)
    }
    return ItemsZoneEvaluation(
        status: result.status,
        itemsSum: result.itemSum,
        baseline: result.baseline,
        delta: delta,
        nItems: items.count,
        collapsedBanding: false
    )
}

/// Port of `geometry.items_boundary_extension_guard`: acceptance requires
/// both a strictly smaller absolute delta and a better reconciliation
/// status (mismatch -> near/match, or near -> match).
public func itemsBoundaryExtensionGuard(
    before: ItemsZoneEvaluation, after: ItemsZoneEvaluation
) -> (verified: Bool, reason: String?) {
    if before.status == "match" {
        return (
            false,
            "Current ITEMS zone already reconciles (match); nothing to repair."
        )
    }
    guard let beforeRank = boundaryReconRank[before.status],
        let afterRank = boundaryReconRank[after.status],
        let beforeDelta = before.delta,
        let afterDelta = after.delta
    else {
        return (
            false,
            "Cannot verify the extension: reconciliation did not produce "
                + "comparable deltas for both zones."
        )
    }
    let shrinks = abs(afterDelta) < abs(beforeDelta)
    let improves = afterRank < beforeRank
    if !(shrinks && improves) {
        return (
            false,
            "Arithmetic guard failed: extension must strictly shrink |delta| "
                + "(before \(beforeDelta), after \(afterDelta)) AND improve "
                + "status (before \(before.status), after \(after.status))."
        )
    }
    return (true, nil)
}

/// Port of `geometry._is_non_product_row`: whether the decoder structurally
/// recognizes a settlement / price-comparison / annotation row.
public func isNonProductRow(_ rowWords: [ZoneWord]) -> Bool {
    for band in bandWords(rowWords) {
        let text = band.map(\.text).joined(separator: " ")
        let bare = LineItemRegex.amountStrip.sub(text, with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if isSettlementRow(bare)
            || LineItemRegex.wasPrice.hasMatch(text)
            || LineItemRegex.salePrice.hasMatch(text)
            || LineItemRegex.nonProductNote.hasMatch(text)
        {
            return true
        }
    }
    return false
}

/// Port of `geometry._is_priced_product_row`.
public func isPricedProductRow(_ rowWords: [ZoneWord]) -> Bool {
    if isNonProductRow(rowWords) { return false }
    for band in bandWords(rowWords) {
        if let parsed = parseBand(band), parsed.price != 0 { return true }
    }
    return false
}

/// A whole visual row considered as an extension candidate.
public struct BoundaryRow: Sendable {
    public let rowId: Int
    public let lineIds: [Int]
    /// Row top edge; the Python code falls back to the minimum word y_mid
    /// when a row carries no y_min.
    public let yMin: Double?

    public init(rowId: Int, lineIds: [Int], yMin: Double? = nil) {
        self.rowId = rowId
        self.lineIds = lineIds
        self.yMin = yMin
    }

    public init(_ row: ReceiptVisualRow) {
        self.init(rowId: row.rowId, lineIds: row.lineIds, yMin: row.yMin)
    }
}

/// A section, for the "claimed by someone else" test.
public struct BoundarySection: Sendable {
    public let sectionType: String
    public let lineIds: [Int]

    public init(sectionType: String, lineIds: [Int]) {
        self.sectionType = sectionType
        self.lineIds = lineIds
    }

    public init(_ section: ReceiptSectionPayload) {
        self.init(
            sectionType: section.sectionType, lineIds: section.lineIds
        )
    }
}

/// A reconciliation-verified extension of the ITEMS zone.
public struct ItemsBoundaryProposal: Sendable, Equatable {
    public let lineIds: [Int]
    public let addedLineIds: [Int]
    public let addedRowIds: [Int]
    public let rowIds: [Int]?
    public let before: ItemsZoneEvaluation
    public let after: ItemsZoneEvaluation
}

private struct VisualRow {
    let rowId: Int
    let lineIds: Set<Int>
    let words: [ZoneWord]
    let yMin: Double
}

/// Port of `geometry.propose_items_boundary_extension`.
///
/// Only whole, unclaimed, priced rows in gaps inside the ITEMS span or
/// adjacent to either edge are candidates. Edge candidates are contiguous
/// prefixes of the unclaimed zone (neutral barcode/SKU rows may separate
/// printed product rows); claimed or settlement rows terminate the scan.
/// Among verified proposals, prefer the best status, then the smallest
/// absolute delta, then the smallest boundary change.
public func proposeItemsBoundaryExtension(
    words: [ZoneWord],
    summary: LineItemSummary?,
    currentLineIds: Set<Int>,
    sections: [BoundarySection],
    rows: [BoundaryRow],
    currentRowIds: [Int]? = nil
) -> ItemsBoundaryProposal? {
    let current = currentLineIds
    if words.isEmpty || current.isEmpty || rows.isEmpty { return nil }

    var otherClaimed: Set<Int> = []
    for section in sections where section.sectionType.uppercased() != "ITEMS" {
        otherClaimed.formUnion(section.lineIds)
    }

    var wordsByLine: [Int: [ZoneWord]] = [:]
    for word in words { wordsByLine[word.lineId, default: []].append(word) }

    var visualRows: [VisualRow] = []
    for row in rows {
        let lineIds = Set(row.lineIds)
        let rowWords = row.lineIds.flatMap { wordsByLine[$0] ?? [] }
        if lineIds.isEmpty || rowWords.isEmpty { continue }
        let yMin = row.yMin ?? (rowWords.map(\.yMid).min() ?? 0.0)
        visualRows.append(
            VisualRow(
                rowId: row.rowId, lineIds: lineIds, words: rowWords,
                yMin: yMin
            )
        )
    }
    visualRows = visualRows.stableSorted {
        $0.yMin != $1.yMin ? $0.yMin < $1.yMin : $0.rowId < $1.rowId
    }

    let itemIndexes = visualRows.indices.filter {
        !visualRows[$0].lineIds.isDisjoint(with: current)
    }
    if itemIndexes.isEmpty { return nil }

    let claimed = current.union(otherClaimed)
    func adjacentChain(_ indexes: [Int]) -> [VisualRow] {
        var chain: [VisualRow] = []
        for index in indexes {
            let row = visualRows[index]
            if !row.lineIds.isDisjoint(with: claimed) { break }
            if isNonProductRow(row.words) { break }
            if isPricedProductRow(row.words) { chain.append(row) }
        }
        return chain
    }

    let first = itemIndexes.min()!
    let last = itemIndexes.max()!
    let interior = visualRows[first...last].filter {
        $0.lineIds.isDisjoint(with: claimed) && isPricedProductRow($0.words)
    }
    let above = adjacentChain(Array(stride(from: first - 1, through: 0, by: -1)))
    let below = adjacentChain(Array((last + 1)..<visualRows.count))
    if interior.isEmpty && above.isEmpty && below.isEmpty { return nil }

    let before = evaluateItemsZone(
        words: words, summary: summary, lineIds: current
    )
    var proposals: [ItemsBoundaryProposal] = []

    func recordProposal(_ addedRows: [VisualRow]) {
        let addedLineIds = addedRows.reduce(into: Set<Int>()) {
            $0.formUnion($1.lineIds)
        }
        let proposedLineIds = current.union(addedLineIds)
        let after = evaluateItemsZone(
            words: words, summary: summary, lineIds: proposedLineIds
        )
        guard itemsBoundaryExtensionGuard(before: before, after: after).verified
        else { return }
        let sortedAdded = addedLineIds.sorted()
        if proposals.contains(where: { $0.addedLineIds == sortedAdded }) {
            return
        }
        proposals.append(
            ItemsBoundaryProposal(
                lineIds: proposedLineIds.sorted(),
                addedLineIds: sortedAdded,
                addedRowIds: addedRows.map(\.rowId).sorted(),
                rowIds: currentRowIds.map {
                    Set($0).union(addedRows.map(\.rowId)).sorted()
                },
                before: before,
                after: after
            )
        )
    }

    // Whole-zone proposals preserve every priced row inside the current span
    // and grow outward only as contiguous edge prefixes.
    for aboveCount in 0...above.count {
        for belowCount in 0...below.count {
            if interior.isEmpty && aboveCount == 0 && belowCount == 0 {
                continue
            }
            recordProposal(
                interior + above.prefix(aboveCount) + below.prefix(belowCount)
            )
        }
    }

    // Some OCR sections have several independent internal holes. Follow the
    // arithmetic downhill one row at a time, but never persist an
    // intermediate mismatch: only states passing the strict guard are
    // recorded. Edge availability stays prefix-based so the search cannot
    // jump over a nearer priced row.
    var selected: [VisualRow] = []
    var remainingInterior = interior
    var aboveCount = 0
    var belowCount = 0
    var currentEvaluation = before
    while let currentDelta = currentEvaluation.delta {
        var available = remainingInterior
        if aboveCount < above.count { available.append(above[aboveCount]) }
        if belowCount < below.count { available.append(below[belowCount]) }

        var downhill: [(ItemsZoneEvaluation, Int)] = []
        for (offset, row) in available.enumerated() {
            let candidateRows = selected + [row]
            var candidateLineIds = current
            for candidate in candidateRows {
                candidateLineIds.formUnion(candidate.lineIds)
            }
            let evaluation = evaluateItemsZone(
                words: words, summary: summary, lineIds: candidateLineIds
            )
            if let delta = evaluation.delta, abs(delta) < abs(currentDelta) {
                downhill.append((evaluation, offset))
            }
        }
        if downhill.isEmpty { break }
        // Python `min` over (abs(delta), rank, row_id) — ties keep the first
        // candidate in `available` order, which the offset tiebreak
        // reproduces.
        let best = downhill.min { lhs, rhs in
            let l = (
                abs(lhs.0.delta!),
                boundaryReconRank[lhs.0.status] ?? 99,
                available[lhs.1].rowId, lhs.1
            )
            let r = (
                abs(rhs.0.delta!),
                boundaryReconRank[rhs.0.status] ?? 99,
                available[rhs.1].rowId, rhs.1
            )
            return l < r
        }!
        currentEvaluation = best.0
        let chosenOffset = best.1
        let chosen = available[chosenOffset]

        selected.append(chosen)
        if let idx = remainingInterior.firstIndex(where: {
            $0.rowId == chosen.rowId
        }) {
            remainingInterior.remove(at: idx)
        } else if aboveCount < above.count,
            above[aboveCount].rowId == chosen.rowId
        {
            aboveCount += 1
        } else if belowCount < below.count,
            below[belowCount].rowId == chosen.rowId
        {
            belowCount += 1
        }
        recordProposal(selected)
        if currentEvaluation.status == "match" { break }
    }

    if proposals.isEmpty { return nil }
    return proposals.min { lhs, rhs in
        let l = (
            boundaryReconRank[lhs.after.status] ?? 99,
            abs(lhs.after.delta ?? 0), lhs.addedLineIds.count
        )
        let r = (
            boundaryReconRank[rhs.after.status] ?? 99,
            abs(rhs.after.delta ?? 0), rhs.addedLineIds.count
        )
        if l != r { return l < r }
        return lhs.addedLineIds.lexicographicallyPrecedes(rhs.addedLineIds)
    }
}
