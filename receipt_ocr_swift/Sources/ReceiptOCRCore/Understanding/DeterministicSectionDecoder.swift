import Foundation

#if os(macOS)

  public enum SectionDecoderError: Error, LocalizedError {
    case invalidPrior(String)

    public var errorDescription: String? {
      switch self {
      case .invalidPrior(let message):
        return "Invalid section prior: \(message)"
      }
    }
  }

  private struct SectionDistribution: Decodable {
    let mean: Double
    let std: Double
  }

  private struct SectionProbability: Decodable {
    let probability: Double
  }

  private struct SectionModel: Decodable {
    let features: [String: SectionDistribution]
    let binaryFeatures: [String: SectionProbability]
    let duration: SectionDistribution
    let tokenLogOdds: [String: Double]

    private enum CodingKeys: String, CodingKey {
      case features
      case binaryFeatures = "binary_features"
      case duration
      case tokenLogOdds = "token_log_odds"
    }

    init(from decoder: Decoder) throws {
      let values = try decoder.container(keyedBy: CodingKeys.self)
      features = try values.decode(
        [String: SectionDistribution].self,
        forKey: .features
      )
      binaryFeatures = try values.decode(
        [String: SectionProbability].self,
        forKey: .binaryFeatures
      )
      duration = try values.decode(
        SectionDistribution.self,
        forKey: .duration
      )
      tokenLogOdds =
        try values.decodeIfPresent(
          [String: Double].self,
          forKey: .tokenLogOdds
        ) ?? [:]
    }
  }

  private struct SectionPrior: Decodable {
    let sections: [String]
    let sectionModels: [String: SectionModel]
    let transitions: [String: [String: Double]]

    private enum CodingKeys: String, CodingKey {
      case sections
      case sectionModels = "section_models"
      case transitions
    }
  }

  private struct SectionPriorBundle: Decodable {
    let global: SectionPrior
    let merchants: [String: SectionPrior]
  }

  private struct SectionRowFeatures {
    let row: VisualReceiptRow
    let position: Double
    let xSpan: Double
    let alphaRatio: Double
    let hasAmount: Double
    let amountDensity: Double
    let hasQuantity: Double
    let tokens: [String]
    let tokenEvidence: [String: Double]?

    func numeric(_ name: String) -> Double {
      switch name {
      case "position": return position
      case "x_span": return xSpan
      case "alpha_ratio": return alphaRatio
      case "amount_density": return amountDensity
      default: return 0
      }
    }

    func binary(_ name: String) -> Double {
      switch name {
      case "has_amount": return hasAmount
      case "has_quantity": return hasQuantity
      default: return 0
      }
    }
  }

  private struct DecoderCell {
    let score: Double
    let start: Int
    let previousState: Int?
  }

  /// Swift port of `receipt_upload.section_assignment`.
  ///
  /// The prior remains the committed Python asset and is loaded by URL. This
  /// avoids maintaining two copies of either the model or the section vocabulary.
  public struct DeterministicSectionDecoder: Sendable {
    private static let epsilon = 1e-9
    private let prior: SectionPriorBundle

    public init(priorURL: URL) throws {
      let data = try Data(contentsOf: priorURL)
      self.prior = try JSONDecoder().decode(SectionPriorBundle.self, from: data)
      guard !prior.global.sections.isEmpty else {
        throw SectionDecoderError.invalidPrior("global sections are empty")
      }
      guard Set(prior.global.sections).count == prior.global.sections.count else {
        throw SectionDecoderError.invalidPrior(
          "global sections contain duplicates"
        )
      }
      for section in prior.global.sections
      where prior.global.sectionModels[section] == nil {
        throw SectionDecoderError.invalidPrior(
          "global model is missing \(section)"
        )
      }
      try Self.validate(
        prior.global,
        name: "global",
        requiredSections: prior.global.sections,
        requiresEveryModel: true
      )
      for (merchant, model) in prior.merchants {
        try Self.validate(
          model,
          name: "merchant \(merchant)",
          requiredSections: prior.global.sections,
          requiresEveryModel: false
        )
      }
    }

    /// Decode rows with optional cosine-weighted, human-VALID neighbor evidence.
    ///
    /// Chroma metadata labels are deliberately ignored. A neighbor contributes
    /// only when its referenced Dynamo section maps a plurality of the
    /// neighbor's row line IDs to one VALID section.
    public func assign(
      rows: [VisualReceiptRow],
      merchantName: String?,
      neighbors: [[SectionNeighbor]] = [],
      knownEvidence: [ReceiptReference: KnownReceiptEvidence] = [:]
    ) -> [SectionAssignment] {
      guard !rows.isEmpty else { return [] }
      let orderedRows = rows.sorted {
        let lhs = -($0.bounds.yMin + $0.bounds.yMax)
        let rhs = -($1.bounds.yMin + $1.bounds.yMax)
        return lhs == rhs ? $0.rowID < $1.rowID : lhs < rhs
      }
      let selectedPrior =
        prior.merchants[Self.normalizeMerchantKey(merchantName)] ?? prior.global
      let sections = orderedSections(selectedPrior)
      guard !sections.isEmpty else { return [] }

      let extracted = extractFeatures(orderedRows)
      let neighborResult = neighborEvidence(
        rows: orderedRows,
        sections: sections,
        neighbors: neighbors,
        knownEvidence: knownEvidence
      )
      let features = zip(extracted, neighborResult.scores).map {
        feature, evidence -> SectionRowFeatures in
        guard let evidence else { return feature }
        var combined: [String: Double] = [:]
        for section in sections {
          let lexical = feature.tokens.reduce(0.0) { total, token in
            total
              + (prior.global.sectionModels[section]?.tokenLogOdds[token] ?? 0)
          }
          combined[section] =
            lexical
            + ReceiptUnderstandingConstants.embeddingKNNWeight
            * (evidence[section] ?? 0)
        }
        return SectionRowFeatures(
          row: feature.row,
          position: feature.position,
          xSpan: feature.xSpan,
          alphaRatio: feature.alphaRatio,
          hasAmount: feature.hasAmount,
          amountDensity: feature.amountDensity,
          hasQuantity: feature.hasQuantity,
          tokens: feature.tokens,
          tokenEvidence: combined
        )
      }

      let emissions = features.map { feature in
        sections.map { section in
          emission(
            feature,
            section: section,
            model: selectedPrior.sectionModels[section]
              ?? prior.global.sectionModels[section]!,
            fallback: prior.global.sectionModels[section]!
          )
        }
      }
      let states = decode(
        features: features,
        sections: sections,
        emissions: emissions,
        selectedPrior: selectedPrior
      )

      return zip(features.indices, states).map { index, state in
        SectionAssignment(
          rowID: features[index].row.rowID,
          lineIDs: features[index].row.lineIDs,
          sectionType: sections[state],
          confidence: confidence(emissions[index], chosen: state),
          neighborConfidence: neighborResult.confidences[index],
          neighborCount: neighborResult.counts[index]
        )
      }
    }

    public static func normalizeMerchantKey(_ name: String?) -> String {
      guard let name, !name.isEmpty else { return "" }
      let pattern = try! NSRegularExpression(pattern: "[a-z0-9]+")
      let lowered = name.lowercased()
      let range = NSRange(lowered.startIndex..., in: lowered)
      return pattern.matches(in: lowered, range: range).compactMap {
        Range($0.range, in: lowered).map { String(lowered[$0]) }
      }.joined(separator: " ")
    }

    private static func validate(
      _ prior: SectionPrior,
      name: String,
      requiredSections: [String],
      requiresEveryModel: Bool
    ) throws {
      let numericFeatures = [
        "position", "x_span", "alpha_ratio", "amount_density",
      ]
      let binaryFeatures = ["has_amount", "has_quantity"]
      for section in requiredSections {
        guard let model = prior.sectionModels[section] else {
          if requiresEveryModel {
            throw SectionDecoderError.invalidPrior(
              "\(name) model is missing \(section)"
            )
          }
          continue
        }
        for feature in numericFeatures {
          guard let distribution = model.features[feature] else {
            throw SectionDecoderError.invalidPrior(
              "\(name).\(section) is missing feature \(feature)"
            )
          }
          guard
            distribution.mean.isFinite,
            distribution.std.isFinite,
            distribution.std >= 0
          else {
            throw SectionDecoderError.invalidPrior(
              "\(name).\(section).\(feature) is not finite"
            )
          }
        }
        for feature in binaryFeatures {
          guard let distribution = model.binaryFeatures[feature] else {
            throw SectionDecoderError.invalidPrior(
              "\(name).\(section) is missing binary feature \(feature)"
            )
          }
          guard
            distribution.probability.isFinite,
            (0...1).contains(distribution.probability)
          else {
            throw SectionDecoderError.invalidPrior(
              "\(name).\(section).\(feature) is outside 0...1"
            )
          }
        }
        guard
          model.duration.mean.isFinite,
          model.duration.std.isFinite,
          model.duration.std >= 0,
          model.tokenLogOdds.values.allSatisfy(\.isFinite)
        else {
          throw SectionDecoderError.invalidPrior(
            "\(name).\(section) has an invalid duration or token weight"
          )
        }
      }
      for (source, destinations) in prior.transitions {
        for (destination, probability) in destinations
        where !probability.isFinite || probability < 0 || probability > 1 {
          throw SectionDecoderError.invalidPrior(
            "\(name) transition \(source)->\(destination) is outside 0...1"
          )
        }
      }
    }

    private func extractFeatures(
      _ rows: [VisualReceiptRow]
    ) -> [SectionRowFeatures] {
      let tokensByRow = rows.map { Self.tokens($0.text) }
      let amountFlags = zip(rows, tokensByRow).map {
        row, tokens in
        (row.amountText != nil || tokens.contains("__amount__")) ? 1.0 : 0.0
      }
      return rows.indices.map { index in
        let row = rows[index]
        let text = row.text
        let letters = text.unicodeScalars.filter {
          CharacterSet.letters.contains($0)
        }.count
        let digits = text.unicodeScalars.filter {
          CharacterSet.decimalDigits.contains($0)
        }.count
        let visible = max(letters + digits, 1)
        let lower = max(0, index - 2)
        let upper = min(rows.count, index + 3)
        let density =
          amountFlags[lower..<upper].reduce(0, +) / Double(upper - lower)
        return SectionRowFeatures(
          row: row,
          position: rows.count > 1
            ? Double(index) / Double(rows.count - 1) : 0.5,
          xSpan: row.bounds.xMax - row.bounds.xMin,
          alphaRatio: Double(letters) / Double(visible),
          hasAmount: amountFlags[index],
          amountDensity: density,
          hasQuantity: Self.hasQuantity(text) ? 1 : 0,
          tokens: tokensByRow[index],
          tokenEvidence: nil
        )
      }
    }

    private func orderedSections(_ selected: SectionPrior) -> [String] {
      prior.global.sections.sorted { lhs, rhs in
        let lhsModel =
          selected.sectionModels[lhs] ?? prior.global.sectionModels[lhs]!
        let rhsModel =
          selected.sectionModels[rhs] ?? prior.global.sectionModels[rhs]!
        let lhsMean = lhsModel.features["position"]!.mean
        let rhsMean = rhsModel.features["position"]!.mean
        return lhsMean == rhsMean ? lhs < rhs : lhsMean < rhsMean
      }
    }

    private func emission(
      _ feature: SectionRowFeatures,
      section: String,
      model: SectionModel,
      fallback: SectionModel
    ) -> Double {
      let numericOrder = ["position", "x_span", "alpha_ratio", "amount_density"]
      var score = numericOrder.reduce(0.0) { total, name in
        let candidate = model.features[name]!
        let distribution =
          candidate.std > Self.epsilon ? candidate : fallback.features[name]!
        return total + gaussian(feature.numeric(name), distribution)
      }
      let binaryOrder = ["has_amount", "has_quantity"]
      score += binaryOrder.reduce(0.0) { total, name in
        let distribution =
          model.binaryFeatures[name] ?? fallback.binaryFeatures[name]!
        return total + bernoulli(feature.binary(name), distribution)
      }
      if let tokenEvidence = feature.tokenEvidence {
        score += tokenEvidence[section] ?? 0
      } else {
        let odds =
          model.tokenLogOdds.isEmpty
          ? fallback.tokenLogOdds : model.tokenLogOdds
        score += feature.tokens.reduce(0.0) {
          $0 + (odds[$1] ?? 0)
        }
      }
      return score
    }

    private func decode(
      features: [SectionRowFeatures],
      sections: [String],
      emissions: [[Double]],
      selectedPrior: SectionPrior
    ) -> [Int] {
      var prefixes = Array(
        repeating: [0.0],
        count: sections.count
      )
      for state in sections.indices {
        for row in emissions {
          prefixes[state].append(prefixes[state].last! + row[state])
        }
      }

      var paths: [[DecoderCell]] = []
      for end in features.indices {
        var current: [DecoderCell] = []
        for state in sections.indices {
          var best: DecoderCell?
          for start in 0...end {
            let duration = end - start + 1
            let segment =
              prefixes[state][end + 1] - prefixes[state][start]
              + durationScore(
                selectedPrior,
                section: sections[state],
                duration: duration
              )
            if start == 0 {
              let candidate = DecoderCell(
                score: log(
                  max(
                    transition(
                      selectedPrior,
                      source: "<START>",
                      destination: sections[state]
                    ),
                    Self.epsilon
                  )
                ) + segment,
                start: start,
                previousState: nil
              )
              if isBetter(candidate, than: best) { best = candidate }
            } else {
              for previous in sections.indices where previous != state {
                let candidate = DecoderCell(
                  score: paths[start - 1][previous].score
                    + log(
                      max(
                        transition(
                          selectedPrior,
                          source: sections[previous],
                          destination: sections[state]
                        ),
                        Self.epsilon
                      )
                    ) + segment,
                  start: start,
                  previousState: previous
                )
                if isBetter(candidate, than: best) { best = candidate }
              }
            }
          }
          current.append(best!)
        }
        paths.append(current)
      }

      var state = sections.indices.first!
      var terminalScore = -Double.infinity
      for index in sections.indices {
        let score =
          paths.last![index].score
          + log(
            max(
              transition(
                selectedPrior,
                source: sections[index],
                destination: "<END>"
              ),
              Self.epsilon
            )
          )
        if score > terminalScore {
          terminalScore = score
          state = index
        }
      }
      var states = Array(repeating: 0, count: features.count)
      var end = features.count - 1
      while end >= 0 {
        let cell = paths[end][state]
        for index in cell.start...end { states[index] = state }
        guard let previous = cell.previousState else { break }
        end = cell.start - 1
        state = previous
      }
      return states
    }

    private func isBetter(
      _ candidate: DecoderCell,
      than current: DecoderCell?
    ) -> Bool {
      guard let current else { return true }
      if candidate.score != current.score {
        return candidate.score > current.score
      }
      if candidate.start != current.start {
        return candidate.start < current.start
      }
      return (candidate.previousState ?? 0) < (current.previousState ?? 0)
    }

    private func transition(
      _ selected: SectionPrior,
      source: String,
      destination: String
    ) -> Double {
      selected.transitions[source]?[destination]
        ?? prior.global.transitions[source]?[destination]
        ?? Self.epsilon
    }

    private func durationScore(
      _ selected: SectionPrior,
      section: String,
      duration: Int
    ) -> Double {
      let selectedModel =
        selected.sectionModels[section] ?? prior.global.sectionModels[section]!
      return gaussian(log(Double(duration)), selectedModel.duration)
    }

    private func gaussian(
      _ value: Double,
      _ distribution: SectionDistribution
    ) -> Double {
      let std = max(distribution.std, Self.epsilon)
      let z = (value - distribution.mean) / std
      return -0.5 * z * z - log(std)
    }

    private func bernoulli(
      _ value: Double,
      _ distribution: SectionProbability
    ) -> Double {
      let probability = min(
        max(distribution.probability, Self.epsilon),
        1 - Self.epsilon
      )
      return log(value != 0 ? probability : 1 - probability)
    }

    private func confidence(_ scores: [Double], chosen: Int) -> Double {
      let peak = scores.max()!
      let weights = scores.map { exp($0 - peak) }
      return weights[chosen] / max(weights.reduce(0, +), Self.epsilon)
    }

    private func neighborEvidence(
      rows: [VisualReceiptRow],
      sections: [String],
      neighbors: [[SectionNeighbor]],
      knownEvidence: [ReceiptReference: KnownReceiptEvidence]
    ) -> (
      scores: [[String: Double]?],
      confidences: [Double?],
      counts: [Int]
    ) {
      var scores: [[String: Double]?] = []
      var confidences: [Double?] = []
      var counts: [Int] = []
      for index in rows.indices {
        let rowNeighbors = index < neighbors.count ? neighbors[index] : []
        var probabilities = Dictionary(
          uniqueKeysWithValues: sections.map { ($0, 0.1) }
        )
        var used = 0
        var totalWeight = 0.0
        for neighbor in rowNeighbors {
          guard
            let known = knownEvidence[neighbor.reference],
            let section = pluralitySection(
              lineIDs: neighbor.rowLineIDs,
              validSections: known.validSectionByLine
            ),
            probabilities[section] != nil
          else { continue }
          let weight = max(neighbor.cosineSimilarity, 0)
          probabilities[section, default: 0] += weight
          totalWeight += weight
          used += 1
        }
        guard used > 0, totalWeight > 0 else {
          scores.append(nil)
          confidences.append(nil)
          counts.append(0)
          continue
        }
        let sum = probabilities.values.reduce(0, +)
        let logs = sections.map {
          log((probabilities[$0] ?? 0) / sum + Self.epsilon)
        }
        let mean = logs.reduce(0, +) / Double(logs.count)
        scores.append(
          Dictionary(
            uniqueKeysWithValues: zip(
              sections,
              logs.map { min(max($0 - mean, -6), 6) }
            )
          )
        )
        let winning = probabilities.values.max() ?? 0
        confidences.append(winning / sum)
        counts.append(used)
      }
      return (scores, confidences, counts)
    }

    private func pluralitySection(
      lineIDs: [Int],
      validSections: [Int: String]
    ) -> String? {
      var counts: [String: Int] = [:]
      for lineID in lineIDs {
        if let section = validSections[lineID] {
          counts[section, default: 0] += 1
        }
      }
      guard let maximum = counts.values.max() else { return nil }
      let winners = counts.filter { $0.value == maximum }.map(\.key)
      return winners.count == 1 ? winners[0] : nil
    }

    private static func tokens(_ text: String) -> [String] {
      let lowered = text.lowercased()
      let regex = try! NSRegularExpression(
        pattern: "[a-z]+|\\d+(?:\\.\\d+)?"
      )
      let range = NSRange(lowered.startIndex..., in: lowered)
      return regex.matches(in: lowered, range: range).compactMap { match in
        guard let tokenRange = Range(match.range, in: lowered) else {
          return nil
        }
        let token = String(lowered[tokenRange])
        if looksLikeReceiptAmount(token) { return "__amount__" }
        if token.allSatisfy(\.isNumber) { return "__number__" }
        return token
      }
    }

    private static func looksLikeReceiptAmount(_ text: String) -> Bool {
      if text.range(of: #"[$€£¥₹]"#, options: .regularExpression) != nil {
        return true
      }
      let patterns = [
        #"-?\(?\d{1,3}(,\d{3})+(\.\d{2})?\)?-?"#,
        #"-?\(?\d+([.,]\d{2})\)?-?"#,
      ]
      return patterns.contains {
        text.range(of: "^(?:\($0))$", options: .regularExpression) != nil
      }
    }

    private static func hasQuantity(_ text: String) -> Bool {
      let pattern =
        #"(?:\b\d+\s*(?:@|x)\s*\$?\d)|(?:\b(?:each|ea)\b)|(?:/\s*lb\b)"#
      return text.range(
        of: pattern,
        options: [.regularExpression, .caseInsensitive]
      ) != nil
    }
  }

#endif
