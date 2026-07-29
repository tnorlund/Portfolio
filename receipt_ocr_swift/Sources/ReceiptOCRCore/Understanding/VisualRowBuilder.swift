import Foundation

#if os(macOS)

  /// Builds the same visual rows and row-embedding text as the Python upload
  /// pipeline.
  ///
  /// Apple Vision can emit the label and amount on one printed row as separate
  /// ``Line`` values. The canonical Python implementation joins those values
  /// using inclusive centroid overlap and union-find connected components. This
  /// type intentionally mirrors that behavior so shadow understanding keeps the
  /// same row identity as the persisted `ReceiptRow` entities.
  public struct VisualRowBuilder: Sendable {
    private struct IndexedLine {
      let index: Int
      let lineID: Int
      let line: Line
    }

    private struct IndexedWord {
      let lineID: Int
      let wordID: Int
      let word: Word
    }

    private struct PriceColumn {
      let x: Double
      let tolerance: Double
    }

    private struct LabelAmountPair {
      let labelText: String
      let amountText: String
    }

    private struct RowDraft {
      let lines: [IndexedLine]
      let text: String
      let bounds: VisualRowBounds
      let pair: LabelAmountPair?
      let layoutEvidence: [LayoutWordEvidence]
    }

    private struct UnionFind {
      private var parent: [Int]

      init(count: Int) {
        parent = Array(0..<count)
      }

      mutating func find(_ value: Int) -> Int {
        if parent[value] != value {
          parent[value] = find(parent[value])
        }
        return parent[value]
      }

      mutating func union(_ first: Int, _ second: Int) {
        let firstRoot = find(first)
        let secondRoot = find(second)
        if firstRoot != secondRoot {
          // This direction matches line_format.py's `parent[px] = py`.
          parent[firstRoot] = secondRoot
        }
      }
    }

    private static let amountExpression = try! NSRegularExpression(
      pattern: #"^(?:\()?[-+]?\$?(?:\d+|\d{1,3}(?:,\d{3})+)\.\d{2}-?(?:\))?$"#
    )

    public init() {}

    /// Materialize visual rows in top-to-bottom order.
    ///
    /// - Parameters:
    ///   - lines: OCR lines in their original serialized order. Their 1-based
    ///     array positions remain the canonical line IDs even after row sorting.
    ///   - predictions: LayoutLM predictions parallel to `lines`. Short or
    ///     internally uneven prediction arrays are mapped without shifting any
    ///     later line or word IDs.
    public func build(
      lines: [Line],
      predictions: [LinePrediction] = []
    ) -> [VisualReceiptRow] {
      guard !lines.isEmpty else { return [] }

      // The Python Swift-payload parser drops empty lines but never reclaims
      // their 1-based positions. Filtering after enumeration preserves those
      // intentional ID gaps.
      let indexedLines = lines.enumerated().compactMap { index, line in
        line.text.isEmpty
          ? nil
          : IndexedLine(index: index, lineID: index + 1, line: line)
      }
      guard !indexedLines.isEmpty else { return [] }
      let rows = group(indexedLines)
      let words = indexedLines.flatMap { indexedLine in
        indexedLine.line.words.enumerated().compactMap {
          wordIndex, word -> IndexedWord? in
          // Match `_parse_receipt_ocr_from_swift`: invalid words are omitted,
          // while their original word IDs remain reserved.
          guard !word.text.isEmpty, word.confidence > 0 else { return nil }
          return IndexedWord(
            lineID: indexedLine.lineID,
            wordID: wordIndex + 1,
            word: word
          )
        }
      }
      let priceColumn = detectPriceColumn(words)

      let drafts = rows.map { row -> RowDraft in
        let boxes = row.map(\.line.boundingBox)
        let rowLineIDs = Set(row.map(\.lineID))
        return RowDraft(
          lines: row,
          text: row.map(\.line.text).joined(separator: " "),
          bounds: VisualRowBounds(
            xMin: boxes.map { Double($0.x) }.min() ?? 0,
            yMin: boxes.map { Double($0.y) }.min() ?? 0,
            xMax: boxes.map { Double($0.x + $0.width) }.max() ?? 0,
            yMax: boxes.map { Double($0.y + $0.height) }.max() ?? 0
          ),
          pair: pairLabelAndAmount(
            rowLineIDs: rowLineIDs,
            words: words,
            priceColumn: priceColumn
          ),
          layoutEvidence: layoutEvidence(for: row, predictions: predictions)
        )
      }

      return drafts.enumerated().map { index, draft in
        let above = index > 0 ? drafts[index - 1].text : "<EDGE>"
        let below =
          index + 1 < drafts.count
          ? drafts[index + 1].text
          : "<EDGE>"
        return VisualReceiptRow(
          rowID: draft.lines[0].lineID,
          lineIDs: draft.lines.map(\.lineID),
          text: draft.text,
          embeddingInput: "\(above)\n\(draft.text)\n\(below)",
          bounds: draft.bounds,
          priceColumnX: priceColumn?.x,
          labelText: draft.pair?.labelText,
          amountText: draft.pair?.amountText,
          layoutEvidence: draft.layoutEvidence
        )
      }
    }

    private func group(_ lines: [IndexedLine]) -> [[IndexedLine]] {
      var unionFind = UnionFind(count: lines.count)

      for firstIndex in lines.indices {
        let firstBox = lines[firstIndex].line.boundingBox
        let firstMinimumY = Double(firstBox.y)
        let firstMaximumY = Double(firstBox.y + firstBox.height)
        let firstCentroidY = firstMinimumY + Double(firstBox.height) / 2

        for secondIndex in lines.indices where secondIndex > firstIndex {
          let secondBox = lines[secondIndex].line.boundingBox
          let secondMinimumY = Double(secondBox.y)
          let secondMaximumY = Double(secondBox.y + secondBox.height)
          let secondCentroidY =
            secondMinimumY + Double(secondBox.height) / 2

          let firstCentroidInSecond =
            secondMinimumY <= firstCentroidY
            && firstCentroidY <= secondMaximumY
          let secondCentroidInFirst =
            firstMinimumY <= secondCentroidY
            && secondCentroidY <= firstMaximumY
          if firstCentroidInSecond || secondCentroidInFirst {
            unionFind.union(firstIndex, secondIndex)
          }
        }
      }

      // Python dictionaries retain the insertion order of the first member seen
      // for each component. Keep that order explicitly for deterministic ties.
      var components: [Int: [IndexedLine]] = [:]
      var componentOrder: [Int] = []
      for index in lines.indices {
        let root = unionFind.find(index)
        if components[root] == nil {
          components[root] = []
          componentOrder.append(root)
        }
        components[root, default: []].append(lines[index])
      }

      let orderedComponents = componentOrder.enumerated().map {
        componentIndex, root -> (Int, [IndexedLine], Double) in
        let component = (components[root] ?? []).sorted { first, second in
          let firstX = Double(first.line.boundingBox.x)
          let secondX = Double(second.line.boundingBox.x)
          return firstX == secondX ? first.index < second.index : firstX < secondX
        }
        let averageY =
          component.reduce(0.0) {
            $0 + Double($1.line.boundingBox.y)
          } / Double(component.count)
        return (componentIndex, component, averageY)
      }

      return orderedComponents.sorted { first, second in
        first.2 == second.2 ? first.0 < second.0 : first.2 > second.2
      }.map(\.1)
    }

    private func layoutEvidence(
      for row: [IndexedLine],
      predictions: [LinePrediction]
    ) -> [LayoutWordEvidence] {
      row.flatMap { indexedLine -> [LayoutWordEvidence] in
        guard indexedLine.index < predictions.count else { return [] }
        let prediction = predictions[indexedLine.index]
        let count = min(
          prediction.tokens.count,
          prediction.labels.count,
          prediction.confidences.count
        )
        return (0..<count).map { wordIndex in
          LayoutWordEvidence(
            lineID: indexedLine.lineID,
            wordID: wordIndex + 1,
            text: prediction.tokens[wordIndex],
            label: prediction.labels[wordIndex],
            confidence: Double(prediction.confidences[wordIndex])
          )
        }
      }
    }

    private func detectPriceColumn(_ words: [IndexedWord]) -> PriceColumn? {
      let amounts = words.filter { Self.isAmountText($0.word.text) }
      guard !amounts.isEmpty else { return nil }

      let widths = amounts.compactMap { indexedWord -> Double? in
        let compact = indexedWord.word.text
          .trimmingCharacters(in: .whitespacesAndNewlines)
          .replacingOccurrences(of: " ", with: "")
        let width =
          Double(indexedWord.word.boundingBox.width)
          / Double(max(compact.count, 1))
        return width == 0 ? nil : width
      }
      let tolerance = median(widths) ?? 0
      let ordered = amounts.enumerated().sorted { first, second in
        let firstEdge = rightEdge(first.element.word)
        let secondEdge = rightEdge(second.element.word)
        return firstEdge == secondEdge
          ? first.offset < second.offset
          : firstEdge < secondEdge
      }.map(\.element)

      var clusters: [[IndexedWord]] = []
      for word in ordered {
        if let lastWord = clusters.last?.last,
          rightEdge(word.word) - rightEdge(lastWord.word) <= tolerance
        {
          clusters[clusters.count - 1].append(word)
        } else {
          clusters.append([word])
        }
      }

      var winner = clusters[0]
      for cluster in clusters.dropFirst() where isBetter(cluster, than: winner) {
        winner = cluster
      }
      return PriceColumn(
        x: median(winner.map { rightEdge($0.word) }) ?? 0,
        tolerance: tolerance
      )
    }

    private func isBetter(
      _ candidate: [IndexedWord],
      than current: [IndexedWord]
    ) -> Bool {
      let candidateKey = (
        Set(candidate.map(\.lineID)).count,
        candidate.count,
        median(candidate.map { rightEdge($0.word) }) ?? 0
      )
      let currentKey = (
        Set(current.map(\.lineID)).count,
        current.count,
        median(current.map { rightEdge($0.word) }) ?? 0
      )
      if candidateKey.0 != currentKey.0 {
        return candidateKey.0 > currentKey.0
      }
      if candidateKey.1 != currentKey.1 {
        return candidateKey.1 > currentKey.1
      }
      return candidateKey.2 > currentKey.2
    }

    private func pairLabelAndAmount(
      rowLineIDs: Set<Int>,
      words: [IndexedWord],
      priceColumn: PriceColumn?
    ) -> LabelAmountPair? {
      guard let priceColumn else { return nil }
      let rowWords = words.filter { rowLineIDs.contains($0.lineID) }.sorted {
        first, second in
        let firstX = Double(first.word.boundingBox.x)
        let secondX = Double(second.word.boundingBox.x)
        if firstX != secondX { return firstX < secondX }
        if first.lineID != second.lineID { return first.lineID < second.lineID }
        return first.wordID < second.wordID
      }
      let candidates = rowWords.filter {
        Self.isAmountText($0.word.text)
          && abs(rightEdge($0.word) - priceColumn.x) <= priceColumn.tolerance
      }
      guard var amount = candidates.first else { return nil }
      for candidate in candidates.dropFirst()
      where rightEdge(candidate.word) > rightEdge(amount.word) {
        amount = candidate
      }

      let amountX = Double(amount.word.boundingBox.x)
      let labelText = rowWords.filter {
        rightEdge($0.word) <= amountX && !Self.isAmountText($0.word.text)
      }.map {
        $0.word.text.trimmingCharacters(in: .whitespacesAndNewlines)
      }.joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)

      return LabelAmountPair(
        labelText: labelText,
        amountText: amount.word.text
      )
    }

    private func rightEdge(_ word: Word) -> Double {
      Double(word.boundingBox.x + word.boundingBox.width)
    }

    private func median(_ values: [Double]) -> Double? {
      guard !values.isEmpty else { return nil }
      let sorted = values.sorted()
      let middle = sorted.count / 2
      if sorted.count.isMultiple(of: 2) {
        return (sorted[middle - 1] + sorted[middle]) / 2
      }
      return sorted[middle]
    }

    static func isAmountText(_ text: String) -> Bool {
      let compact =
        text
        .trimmingCharacters(in: .whitespacesAndNewlines)
        .replacingOccurrences(of: " ", with: "")
      let range = NSRange(compact.startIndex..<compact.endIndex, in: compact)
      return amountExpression.firstMatch(in: compact, range: range) != nil
    }
  }

#endif
