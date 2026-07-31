import Foundation

#if os(macOS)

  public struct ReceiptIdentityResolver: Sendable {
    private struct PlaceVote {
      let evidence: KnownReceiptEvidence
      var weight: Double
      var references: Set<ReceiptReference>
    }

    public init() {}

    public func extractSignals(
      rows: [VisualReceiptRow]
    ) -> ReceiptIdentitySignals {
      var names: [String] = []
      var addresses: [String] = []
      var phones: [String] = []
      var websites: [String] = []
      for row in rows {
        let grouped = Dictionary(
          grouping: row.layoutEvidence.filter { $0.confidence >= 0.5 }
        ) { Self.baseLabel($0.label) }
        func phrase(_ label: String) -> String? {
          let words = grouped[label, default: []].map(\.text)
          let result = words.joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
          return result.isEmpty ? nil : result
        }
        if let value = phrase("MERCHANT_NAME") { names.append(value) }
        if let value = phrase("ADDRESS_LINE") ?? phrase("ADDRESS") {
          addresses.append(value)
        }
        if let value = phrase("PHONE_NUMBER") { phones.append(value) }
        if let value = phrase("WEBSITE") { websites.append(value) }
      }
      return ReceiptIdentitySignals(
        merchantNames: Self.unique(names),
        addresses: Self.unique(addresses),
        phoneNumbers: Self.unique(phones),
        websites: Self.unique(websites)
      )
    }

    public func resolveKnown(
      identity: ReceiptIdentitySignals,
      neighbors: [[SectionNeighbor]],
      knownEvidence: [ReceiptReference: KnownReceiptEvidence]
    ) -> (resolution: MerchantResolution?, conflict: String?) {
      // Cap repeated rows from one receipt at its strongest cosine so a long
      // receipt cannot manufacture consensus with itself.
      var bestByReference: [ReceiptReference: Double] = [:]
      for neighbor in neighbors.flatMap({ $0 }) {
        bestByReference[neighbor.reference] = max(
          bestByReference[neighbor.reference] ?? 0,
          max(neighbor.cosineSimilarity, 0)
        )
      }
      var votes: [String: PlaceVote] = [:]
      for (reference, weight) in bestByReference {
        guard
          weight > 0,
          let known = knownEvidence[reference],
          known.isHumanValidatedPlace,
          let placeID = Self.nonempty(known.placeID),
          let merchantName = Self.nonempty(known.merchantName)
        else { continue }
        if var vote = votes[placeID] {
          vote.weight += weight
          vote.references.insert(reference)
          votes[placeID] = vote
        } else {
          votes[placeID] = PlaceVote(
            evidence: known,
            weight: weight,
            references: [reference]
          )
        }
        _ = merchantName
      }
      let ranked = votes.values.sorted {
        $0.weight == $1.weight
          ? ($0.evidence.placeID ?? "") < ($1.evidence.placeID ?? "")
          : $0.weight > $1.weight
      }
      guard let top = ranked.first else { return (nil, nil) }
      let total = ranked.reduce(0.0) { $0 + $1.weight }
      let share = top.weight / max(total, 1e-9)
      if ranked.count > 1, share < 0.72, ranked[1].weight >= 0.5 {
        return (nil, "known receipt identities disagree")
      }

      let matches = matchedFields(identity: identity, known: top.evidence)
      let hasLayoutIdentity =
        !identity.merchantNames.isEmpty || !identity.addresses.isEmpty
        || !identity.phoneNumbers.isEmpty || !identity.websites.isEmpty
      if hasLayoutIdentity, matches.isEmpty {
        return (nil, "LayoutLM identity conflicts with known receipt identity")
      }
      let independentKnownConsensus = top.references.count >= 2
      let reliable =
        top.weight >= 0.75 && share >= 0.72
        && (!matches.isEmpty || independentKnownConsensus)
      guard reliable else { return (nil, nil) }
      return (
        MerchantResolution(
          merchantName: top.evidence.merchantName,
          placeID: top.evidence.placeID,
          formattedAddress: top.evidence.formattedAddress,
          confidence: min(
            0.99,
            0.65 + 0.2 * share + 0.05 * Double(matches.count)
          ),
          source: .knownReceipt,
          matchedFields: matches,
          evidence: top.references.sorted {
            $0.imageID == $1.imageID
              ? $0.receiptID < $1.receiptID
              : $0.imageID < $1.imageID
          }.map {
            "VALID_RECEIPT:\($0.imageID):\($0.receiptID)"
          }
        ),
        nil
      )
    }

    public func resolvePlaces(
      identity: ReceiptIdentitySignals,
      candidate: PlacesCandidate?
    ) -> MerchantResolution {
      guard let candidate, candidate.confidence >= 0.8 else {
        return .abstained("no reliable known receipt or Places match")
      }
      return MerchantResolution(
        merchantName: candidate.merchantName,
        placeID: candidate.placeID,
        formattedAddress: candidate.formattedAddress,
        confidence: candidate.confidence,
        source: .places,
        matchedFields: candidate.matchedFields,
        evidence: ["EXTERNAL_PLACES_LOOKUP"]
      )
    }

    private func matchedFields(
      identity: ReceiptIdentitySignals,
      known: KnownReceiptEvidence
    ) -> [String] {
      var fields: [String] = []
      if let knownName = Self.nonempty(known.merchantName),
        identity.merchantNames.contains(where: {
          Self.nameSimilarity($0, knownName) >= 0.7
        })
      {
        fields.append("merchant_name")
      }
      if let knownAddress = Self.nonempty(known.formattedAddress),
        identity.addresses.contains(where: {
          let observed = Self.normalized($0)
          let expected = Self.normalized(knownAddress)
          return !observed.isEmpty && !expected.isEmpty
            && (observed.contains(expected) || expected.contains(observed))
        })
      {
        fields.append("address")
      }
      if let knownPhone = Self.nonempty(known.phoneNumber),
        identity.phoneNumbers.contains(where: {
          let observed = Self.phone($0)
          let expected = Self.phone(knownPhone)
          return !observed.isEmpty && observed == expected
        })
      {
        fields.append("phone")
      }
      if let knownWebsite = Self.nonempty(known.website),
        identity.websites.contains(where: {
          let observed = Self.website($0)
          let expected = Self.website(knownWebsite)
          return !observed.isEmpty && observed == expected
        })
      {
        fields.append("website")
      }
      return fields
    }

    private static func baseLabel(_ label: String) -> String {
      let upper = label.uppercased()
      if upper.hasPrefix("B-") || upper.hasPrefix("I-") {
        return String(upper.dropFirst(2))
      }
      return upper
    }

    private static func unique(_ values: [String]) -> [String] {
      var seen: Set<String> = []
      return values.filter {
        let key = normalized($0)
        return !key.isEmpty && seen.insert(key).inserted
      }
    }

    private static func nonempty(_ value: String?) -> String? {
      let result = value?.trimmingCharacters(in: .whitespacesAndNewlines)
      return result?.isEmpty == false ? result : nil
    }

    private static func normalized(_ value: String) -> String {
      let folded = value.folding(
        options: [.diacriticInsensitive, .caseInsensitive],
        locale: Locale(identifier: "en_US_POSIX")
      )
      let separated = folded.unicodeScalars.reduce(into: "") { result, scalar in
        result +=
          CharacterSet.alphanumerics.contains(scalar)
          ? String(scalar) : " "
      }
      return separated.split(separator: " ").map { String($0) }
        .joined(separator: " ")
    }

    private static func nameSimilarity(_ lhs: String, _ rhs: String) -> Double {
      let left = Set(normalized(lhs).split(separator: " ").map(String.init))
      let right = Set(normalized(rhs).split(separator: " ").map(String.init))
      guard !left.isEmpty, !right.isEmpty else { return 0 }
      return Double(left.intersection(right).count)
        / Double(left.union(right).count)
    }

    private static func phone(_ value: String) -> String {
      var digits = String(value.filter(\.isNumber))
      if digits.count == 11, digits.hasPrefix("1") {
        digits.removeFirst()
      }
      guard digits.count == 10, Set(digits).count > 1 else { return "" }
      return digits
    }

    private static func website(_ value: String) -> String {
      var result = normalized(value).replacingOccurrences(of: " ", with: "")
      for prefix in ["https", "http", "www"] where result.hasPrefix(prefix) {
        result.removeFirst(prefix.count)
      }
      return result
    }
  }

  public struct ReceiptConsistencyChecker: Sendable {
    public init() {}

    public func check(
      rows: [VisualReceiptRow],
      sections: [SectionAssignment],
      merchant: MerchantResolution
    ) -> [ConsistencyIssue] {
      let sectionByRow = Dictionary(
        uniqueKeysWithValues: sections.map { ($0.rowID, $0.sectionType) }
      )
      var issues: [ConsistencyIssue] = []
      for row in rows {
        guard let section = sectionByRow[row.rowID] else { continue }
        let labels = Set(
          row.layoutEvidence.map {
            baseLabel($0.label)
          }
        ).subtracting(["O"])
        if !labels.isDisjoint(
          with: ["PRODUCT_NAME", "QUANTITY", "UNIT_PRICE", "LINE_TOTAL"]
        ), section != "ITEMS" {
          issues.append(
            issue(
              "ITEM_LABEL_OUTSIDE_ITEMS",
              "Product or item-price labels appear in \(section)",
              row.rowID
            )
          )
        }
        if !labels.isDisjoint(with: ["SUBTOTAL", "TAX", "TIP"]),
          section != "SUMMARY"
        {
          issues.append(
            issue(
              "SUMMARY_LABEL_OUTSIDE_SUMMARY",
              "Tax or summary labels appear in \(section)",
              row.rowID
            )
          )
        }
        if labels.contains("GRAND_TOTAL"),
          !["SUMMARY", "TOTAL_LINE"].contains(section)
        {
          issues.append(
            issue(
              "TOTAL_LABEL_OUTSIDE_SUMMARY",
              "Grand total label appears in \(section)",
              row.rowID
            )
          )
        }
        if !labels.isDisjoint(
          with: ["PAYMENT_METHOD", "CHANGE", "CASH_BACK", "REFUND"]
        ), section != "PAYMENT" {
          issues.append(
            issue(
              "PAYMENT_LABEL_OUTSIDE_PAYMENT",
              "Payment label appears in \(section)",
              row.rowID
            )
          )
        }
      }
      issues.append(contentsOf: sectionOrderIssues(sections))
      issues.append(contentsOf: arithmeticIssues(rows))
      if merchant.source == .abstained,
        let reason = merchant.abstainReason,
        reason.localizedCaseInsensitiveContains("conflict")
          || reason.localizedCaseInsensitiveContains("disagree")
      {
        issues.append(
          ConsistencyIssue(
            code: "MERCHANT_IDENTITY_CONFLICT",
            severity: .conflict,
            message: reason
          )
        )
      }
      return issues
    }

    private func sectionOrderIssues(
      _ assignments: [SectionAssignment]
    ) -> [ConsistencyIssue] {
      let order = [
        "STOREFRONT", "ADDRESS", "SECTION_HEADER", "ITEMS", "SUMMARY",
        "TOTAL_LINE", "PAYMENT", "BARCODE", "SURVEY", "FOOTER",
      ]
      let rank = Dictionary(
        uniqueKeysWithValues: order.enumerated().map { ($1, $0) }
      )
      var priorRank = -1
      for assignment in assignments {
        guard let current = rank[assignment.sectionType] else { continue }
        if current < priorRank {
          return [
            ConsistencyIssue(
              code: "SECTION_ORDER_CONFLICT",
              severity: .conflict,
              message: "Section order moves backward at \(assignment.sectionType)",
              rowIDs: [assignment.rowID]
            )
          ]
        }
        priorRank = current
      }
      return []
    }

    private func arithmeticIssues(
      _ rows: [VisualReceiptRow]
    ) -> [ConsistencyIssue] {
      func values(_ label: String) -> [(Int, Double)] {
        rows.compactMap { row in
          guard
            row.layoutEvidence.contains(where: {
              baseLabel($0.label) == label
            }), let value = parseAmount(row.amountText ?? row.text)
          else { return nil }
          return (row.rowID, value)
        }
      }
      let subtotals = values("SUBTOTAL")
      let taxes = values("TAX")
      let tips = values("TIP")
      let totals = values("GRAND_TOTAL")
      guard let subtotal = subtotals.last, let total = totals.last else {
        return []
      }
      let expected =
        subtotal.1 + taxes.reduce(0) { $0 + $1.1 }
        + tips.reduce(0) { $0 + $1.1 }
      guard abs(expected - total.1) > 0.05 else { return [] }
      return [
        ConsistencyIssue(
          code: "RECEIPT_ARITHMETIC_CONFLICT",
          severity: .conflict,
          message: String(
            format: "Subtotal, tax, and tip %.2f do not reconcile to total %.2f",
            expected,
            total.1
          ),
          rowIDs: [subtotal.0, total.0]
        )
      ]
    }

    private func parseAmount(_ text: String) -> Double? {
      let expression = try! NSRegularExpression(
        pattern: #"[-+]?\$?\d[\d,]*\.\d{2}-?"#
      )
      let range = NSRange(text.startIndex..., in: text)
      guard
        let match = expression.matches(in: text, range: range).last,
        let swiftRange = Range(match.range, in: text)
      else {
        return nil
      }
      let raw = String(text[swiftRange])
      var compact = raw.replacingOccurrences(of: "$", with: "")
        .replacingOccurrences(of: ",", with: "")
      let negative = compact.hasSuffix("-")
      if negative { compact.removeLast() }
      guard let value = Double(compact) else { return nil }
      return negative ? -value : value
    }

    private func baseLabel(_ label: String) -> String {
      let upper = label.uppercased()
      return upper.hasPrefix("B-") || upper.hasPrefix("I-")
        ? String(upper.dropFirst(2)) : upper
    }

    private func issue(
      _ code: String,
      _ message: String,
      _ rowID: Int
    ) -> ConsistencyIssue {
      ConsistencyIssue(
        code: code,
        severity: .warning,
        message: message,
        rowIDs: [rowID]
      )
    }
  }

  public struct ReceiptCandidateBuilder: Sendable {
    public init() {}

    public func build(
      reference: ReceiptReference,
      sections: [SectionAssignment],
      merchant: MerchantResolution,
      issues: [ConsistencyIssue],
      forceNeedsReview: Bool = false
    ) -> [CandidateWrite] {
      let hasIssue = !issues.isEmpty
      var result: [CandidateWrite] = []
      let grouped = Dictionary(grouping: sections, by: \.sectionType)
      for sectionType in grouped.keys.sorted() {
        let assignments = grouped[sectionType]!
        let confidence =
          assignments.reduce(0.0) {
            $0 + $1.confidence
          } / Double(assignments.count)
        let status: CandidateValidationStatus =
          hasIssue || forceNeedsReview
            || confidence
              < ReceiptUnderstandingConstants.minimumSectionCandidateConfidence
          ? .needsReview : .pending
        var provenance = [
          ReceiptUnderstandingConstants.sectionModelSource,
          ReceiptUnderstandingConstants.modelVersion,
        ]
        if assignments.contains(where: { $0.neighborCount > 0 }) {
          provenance.append("chroma_cosine_valid_sections")
        }
        if forceNeedsReview {
          provenance.append("offline_fallback")
        }
        result.append(
          CandidateWrite(
            entityType: .section,
            idempotencyKey:
              "IMAGE#\(reference.imageID)#RECEIPT#"
              + String(format: "%05d", reference.receiptID)
              + "#SECTION#\(sectionType)",
            validationStatus: status,
            confidence: confidence,
            provenance: provenance,
            sectionType: sectionType,
            lineIDs: assignments.flatMap(\.lineIDs),
            rowIDs: assignments.map(\.rowID),
            evidence: issues.map(\.code)
          )
        )
      }
      if merchant.source != .abstained,
        let merchantName = merchant.merchantName,
        let placeID = merchant.placeID
      {
        var provenance = [
          merchant.source.rawValue,
          ReceiptUnderstandingConstants.modelVersion,
        ]
        if forceNeedsReview {
          provenance.append("offline_fallback")
        }
        result.append(
          CandidateWrite(
            entityType: .place,
            idempotencyKey:
              "IMAGE#\(reference.imageID)#RECEIPT#"
              + String(format: "%05d", reference.receiptID) + "#PLACE",
            validationStatus:
              hasIssue || forceNeedsReview || merchant.confidence < 0.8
              ? .needsReview : .pending,
            confidence: merchant.confidence,
            provenance: provenance,
            merchantName: merchantName,
            placeID: placeID,
            evidence: merchant.evidence + issues.map(\.code)
          )
        )
      }
      return result
    }
  }

  /// Best-effort, shadow-only first-pass receipt understanding pipeline.
  ///
  /// No collaborator in this type can execute a production entity write.
  /// Candidate payloads are returned for parity comparison and remain PENDING or
  /// NEEDS_REVIEW.
  public struct ReceiptUnderstandingPipeline: ReceiptUnderstandingAnalyzing {
    private let rowBuilder: VisualRowBuilder
    private let embedder: (any RowEmbeddingProviding)?
    private let neighborQuery: (any ReceiptNeighborQuerying)?
    private let knownEvidenceProvider: (any KnownReceiptEvidenceProviding)?
    private let places: (any PlacesLookingUp)?
    private let sectionDecoder: DeterministicSectionDecoder
    private let identityResolver: ReceiptIdentityResolver
    private let consistencyChecker: ReceiptConsistencyChecker
    private let candidateBuilder: ReceiptCandidateBuilder

    public init(
      priorURL: URL,
      embedder: (any RowEmbeddingProviding)? = nil,
      neighborQuery: (any ReceiptNeighborQuerying)? = nil,
      knownEvidenceProvider: (any KnownReceiptEvidenceProviding)? = nil,
      places: (any PlacesLookingUp)? = nil
    ) throws {
      self.rowBuilder = VisualRowBuilder()
      self.embedder = embedder
      self.neighborQuery = neighborQuery
      self.knownEvidenceProvider = knownEvidenceProvider
      self.places = places
      self.sectionDecoder = try DeterministicSectionDecoder(
        priorURL: priorURL
      )
      self.identityResolver = ReceiptIdentityResolver()
      self.consistencyChecker = ReceiptConsistencyChecker()
      self.candidateBuilder = ReceiptCandidateBuilder()
    }

    public func analyze(
      reference: ReceiptReference,
      lines: [Line],
      predictions: [LinePrediction]
    ) async -> ReceiptUnderstandingReport {
      let totalStart = DispatchTime.now().uptimeNanoseconds
      var timings: [StageTiming] = []
      var errors: [String] = []
      var offline = false

      let rows = measure("row_construction", timings: &timings) {
        rowBuilder.build(lines: lines, predictions: predictions)
      }
      let identity = measure("identity_extraction", timings: &timings) {
        identityResolver.extractSignals(rows: rows)
      }

      var embeddings: [[Double]] = []
      let embeddingStart = DispatchTime.now().uptimeNanoseconds
      if let embedder {
        do {
          embeddings = try await embedder.embedRows(
            rows.map(\.embeddingInput)
          )
          if embeddings.count != rows.count
            || embeddings.contains(where: {
              $0.count != ReceiptUnderstandingConstants.embeddingDimensions
            })
          {
            offline = true
            errors.append(
              "openai_embeddings: missing or dimension-mismatched vectors"
            )
            embeddings = []
          }
        } catch {
          offline = true
          errors.append("openai_embeddings: \(error.localizedDescription)")
        }
      } else {
        offline = true
        errors.append("openai_embeddings: unavailable")
      }
      timings.append(timing("openai_embeddings", since: embeddingStart))

      var neighbors: [[SectionNeighbor]] = []
      let chromaStart = DispatchTime.now().uptimeNanoseconds
      if embeddings.count == rows.count, let neighborQuery {
        do {
          neighbors = try await neighborQuery.queryRows(
            embeddings: embeddings,
            excluding: reference,
            nResults: ReceiptUnderstandingConstants.sectionNeighborCount
          )
        } catch {
          offline = true
          errors.append("chroma_query: \(error.localizedDescription)")
        }
      } else if !rows.isEmpty {
        offline = true
        errors.append("chroma_query: unavailable")
      }
      timings.append(timing("chroma_query", since: chromaStart))

      var known: [ReceiptReference: KnownReceiptEvidence] = [:]
      var strongestByReference: [ReceiptReference: Double] = [:]
      for neighbor in neighbors.flatMap({ $0 }) {
        strongestByReference[neighbor.reference] = max(
          strongestByReference[neighbor.reference] ?? 0,
          neighbor.cosineSimilarity
        )
      }
      let references = Set(
        strongestByReference.sorted {
          if $0.value != $1.value { return $0.value > $1.value }
          if $0.key.imageID != $1.key.imageID {
            return $0.key.imageID < $1.key.imageID
          }
          return $0.key.receiptID < $1.key.receiptID
        }.prefix(ReceiptUnderstandingConstants.maximumKnownReceiptReferences)
          .map(\.key)
      )
      let evidenceNeighbors = neighbors.map { rowNeighbors in
        rowNeighbors.filter { references.contains($0.reference) }
      }
      let knownStart = DispatchTime.now().uptimeNanoseconds
      if !references.isEmpty, let knownEvidenceProvider {
        do {
          known = try await knownEvidenceProvider.evidence(for: references)
        } catch {
          offline = true
          errors.append("known_receipts: \(error.localizedDescription)")
        }
      } else if !references.isEmpty {
        offline = true
        errors.append("known_receipts: unavailable")
      }
      timings.append(timing("known_receipts", since: knownStart))

      let resolutionStart = DispatchTime.now().uptimeNanoseconds
      let knownResolution = identityResolver.resolveKnown(
        identity: identity,
        neighbors: evidenceNeighbors,
        knownEvidence: known
      )
      let merchant: MerchantResolution
      var placesMilliseconds = 0.0
      if let conflict = knownResolution.conflict {
        merchant = .abstained(conflict)
      } else if let resolved = knownResolution.resolution {
        merchant = resolved
      } else if let places,
        !identity.merchantNames.isEmpty || !identity.addresses.isEmpty
          || !identity.phoneNumbers.isEmpty || !identity.websites.isEmpty
      {
        let placesStart = DispatchTime.now().uptimeNanoseconds
        do {
          merchant = identityResolver.resolvePlaces(
            identity: identity,
            candidate: try await places.lookup(identity: identity)
          )
        } catch {
          offline = true
          errors.append("places_lookup: \(error.localizedDescription)")
          merchant = .abstained("Places lookup failed")
        }
        placesMilliseconds = milliseconds(since: placesStart)
      } else if !identity.merchantNames.isEmpty || !identity.addresses.isEmpty
        || !identity.phoneNumbers.isEmpty || !identity.websites.isEmpty
      {
        offline = true
        errors.append("places_lookup: unavailable")
        merchant = .abstained("Places lookup unavailable")
      } else {
        merchant = .abstained("insufficient identity evidence")
      }
      timings.append(
        StageTiming(
          stage: "places_lookup",
          milliseconds: placesMilliseconds
        )
      )
      timings.append(timing("merchant_resolution", since: resolutionStart))

      let sections = measure("section_decoder", timings: &timings) {
        sectionDecoder.assign(
          rows: rows,
          merchantName: merchant.merchantName,
          neighbors: evidenceNeighbors,
          knownEvidence: known
        )
      }
      let issues = measure("consistency_checks", timings: &timings) {
        consistencyChecker.check(
          rows: rows,
          sections: sections,
          merchant: merchant
        )
      }
      let candidates = measure("candidate_payloads", timings: &timings) {
        candidateBuilder.build(
          reference: reference,
          sections: sections,
          merchant: merchant,
          issues: issues,
          forceNeedsReview: offline
        )
      }
      let total = milliseconds(since: totalStart)
      return ReceiptUnderstandingReport(
        reference: reference,
        rows: rows,
        identitySignals: identity,
        merchantResolution: merchant,
        sections: sections,
        consistencyIssues: issues,
        candidates: candidates,
        timings: timings,
        totalMilliseconds: total,
        offlineFallback: offline,
        errors: errors
      )
    }

    private func measure<T>(
      _ name: String,
      timings: inout [StageTiming],
      operation: () -> T
    ) -> T {
      let start = DispatchTime.now().uptimeNanoseconds
      let result = operation()
      timings.append(timing(name, since: start))
      return result
    }

    private func timing(
      _ stage: String,
      since start: UInt64
    ) -> StageTiming {
      StageTiming(stage: stage, milliseconds: milliseconds(since: start))
    }

    private func milliseconds(since start: UInt64) -> Double {
      Double(DispatchTime.now().uptimeNanoseconds - start) / 1_000_000
    }
  }

#endif
