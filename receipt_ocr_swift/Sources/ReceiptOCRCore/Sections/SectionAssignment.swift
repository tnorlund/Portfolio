import Foundation

/// Swift port of the deterministic section assignment pipeline:
///
/// - `receipt_chroma.embedding.formatting.receipt_rows.build_receipt_rows`
/// - `receipt_upload.section_assignment.extract_row_features`
/// - `receipt_upload.section_assignment.assign_row_sections`
/// - `receipt_upload.section_assignment.sections_from_assignments`
///
/// The implementation deliberately preserves Python iteration and stable-sort
/// order because those orders participate in floating-point score ties.

// MARK: - OCR geometry consumed by the row builder

public struct SectionRect: Sendable {
    public let x: Double
    public let y: Double
    public let width: Double
    public let height: Double

    public init(x: Double, y: Double, width: Double, height: Double) {
        self.x = x
        self.y = y
        self.width = width
        self.height = height
    }
}

public struct SectionWord: Sendable {
    public let lineId: Int
    public let wordId: Int
    public let text: String
    public let boundingBox: SectionRect

    public init(
        lineId: Int, wordId: Int, text: String, boundingBox: SectionRect
    ) {
        self.lineId = lineId
        self.wordId = wordId
        self.text = text
        self.boundingBox = boundingBox
    }
}

public struct SectionLine: Sendable {
    public let imageId: String
    public let receiptId: Int
    public let lineId: Int
    public let text: String
    public let boundingBox: SectionRect
    public let words: [SectionWord]

    public init(
        imageId: String, receiptId: Int, lineId: Int, text: String,
        boundingBox: SectionRect, words: [SectionWord]
    ) {
        self.imageId = imageId
        self.receiptId = receiptId
        self.lineId = lineId
        self.text = text
        self.boundingBox = boundingBox
        self.words = words
    }
}

public struct ReceiptVisualRow: Sendable, Equatable {
    public let imageId: String
    public let receiptId: Int
    public let rowId: Int
    public let lineIds: [Int]
    public let yMin: Double
    public let yMax: Double
    public let xMin: Double
    public let xMax: Double
    public let priceColumnX: Double?
    public let labelText: String?
    public let amountText: String?
    public let amountLineId: Int?
    public let amountWordId: Int?

    public init(
        imageId: String, receiptId: Int, rowId: Int, lineIds: [Int],
        yMin: Double, yMax: Double, xMin: Double, xMax: Double,
        priceColumnX: Double? = nil, labelText: String? = nil,
        amountText: String? = nil, amountLineId: Int? = nil,
        amountWordId: Int? = nil
    ) {
        self.imageId = imageId
        self.receiptId = receiptId
        self.rowId = rowId
        self.lineIds = lineIds
        self.yMin = yMin
        self.yMax = yMax
        self.xMin = xMin
        self.xMax = xMax
        self.priceColumnX = priceColumnX
        self.labelText = labelText
        self.amountText = amountText
        self.amountLineId = amountLineId
        self.amountWordId = amountWordId
    }
}

private struct PriceColumn {
    let x: Double
    let tolerance: Double
}

private struct LabelAmountPair {
    let labelText: String
    let amountText: String
    let amountLineId: Int
    let amountWordId: Int
}

private enum SectionRegex {
    static let amount = Rx(
        "^(?:\\()?[-+]?\\$?(?:\\d+|\\d{1,3}(?:,\\d{3})+)\\.\\d{2}-?(?:\\))?$"
    )
    static let token = Rx("[a-z]+|\\d+(?:\\.\\d+)?")
    static let quantity = Rx(
        "(?:\\b\\d+\\s*(?:@|x)\\s*\\$?\\d)|(?:\\b(?:each|ea)\\b)|(?:/\\s*lb\\b)",
        ci: true
    )
    static let merchant = Rx("[a-z0-9]+")
}

private func median(_ values: [Double]) -> Double {
    precondition(!values.isEmpty)
    let ordered = values.sorted()
    let middle = ordered.count / 2
    if ordered.count.isMultiple(of: 2) {
        return (ordered[middle - 1] + ordered[middle]) / 2
    }
    return ordered[middle]
}

private func isAmountText(_ text: String) -> Bool {
    let compact = text.trimmingCharacters(in: .whitespacesAndNewlines)
        .replacingOccurrences(of: " ", with: "")
    return SectionRegex.amount.fullMatch(compact) != nil
}

private func rightEdge(_ word: SectionWord) -> Double {
    word.boundingBox.x + word.boundingBox.width
}

private func characterWidth(_ word: SectionWord) -> Double {
    let compact = word.text.trimmingCharacters(in: .whitespacesAndNewlines)
        .replacingOccurrences(of: " ", with: "")
    return word.boundingBox.width / Double(max(compact.count, 1))
}

private func detectPriceColumn(_ words: [SectionWord]) -> PriceColumn? {
    let amounts = words.filter { isAmountText($0.text) }
    guard !amounts.isEmpty else { return nil }

    let widths = amounts.map(characterWidth).filter { $0 != 0 }
    let tolerance = widths.isEmpty ? 0 : median(widths)
    let ordered = amounts.stableSorted { rightEdge($0) < rightEdge($1) }
    var clusters: [[SectionWord]] = []
    for word in ordered {
        if clusters.isEmpty
            || rightEdge(word) - rightEdge(clusters[clusters.count - 1].last!)
                > tolerance
        {
            clusters.append([word])
        } else {
            clusters[clusters.count - 1].append(word)
        }
    }

    func key(_ cluster: [SectionWord]) -> (Int, Int, Double) {
        (
            Set(cluster.map(\.lineId)).count,
            cluster.count,
            median(cluster.map(rightEdge))
        )
    }
    var winner = clusters[0]
    for cluster in clusters.dropFirst() {
        let lhs = key(cluster)
        let rhs = key(winner)
        if lhs.0 > rhs.0
            || (lhs.0 == rhs.0 && lhs.1 > rhs.1)
            || (lhs.0 == rhs.0 && lhs.1 == rhs.1 && lhs.2 > rhs.2)
        {
            winner = cluster
        }
    }
    return PriceColumn(
        x: median(winner.map(rightEdge)), tolerance: tolerance
    )
}

private func pairRowLabelAmount(
    _ row: [SectionLine], words: [SectionWord], priceColumn: PriceColumn?
) -> LabelAmountPair? {
    guard !row.isEmpty, let priceColumn else { return nil }
    let lineIds = Set(row.map(\.lineId))
    let rowWords = words.filter { lineIds.contains($0.lineId) }.stableSorted {
        if $0.boundingBox.x != $1.boundingBox.x {
            return $0.boundingBox.x < $1.boundingBox.x
        }
        if $0.lineId != $1.lineId { return $0.lineId < $1.lineId }
        return $0.wordId < $1.wordId
    }
    let candidates = rowWords.filter {
        isAmountText($0.text)
            && abs(rightEdge($0) - priceColumn.x) <= priceColumn.tolerance
    }
    guard var amount = candidates.first else { return nil }
    for candidate in candidates.dropFirst()
    where rightEdge(candidate) > rightEdge(amount) {
        amount = candidate
    }

    let amountX = amount.boundingBox.x
    var labelText = rowWords.filter {
        rightEdge($0) <= amountX && !isAmountText($0.text)
    }.map { $0.text.trimmingCharacters(in: .whitespacesAndNewlines) }
        .joined(separator: " ")
        .trimmingCharacters(in: .whitespacesAndNewlines)
    if labelText.isEmpty {
        let amountRight = rightEdge(amount)
        labelText = rowWords.filter {
            $0.boundingBox.x >= amountRight && !isAmountText($0.text)
        }.map { $0.text.trimmingCharacters(in: .whitespacesAndNewlines) }
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
    return LabelAmountPair(
        labelText: labelText, amountText: amount.text,
        amountLineId: amount.lineId, amountWordId: amount.wordId
    )
}

/// Port of Python `group_lines_into_visual_rows`.
public func groupLinesIntoVisualRows(_ lines: [SectionLine]) -> [[SectionLine]] {
    guard !lines.isEmpty else { return [] }
    let count = lines.count
    var parent = Array(0..<count)

    func centerY(_ line: SectionLine) -> Double {
        line.boundingBox.y + line.boundingBox.height / 2
    }
    func find(_ value: Int) -> Int {
        var current = value
        while parent[current] != current { current = parent[current] }
        var node = value
        while parent[node] != node {
            let next = parent[node]
            parent[node] = current
            node = next
        }
        return current
    }
    func union(_ left: Int, _ right: Int) {
        let leftRoot = find(left)
        let rightRoot = find(right)
        if leftRoot != rightRoot { parent[leftRoot] = rightRoot }
    }

    for left in 0..<count {
        let a = lines[left]
        let aMin = a.boundingBox.y
        let aMax = aMin + a.boundingBox.height
        if left + 1 >= count { continue }
        for right in (left + 1)..<count {
            let b = lines[right]
            let bMin = b.boundingBox.y
            let bMax = bMin + b.boundingBox.height
            if (bMin...bMax).contains(centerY(a))
                || (aMin...aMax).contains(centerY(b))
            {
                union(left, right)
            }
        }
    }

    var componentIndex: [Int: Int] = [:]
    var components: [[SectionLine]] = []
    for index in lines.indices {
        let root = find(index)
        if let existing = componentIndex[root] {
            components[existing].append(lines[index])
        } else {
            componentIndex[root] = components.count
            components.append([lines[index]])
        }
    }
    let rows = components.map {
        $0.stableSorted { $0.boundingBox.x < $1.boundingBox.x }
    }
    return rows.stableSorted {
        let left = $0.reduce(0.0) { $0 + $1.boundingBox.y }
            / Double($0.count)
        let right = $1.reduce(0.0) { $0 + $1.boundingBox.y }
            / Double($1.count)
        return left > right
    }
}

/// Port of Python `build_receipt_rows`.
public func buildReceiptRows(
    lines: [SectionLine], words suppliedWords: [SectionWord]? = nil
) -> [ReceiptVisualRow] {
    guard !lines.isEmpty else { return [] }
    let words = suppliedWords ?? lines.flatMap(\.words)
    let priceColumn = detectPriceColumn(words)
    return groupLinesIntoVisualRows(lines).map { row in
        let boxes = row.map(\.boundingBox)
        let pair = pairRowLabelAmount(
            row, words: words, priceColumn: priceColumn
        )
        let first = row[0]
        return ReceiptVisualRow(
            imageId: first.imageId,
            receiptId: first.receiptId,
            rowId: first.lineId,
            lineIds: row.map(\.lineId),
            yMin: boxes.map(\.y).min()!,
            yMax: boxes.map { $0.y + $0.height }.max()!,
            xMin: boxes.map(\.x).min()!,
            xMax: boxes.map { $0.x + $0.width }.max()!,
            priceColumnX: priceColumn?.x,
            labelText: pair?.labelText,
            amountText: pair?.amountText,
            amountLineId: pair?.amountLineId,
            amountWordId: pair?.amountWordId
        )
    }
}

// MARK: - Section prior model

public struct SectionPriorModel: Decodable, Sendable {
    let global: SectionPrior
    let merchants: [String: SectionPrior]
}

struct SectionPrior: Decodable, Sendable {
    let sections: [String]
    let sectionModels: [String: SectionModel]
    let transitions: [String: [String: Double]]

    enum CodingKeys: String, CodingKey {
        case sections
        case sectionModels = "section_models"
        case transitions
    }
}

struct SectionModel: Decodable, Sendable {
    let features: [String: Distribution]
    let binaryFeatures: [String: BinaryDistribution]
    let duration: Distribution?
    let tokenLogOdds: [String: Double]?

    enum CodingKeys: String, CodingKey {
        case features
        case binaryFeatures = "binary_features"
        case duration
        case tokenLogOdds = "token_log_odds"
    }
}

struct Distribution: Decodable, Sendable {
    let mean: Double
    let std: Double
}

struct BinaryDistribution: Decodable, Sendable {
    let probability: Double
}

public func loadSectionPriorModel() throws -> SectionPriorModel {
    guard let url = Bundle.module.url(
        forResource: "section_order_priors_v2", withExtension: "json"
    ) else {
        throw CocoaError(.fileNoSuchFile)
    }
    return try JSONDecoder().decode(
        SectionPriorModel.self, from: Data(contentsOf: url)
    )
}

// MARK: - Feature extraction and segment decoding

public struct SectionRowFeatures: Sendable {
    public let row: ReceiptVisualRow
    public let position: Double
    public let xSpan: Double
    public let alphaRatio: Double
    public let hasAmount: Double
    public let amountDensity: Double
    public let hasQuantity: Double
    public let tokens: [String]
    public let tokenEvidence: [String: Double]
    /// Sections this row may not be assigned to, whatever the model
    /// prefers. Port of Python `RowFeatures.forbidden_sections`.
    public let forbiddenSections: Set<String>

    public init(
        row: ReceiptVisualRow, position: Double, xSpan: Double,
        alphaRatio: Double, hasAmount: Double, amountDensity: Double,
        hasQuantity: Double, tokens: [String],
        tokenEvidence: [String: Double] = [:],
        forbiddenSections: Set<String> = []
    ) {
        self.row = row
        self.position = position
        self.xSpan = xSpan
        self.alphaRatio = alphaRatio
        self.hasAmount = hasAmount
        self.amountDensity = amountDensity
        self.hasQuantity = hasQuantity
        self.tokens = tokens
        self.tokenEvidence = tokenEvidence
        self.forbiddenSections = forbiddenSections
    }
}

public struct SectionRowAssignment: Sendable {
    public let row: ReceiptVisualRow
    public let sectionType: String
    public let confidence: Double
}

public struct ReceiptSectionPrediction: Sendable, Equatable {
    public let sectionType: String
    public let lineIds: [Int]
    public let rowIds: [Int]
    public let confidence: Double
    public let modelSource: String

    public init(
        sectionType: String, lineIds: [Int], rowIds: [Int],
        confidence: Double, modelSource: String = "swift-worker-v1"
    ) {
        self.sectionType = sectionType
        self.lineIds = lineIds
        self.rowIds = rowIds
        self.confidence = confidence
        self.modelSource = modelSource
    }
}

private let sectionEpsilon = 1e-9
private let amountContextRadius = 2
/// Port of Python `_FORBIDDEN_SCORE`: sinks a segment without sinking
/// the arithmetic.
private let forbiddenScore = -1e6

/// Port of `section_assignment._forbidden_sections`.
///
/// A TENDER row is never an item. The learned prior cannot reach this on
/// its own -- it was fit on a corpus whose PAYMENT rows are text-only
/// (P(has_amount | PAYMENT) = 0.14 against 0.76 for ITEMS), so a
/// two-column "Cash | $10.00" scores as an item and the semi-Markov
/// decoder re-enters ITEMS for it after the total.
///
/// The vocabulary is the TENDER subset, not the whole settlement
/// vocabulary: a bare TOTAL / SUB TOTAL / TAX row printed inside the
/// items block is a real merchant format (In-N-Out, Trader Joe's).
func forbiddenSections(forRowText text: String) -> Set<String> {
    isTenderRow(LineItemRegex.amountStrip.sub(text, with: " "))
        ? ["ITEMS"] : []
}

private func normalizeMerchantKey(_ name: String?) -> String {
    guard let name, !name.isEmpty else { return "" }
    return SectionRegex.merchant.findAll(name.lowercased())
        .joined(separator: " ")
}

private func rowTokens(_ text: String) -> [String] {
    SectionRegex.token.findAll(text.lowercased()).map { token in
        if Amounts.looksLikeReceiptAmount(token) { return "__amount__" }
        if token.allSatisfy(\.isNumber) { return "__number__" }
        return token
    }
}

public func extractSectionRowFeatures(
    rows: [ReceiptVisualRow], lines: [SectionLine]
) -> [SectionRowFeatures] {
    let ordered = rows.stableSorted {
        let left = $0.yMin + $0.yMax
        let right = $1.yMin + $1.yMax
        if left != right { return left > right }
        return $0.rowId < $1.rowId
    }
    let linesById = Dictionary(uniqueKeysWithValues: lines.map {
        ($0.lineId, $0)
    })
    let texts = ordered.map { row in
        row.lineIds.compactMap { linesById[$0]?.text }.joined(separator: " ")
    }
    let tokensByRow = texts.map(rowTokens)
    let amountFlags = zip(ordered, tokensByRow).map { row, tokens in
        (row.amountText != nil || tokens.contains("__amount__")) ? 1.0 : 0.0
    }
    let count = ordered.count
    return ordered.indices.map { index in
        let text = texts[index]
        let letters = text.unicodeScalars.filter {
            $0.properties.isAlphabetic
        }.count
        let digits = text.filter(\.isNumber).count
        let visible = max(letters + digits, 1)
        let start = max(0, index - amountContextRadius)
        let end = min(count, index + amountContextRadius + 1)
        let density = amountFlags[start..<end].reduce(0, +)
            / Double(end - start)
        return SectionRowFeatures(
            row: ordered[index],
            position: count > 1 ? Double(index) / Double(count - 1) : 0.5,
            xSpan: ordered[index].xMax - ordered[index].xMin,
            alphaRatio: Double(letters) / Double(visible),
            hasAmount: amountFlags[index],
            amountDensity: density,
            hasQuantity: SectionRegex.quantity.search(text) == nil ? 0 : 1,
            tokens: tokensByRow[index],
            forbiddenSections: forbiddenSections(forRowText: text)
        )
    }
}

private func gaussianScore(_ value: Double, _ distribution: Distribution)
    -> Double
{
    let std = max(distribution.std, sectionEpsilon)
    let z = (value - distribution.mean) / std
    return -0.5 * z * z - log(std)
}

private func bernoulliScore(
    _ value: Double, _ distribution: BinaryDistribution
) -> Double {
    let probability = min(
        max(distribution.probability, sectionEpsilon), 1 - sectionEpsilon
    )
    return log(value != 0 ? probability : 1 - probability)
}

private func emission(
    _ feature: SectionRowFeatures, section: String,
    sectionModel: SectionModel, fallback: SectionModel
) -> Double {
    if feature.forbiddenSections.contains(section) { return forbiddenScore }
    // Preserve the insertion order of Python RowFeatures.numeric()/binary().
    let numeric: [(String, Double)] = [
        ("position", feature.position),
        ("x_span", feature.xSpan),
        ("alpha_ratio", feature.alphaRatio),
        ("amount_density", feature.amountDensity),
    ]
    var scores = numeric.map { name, value in
        let own = sectionModel.features[name]!
        return gaussianScore(
            value, own.std > sectionEpsilon ? own : fallback.features[name]!
        )
    }
    let binary: [(String, Double)] = [
        ("has_amount", feature.hasAmount),
        ("has_quantity", feature.hasQuantity),
    ]
    scores.append(contentsOf: binary.map { name, value in
        bernoulliScore(
            value,
            sectionModel.binaryFeatures[name]
                ?? fallback.binaryFeatures[name]!
        )
    })
    if !feature.tokenEvidence.isEmpty {
        scores.append(feature.tokenEvidence[section] ?? 0)
    } else {
        let odds = sectionModel.tokenLogOdds ?? fallback.tokenLogOdds ?? [:]
        scores.append(contentsOf: feature.tokens.compactMap { odds[$0] })
    }
    return scores.reduce(0, +)
}

private func confidence(_ scores: [Double], chosen: Int) -> Double {
    let peak = scores.max()!
    let weights = scores.map { exp($0 - peak) }
    return weights[chosen] / max(weights.reduce(0, +), sectionEpsilon)
}

private func merchantPrior(
    _ model: SectionPriorModel, merchantName: String?
) -> SectionPrior {
    model.merchants[normalizeMerchantKey(merchantName)] ?? model.global
}

private func orderedSections(
    _ prior: SectionPrior, global: SectionPrior
) -> [String] {
    global.sections.stableSorted {
        let left = (prior.sectionModels[$0] ?? global.sectionModels[$0]!)
            .features["position"]!.mean
        let right = (prior.sectionModels[$1] ?? global.sectionModels[$1]!)
            .features["position"]!.mean
        if left != right { return left < right }
        return $0 < $1
    }
}

private func transition(
    _ prior: SectionPrior, global: SectionPrior,
    source: String, destination: String
) -> Double {
    prior.transitions[source]?[destination]
        ?? global.transitions[source]?[destination]
        ?? sectionEpsilon
}

private func durationScore(
    _ prior: SectionPrior, global: SectionPrior,
    section: String, duration: Int
) -> Double {
    let model = prior.sectionModels[section] ?? global.sectionModels[section]!
    let distribution = model.duration
        ?? global.sectionModels[section]!.duration!
    return gaussianScore(log(Double(duration)), distribution)
}

private struct PathCell {
    let score: Double
    let start: Int
    let previousState: Int?
}

private func candidateIsGreater(_ left: PathCell, than right: PathCell)
    -> Bool
{
    if left.score != right.score { return left.score > right.score }
    if left.start != right.start { return -left.start > -right.start }
    // Python: -(candidate[1][1] or 0)
    return -(left.previousState ?? 0) > -(right.previousState ?? 0)
}

public func assignFeatureSections(
    _ features: [SectionRowFeatures], model: SectionPriorModel,
    merchantName: String? = nil
) -> [SectionRowAssignment] {
    guard !features.isEmpty else { return [] }
    let prior = merchantPrior(model, merchantName: merchantName)
    let global = model.global
    let sections = orderedSections(prior, global: global)
    guard !sections.isEmpty else { return [] }

    let emissions = features.map { feature in
        sections.map { section in
            emission(
                feature, section: section,
                sectionModel: prior.sectionModels[section]
                    ?? global.sectionModels[section]!,
                fallback: global.sectionModels[section]!
            )
        }
    }
    var prefixes = Array(
        repeating: [0.0], count: sections.count
    )
    for state in sections.indices {
        for rowScores in emissions {
            prefixes[state].append(prefixes[state].last! + rowScores[state])
        }
    }

    var paths: [[PathCell]] = []
    for end in features.indices {
        var current: [PathCell] = []
        for (state, section) in sections.enumerated() {
            var best: PathCell?
            for start in 0...end {
                let duration = end - start + 1
                let segmentScore =
                    prefixes[state][end + 1] - prefixes[state][start]
                    + durationScore(
                        prior, global: global, section: section,
                        duration: duration
                    )
                if start == 0 {
                    let value = transition(
                        prior, global: global, source: "<START>",
                        destination: section
                    )
                    let candidate = PathCell(
                        score: log(max(value, sectionEpsilon)) + segmentScore,
                        start: start, previousState: nil
                    )
                    if best == nil
                        || candidateIsGreater(candidate, than: best!)
                    {
                        best = candidate
                    }
                    continue
                }
                for (previousState, source) in sections.enumerated()
                where previousState != state {
                    let value = transition(
                        prior, global: global, source: source,
                        destination: section
                    )
                    let candidate = PathCell(
                        score: paths[start - 1][previousState].score
                            + log(max(value, sectionEpsilon)) + segmentScore,
                        start: start, previousState: previousState
                    )
                    if best == nil
                        || candidateIsGreater(candidate, than: best!)
                    {
                        best = candidate
                    }
                }
            }
            current.append(best!)
        }
        paths.append(current)
    }

    var state = 0
    var bestFinal = paths.last![0].score
        + log(max(
            transition(
                prior, global: global, source: sections[0],
                destination: "<END>"
            ),
            sectionEpsilon
        ))
    for index in sections.indices.dropFirst() {
        let score = paths.last![index].score
            + log(max(
                transition(
                    prior, global: global, source: sections[index],
                    destination: "<END>"
                ),
                sectionEpsilon
            ))
        if score > bestFinal {
            bestFinal = score
            state = index
        }
    }

    var states = Array(repeating: 0, count: features.count)
    var end = features.count - 1
    while end >= 0 {
        let cell = paths[end][state]
        for row in cell.start...end { states[row] = state }
        guard let previous = cell.previousState else { break }
        end = cell.start - 1
        state = previous
    }
    return features.indices.map { index in
        SectionRowAssignment(
            row: features[index].row,
            sectionType: sections[states[index]],
            confidence: confidence(emissions[index], chosen: states[index])
        )
    }
}

public func assignRowSections(
    rows: [ReceiptVisualRow], lines: [SectionLine],
    model: SectionPriorModel, merchantName: String? = nil
) -> [SectionRowAssignment] {
    assignFeatureSections(
        extractSectionRowFeatures(rows: rows, lines: lines),
        model: model, merchantName: merchantName
    )
}

public func assignRowSections(
    rows: [ReceiptVisualRow], lines: [SectionLine],
    merchantName: String? = nil
) throws -> [SectionRowAssignment] {
    try assignRowSections(
        rows: rows, lines: lines, model: loadSectionPriorModel(),
        merchantName: merchantName
    )
}

/// Port of Python `sections_from_assignments`. The Python persistence model
/// source is intentionally replaced with the on-device provenance stamp.
public func sectionsFromAssignments(
    _ assignments: [SectionRowAssignment]
) -> [ReceiptSectionPrediction] {
    guard !assignments.isEmpty else { return [] }
    var order: [String] = []
    var grouped: [String: [SectionRowAssignment]] = [:]
    for assignment in assignments {
        if grouped[assignment.sectionType] == nil {
            order.append(assignment.sectionType)
        }
        grouped[assignment.sectionType, default: []].append(assignment)
    }
    return order.sorted().map { sectionType in
        let group = grouped[sectionType]!
        return ReceiptSectionPrediction(
            sectionType: sectionType,
            lineIds: group.flatMap { $0.row.lineIds },
            rowIds: group.map { $0.row.rowId },
            confidence: group.reduce(0.0) { $0 + $1.confidence }
                / Double(group.count)
        )
    }
}

#if os(macOS)
/// Convert the worker's refinement OCR lines to the geometry schema used by
/// the deterministic row builder. IDs match the cloud parser's 1-based
/// `enumerate(..., start=1)` contract.
public func makeSectionLines(
    _ lines: [Line], imageId: String, receiptId: Int
) -> [SectionLine] {
    lines.enumerated().compactMap { lineOffset, line -> SectionLine? in
        guard !line.text.isEmpty else { return nil }
        let lineId = lineOffset + 1
        let words = line.words.enumerated().compactMap {
            wordOffset, word -> SectionWord? in
            guard !word.text.isEmpty, word.confidence > 0 else { return nil }
            return SectionWord(
                lineId: lineId, wordId: wordOffset + 1, text: word.text,
                boundingBox: SectionRect(
                    x: Double(word.boundingBox.x),
                    y: Double(word.boundingBox.y),
                    width: Double(word.boundingBox.width),
                    height: Double(word.boundingBox.height)
                )
            )
        }
        return SectionLine(
            imageId: imageId, receiptId: receiptId, lineId: lineId,
            text: line.text,
            boundingBox: SectionRect(
                x: Double(line.boundingBox.x),
                y: Double(line.boundingBox.y),
                width: Double(line.boundingBox.width),
                height: Double(line.boundingBox.height)
            ),
            words: words
        )
    }
}
#endif
