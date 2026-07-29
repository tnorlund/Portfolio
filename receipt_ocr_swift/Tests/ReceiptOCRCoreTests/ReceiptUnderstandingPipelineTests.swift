import CoreGraphics
import XCTest

@testable import ReceiptOCRCore

final class ReceiptUnderstandingPipelineTests: XCTestCase {
  private enum Failure: Error { case unavailable }

  private struct FailingEmbedder: RowEmbeddingProviding {
    func embedRows(_ inputs: [String]) async throws -> [[Double]] {
      throw Failure.unavailable
    }
  }

  private struct MissingEmbedder: RowEmbeddingProviding {
    func embedRows(_ inputs: [String]) async throws -> [[Double]] { [] }
  }

  private struct StaticEmbedder: RowEmbeddingProviding {
    func embedRows(_ inputs: [String]) async throws -> [[Double]] {
      inputs.map {
        _ in
        Array(
          repeating: 0.1,
          count: ReceiptUnderstandingConstants.embeddingDimensions
        )
      }
    }
  }

  private struct FailingNeighbors: ReceiptNeighborQuerying {
    func queryRows(
      embeddings: [[Double]],
      excluding reference: ReceiptReference,
      nResults: Int
    ) async throws -> [[SectionNeighbor]] {
      throw Failure.unavailable
    }
  }

  private struct EmptyNeighbors: ReceiptNeighborQuerying {
    func queryRows(
      embeddings: [[Double]],
      excluding reference: ReceiptReference,
      nResults: Int
    ) async throws -> [[SectionNeighbor]] {
      Array(repeating: [], count: embeddings.count)
    }
  }

  private struct FailingPlaces: PlacesLookingUp {
    func lookup(
      identity: ReceiptIdentitySignals
    ) async throws -> PlacesCandidate? {
      throw Failure.unavailable
    }
  }

  private struct ManyNeighbors: ReceiptNeighborQuerying {
    func queryRows(
      embeddings: [[Double]],
      excluding reference: ReceiptReference,
      nResults: Int
    ) async throws -> [[SectionNeighbor]] {
      embeddings.map { _ in
        (0..<30).map { index in
          SectionNeighbor(
            reference: ReceiptReference(
              imageID: "known-\(index)",
              receiptID: 1
            ),
            lineID: 1,
            rowLineIDs: [1],
            document: nil,
            cosineSimilarity: 1 - Double(index) / 100,
            metadataSection: nil,
            metadataMerchantName: nil
          )
        }
      }
    }
  }

  private actor EvidenceSpy: KnownReceiptEvidenceProviding {
    private var requested: Set<ReceiptReference> = []

    func evidence(
      for references: Set<ReceiptReference>
    ) async throws -> [ReceiptReference: KnownReceiptEvidence] {
      requested = references
      return [:]
    }

    func requestedReferences() -> Set<ReceiptReference> {
      requested
    }
  }

  private static var priorURL: URL {
    URL(fileURLWithPath: #filePath)
      .deletingLastPathComponent()
      .deletingLastPathComponent()
      .deletingLastPathComponent()
      .appendingPathComponent(
        "receipt_upload/receipt_upload/assets/section_order_priors_v2.json"
      )
  }

  private func row(
    _ id: Int,
    _ text: String,
    y: Double,
    amount: String? = nil,
    labels: [String] = []
  ) -> VisualReceiptRow {
    VisualReceiptRow(
      rowID: id,
      lineIDs: [id],
      text: text,
      embeddingInput: "<EDGE>\n\(text)\n<EDGE>",
      bounds: VisualRowBounds(
        xMin: 0.05,
        yMin: y,
        xMax: 0.95,
        yMax: y + 0.04
      ),
      priceColumnX: amount == nil ? nil : 0.95,
      labelText: amount == nil
        ? nil
        : text.replacingOccurrences(of: amount!, with: "")
          .trimmingCharacters(in: .whitespaces),
      amountText: amount,
      layoutEvidence: labels.enumerated().map {
        LayoutWordEvidence(
          lineID: id,
          wordID: $0.offset + 1,
          text: text.split(separator: " ").first.map(String.init) ?? text,
          label: "B-\($0.element)",
          confidence: 0.95
        )
      }
    )
  }

  private func line(_ text: String) -> Line {
    let point = CodablePoint(x: 0, y: 0)
    return Line(
      text: text,
      boundingBox: NormalizedRect(
        x: 0.05,
        y: 0.8,
        width: 0.9,
        height: 0.04
      ),
      topLeft: point,
      topRight: point,
      bottomLeft: point,
      bottomRight: point,
      angleDegrees: 0,
      angleRadians: 0,
      confidence: 1,
      words: []
    )
  }

  func testPythonGoldenSectionAssignmentsAndConfidenceParity() throws {
    let decoder = try DeterministicSectionDecoder(
      priorURL: Self.priorURL
    )
    let rows = [
      row(1, "SPROUTS FARMERS MARKET", y: 0.9),
      row(2, "123 MAIN ST", y: 0.8),
      row(3, "ORGANIC APPLES 4.99", y: 0.6, amount: "4.99"),
      row(4, "SUBTOTAL 4.99", y: 0.3, amount: "4.99"),
      row(5, "TAX 0.41", y: 0.25, amount: "0.41"),
      row(6, "TOTAL 5.40", y: 0.2, amount: "5.40"),
      row(7, "VISA 5.40", y: 0.1, amount: "5.40"),
    ]

    let assignments = decoder.assign(
      rows: rows,
      merchantName: "Sprouts Farmers Market"
    )

    XCTAssertEqual(
      assignments.map(\.sectionType),
      [
        "STOREFRONT", "ITEMS", "ITEMS", "SUMMARY", "SUMMARY",
        "SUMMARY", "TOTAL_LINE",
      ]
    )
    let pythonConfidences = [
      0.999_921_212_448_287_6,
      0.073_218_084_870_463_19,
      0.997_576_809_809_947_1,
      0.983_596_969_525_279_6,
      0.997_122_693_335_959_8,
      0.133_612_048_032_665_3,
      0.177_567_111_292_142_84,
    ]
    for (actual, expected) in zip(
      assignments.map(\.confidence),
      pythonConfidences
    ) {
      XCTAssertEqual(actual, expected, accuracy: 1e-10)
    }
  }

  func testMalformedPriorIsRejectedBeforeDecoding() throws {
    let data = try Data(contentsOf: Self.priorURL)
    var root = try XCTUnwrap(
      JSONSerialization.jsonObject(with: data) as? [String: Any]
    )
    var global = try XCTUnwrap(root["global"] as? [String: Any])
    var models = try XCTUnwrap(
      global["section_models"] as? [String: Any]
    )
    let section = try XCTUnwrap(
      (global["sections"] as? [String])?.first
    )
    var model = try XCTUnwrap(models[section] as? [String: Any])
    var features = try XCTUnwrap(model["features"] as? [String: Any])
    features.removeValue(forKey: "position")
    model["features"] = features
    models[section] = model
    global["section_models"] = models
    root["global"] = global

    let url = FileManager.default.temporaryDirectory.appendingPathComponent(
      "malformed-section-prior-\(UUID().uuidString).json"
    )
    defer { try? FileManager.default.removeItem(at: url) }
    try JSONSerialization.data(withJSONObject: root).write(to: url)

    XCTAssertThrowsError(
      try DeterministicSectionDecoder(priorURL: url)
    )
  }

  func testCosineNeighborsRequireValidDynamoSectionEvidence() throws {
    let decoder = try DeterministicSectionDecoder(
      priorURL: Self.priorURL
    )
    let reference = ReceiptReference(imageID: "known", receiptID: 2)
    let neighbors = [
      SectionNeighbor(
        reference: reference,
        lineID: 9,
        rowLineIDs: [9, 10],
        document: "VISA 5.40",
        cosineSimilarity: 0.95,
        metadataSection: "ITEMS",
        metadataMerchantName: "Store"
      )
    ]
    let known = KnownReceiptEvidence(
      reference: reference,
      validSectionByLine: [9: "PAYMENT", 10: "PAYMENT"]
    )

    let assignment = try XCTUnwrap(
      decoder.assign(
        rows: [row(1, "VISA 5.40", y: 0.1, amount: "5.40")],
        merchantName: nil,
        neighbors: [neighbors],
        knownEvidence: [reference: known]
      ).first
    )

    XCTAssertEqual(assignment.neighborCount, 1)
    XCTAssertNotNil(assignment.neighborConfidence)
    // The unverified Chroma metadata says ITEMS and must not be used.
    XCTAssertNotEqual(assignment.sectionType, "ITEMS")
  }

  func testMerchantResolutionAbstainsOnConflictingIdentity() {
    let resolver = ReceiptIdentityResolver()
    let reference = ReceiptReference(imageID: "known", receiptID: 3)
    let identity = ReceiptIdentitySignals(
      merchantNames: ["Completely Different Shop"]
    )
    let neighbors = [
      [
        SectionNeighbor(
          reference: reference,
          lineID: 1,
          rowLineIDs: [1],
          document: nil,
          cosineSimilarity: 0.95,
          metadataSection: nil,
          metadataMerchantName: nil
        )
      ]
    ]
    let known = KnownReceiptEvidence(
      reference: reference,
      placeID: "place-1",
      merchantName: "Trusted Grocery",
      placeValidationStatus: "MATCHED",
      placeConfidence: 0.99
    )

    let result = resolver.resolveKnown(
      identity: identity,
      neighbors: neighbors,
      knownEvidence: [reference: known]
    )

    XCTAssertNil(result.resolution)
    XCTAssertEqual(
      result.conflict,
      "LayoutLM identity conflicts with known receipt identity"
    )
  }

  func testMerchantResolutionUsesReliableKnownReceiptEvidence() throws {
    let resolver = ReceiptIdentityResolver()
    let reference = ReceiptReference(imageID: "known", receiptID: 4)
    let known = KnownReceiptEvidence(
      reference: reference,
      placeID: "place-1",
      merchantName: "Trusted Grocery",
      formattedAddress: "123 Main Street",
      placeValidationStatus: "MATCHED",
      placeConfidence: 0.99
    )
    let result = resolver.resolveKnown(
      identity: ReceiptIdentitySignals(
        merchantNames: ["Trusted Grocery"],
        addresses: ["123 Main Street"]
      ),
      neighbors: [
        [
          SectionNeighbor(
            reference: reference,
            lineID: 1,
            rowLineIDs: [1],
            document: nil,
            cosineSimilarity: 0.95,
            metadataSection: nil,
            metadataMerchantName: nil
          )
        ]
      ],
      knownEvidence: [reference: known]
    )

    let resolution = try XCTUnwrap(result.resolution)
    XCTAssertNil(result.conflict)
    XCTAssertEqual(resolution.source, .knownReceipt)
    XCTAssertEqual(resolution.placeID, "place-1")
    XCTAssertEqual(
      resolution.matchedFields,
      ["merchant_name", "address"]
    )
  }

  func testUnknownMerchantAbstainsInsteadOfCreatingNegativePlace() {
    let resolver = ReceiptIdentityResolver()
    let result = resolver.resolvePlaces(
      identity: ReceiptIdentitySignals(),
      candidate: nil
    )
    XCTAssertEqual(result.source, .abstained)
    XCTAssertNil(result.placeID)
  }

  func testPunctuationAndTrivialPhoneDoNotCountAsIdentityMatches() {
    let resolver = ReceiptIdentityResolver()
    let reference = ReceiptReference(imageID: "known", receiptID: 5)
    let known = KnownReceiptEvidence(
      reference: reference,
      placeID: "place-1",
      merchantName: "Trusted Grocery",
      formattedAddress: "123 Main Street",
      phoneNumber: "0000000000",
      placeValidationStatus: "MATCHED",
      placeConfidence: 0.99
    )
    let result = resolver.resolveKnown(
      identity: ReceiptIdentitySignals(
        addresses: ["---"],
        phoneNumbers: ["0000000000"]
      ),
      neighbors: [
        [
          SectionNeighbor(
            reference: reference,
            lineID: 1,
            rowLineIDs: [1],
            document: nil,
            cosineSimilarity: 0.95,
            metadataSection: nil,
            metadataMerchantName: nil
          )
        ]
      ],
      knownEvidence: [reference: known]
    )

    XCTAssertNil(result.resolution)
    XCTAssertNotNil(result.conflict)
  }

  func testConflictsCreateNeedsReviewPayloadsAndNeverValidPayloads() {
    let candidates = ReceiptCandidateBuilder().build(
      reference: ReceiptReference(imageID: "image", receiptID: 1),
      sections: [
        SectionAssignment(
          rowID: 1,
          lineIDs: [1],
          sectionType: "ITEMS",
          confidence: 0.8
        )
      ],
      merchant: .abstained("identity conflict"),
      issues: [
        ConsistencyIssue(
          code: "MERCHANT_IDENTITY_CONFLICT",
          severity: .conflict,
          message: "conflict"
        )
      ]
    )

    XCTAssertEqual(candidates.map(\.validationStatus), [.needsReview])
    XCTAssertEqual(
      candidates.first?.conditionExpression,
      "attribute_not_exists(PK) AND attribute_not_exists(SK)"
    )
    XCTAssertEqual(
      candidates.first?.idempotencyKey,
      "IMAGE#image#RECEIPT#00001#SECTION#ITEMS"
    )
  }

  func testUncertainSectionsNeedReviewWhileHighConfidenceSectionsStayPending() {
    let builder = ReceiptCandidateBuilder()
    let reference = ReceiptReference(imageID: "image", receiptID: 1)
    let uncertain = builder.build(
      reference: reference,
      sections: [
        SectionAssignment(
          rowID: 1,
          lineIDs: [1],
          sectionType: "ITEMS",
          confidence: 0.59
        )
      ],
      merchant: .abstained("unknown"),
      issues: []
    )
    let confident = builder.build(
      reference: reference,
      sections: [
        SectionAssignment(
          rowID: 1,
          lineIDs: [1],
          sectionType: "ITEMS",
          confidence: 0.9
        )
      ],
      merchant: .abstained("unknown"),
      issues: []
    )

    XCTAssertEqual(uncertain.first?.validationStatus, .needsReview)
    XCTAssertEqual(confident.first?.validationStatus, .pending)
  }

  func testOpenAIFailureFallsBackOfflineAndStillDecodesSections() async throws {
    let pipeline = try ReceiptUnderstandingPipeline(
      priorURL: Self.priorURL,
      embedder: FailingEmbedder()
    )

    let report = await pipeline.analyze(
      reference: ReceiptReference(imageID: "image", receiptID: 1),
      lines: [line("TOTAL 5.40")],
      predictions: [
        LinePrediction(
          tokens: ["TOTAL", "5.40"],
          labels: ["B-GRAND_TOTAL", "I-GRAND_TOTAL"],
          confidences: [0.9, 0.9],
          allProbabilities: nil
        )
      ]
    )

    XCTAssertTrue(report.offlineFallback)
    XCTAssertFalse(report.sections.isEmpty)
    XCTAssertTrue(
      report.candidates.allSatisfy {
        $0.validationStatus == .needsReview
          && $0.provenance.contains("offline_fallback")
      }
    )
    XCTAssertTrue(
      report.errors.contains {
        $0.hasPrefix("openai_embeddings:")
      }
    )
    XCTAssertGreaterThanOrEqual(report.totalMilliseconds, 0)
    XCTAssertTrue(
      report.timings.contains { $0.stage == "section_decoder" }
    )
  }

  func testMissingEmbeddingsAndChromaFailureAreNonfatal() async throws {
    let missingPipeline = try ReceiptUnderstandingPipeline(
      priorURL: Self.priorURL,
      embedder: MissingEmbedder()
    )
    let missing = await missingPipeline.analyze(
      reference: ReceiptReference(imageID: "image", receiptID: 1),
      lines: [line("TOTAL 5.40")],
      predictions: []
    )
    XCTAssertTrue(missing.offlineFallback)
    XCTAssertTrue(
      missing.errors.contains {
        $0.contains("missing or dimension-mismatched")
      }
    )
    XCTAssertFalse(missing.sections.isEmpty)
    XCTAssertTrue(
      missing.candidates.allSatisfy {
        $0.validationStatus == .needsReview
      }
    )

    let chromaPipeline = try ReceiptUnderstandingPipeline(
      priorURL: Self.priorURL,
      embedder: StaticEmbedder(),
      neighborQuery: FailingNeighbors()
    )
    let chroma = await chromaPipeline.analyze(
      reference: ReceiptReference(imageID: "image", receiptID: 1),
      lines: [line("TOTAL 5.40")],
      predictions: []
    )
    XCTAssertTrue(chroma.offlineFallback)
    XCTAssertTrue(
      chroma.errors.contains { $0.hasPrefix("chroma_query:") }
    )
    XCTAssertFalse(chroma.sections.isEmpty)
    XCTAssertTrue(
      chroma.candidates.allSatisfy {
        $0.validationStatus == .needsReview
      }
    )
  }

  func testPlacesFailureIsTimedAndMarksOfflineFallback() async throws {
    let pipeline = try ReceiptUnderstandingPipeline(
      priorURL: Self.priorURL,
      embedder: StaticEmbedder(),
      neighborQuery: EmptyNeighbors(),
      places: FailingPlaces()
    )
    let report = await pipeline.analyze(
      reference: ReceiptReference(imageID: "image", receiptID: 1),
      lines: [line("TRUSTED GROCERY")],
      predictions: [
        LinePrediction(
          tokens: ["TRUSTED", "GROCERY"],
          labels: ["B-MERCHANT_NAME", "I-MERCHANT_NAME"],
          confidences: [0.95, 0.95],
          allProbabilities: nil
        )
      ]
    )

    XCTAssertTrue(report.offlineFallback)
    XCTAssertTrue(
      report.errors.contains { $0.hasPrefix("places_lookup:") }
    )
    XCTAssertTrue(
      report.timings.contains { $0.stage == "places_lookup" }
    )
  }

  func testKnownReceiptHydrationIsBoundedToStrongestReferences() async throws {
    let evidence = EvidenceSpy()
    let pipeline = try ReceiptUnderstandingPipeline(
      priorURL: Self.priorURL,
      embedder: StaticEmbedder(),
      neighborQuery: ManyNeighbors(),
      knownEvidenceProvider: evidence
    )
    _ = await pipeline.analyze(
      reference: ReceiptReference(imageID: "image", receiptID: 1),
      lines: [line("TOTAL 5.40")],
      predictions: []
    )

    let references = await evidence.requestedReferences()
    XCTAssertEqual(
      references.count,
      ReceiptUnderstandingConstants.maximumKnownReceiptReferences
    )
    XCTAssertTrue(
      references.contains(
        ReceiptReference(imageID: "known-0", receiptID: 1)
      )
    )
    XCTAssertFalse(
      references.contains(
        ReceiptReference(imageID: "known-29", receiptID: 1)
      )
    )
  }

  func testConsistencyChecksLabelsSectionsAndArithmeticIndependently() {
    let rows = [
      row(
        1,
        "APPLE 4.00",
        y: 0.6,
        amount: "4.00",
        labels: ["PRODUCT_NAME"]
      ),
      row(
        2,
        "SUBTOTAL 4.00",
        y: 0.3,
        amount: "4.00",
        labels: ["SUBTOTAL"]
      ),
      row(
        3,
        "TAX 1.00",
        y: 0.2,
        amount: "1.00",
        labels: ["TAX"]
      ),
      row(
        4,
        "TOTAL 9.00",
        y: 0.1,
        amount: "9.00",
        labels: ["GRAND_TOTAL"]
      ),
    ]
    let assignments = rows.map {
      SectionAssignment(
        rowID: $0.rowID,
        lineIDs: $0.lineIDs,
        sectionType: "PAYMENT",
        confidence: 0.9
      )
    }
    let issues = ReceiptConsistencyChecker().check(
      rows: rows,
      sections: assignments,
      merchant: .abstained("unknown")
    )

    XCTAssertTrue(
      issues.contains { $0.code == "ITEM_LABEL_OUTSIDE_ITEMS" }
    )
    XCTAssertTrue(
      issues.contains { $0.code == "SUMMARY_LABEL_OUTSIDE_SUMMARY" }
    )
    XCTAssertTrue(
      issues.contains { $0.code == "RECEIPT_ARITHMETIC_CONFLICT" }
    )
  }
}
