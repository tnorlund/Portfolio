import Foundation

#if os(macOS)

  public enum ReceiptUnderstandingConstants {
    public static let schemaVersion = 1
    public static let groupingVersion = "visual-rows-v1"
    public static let modelVersion = "swift-receipt-understanding-shadow-v1"
    public static let embeddingModel = "text-embedding-3-small"
    public static let embeddingDimensions = 1_536
    public static let sectionModelSource = "upload-determinism-v1"
    public static let sectionNeighborCount = 15
    public static let maximumKnownReceiptReferences = 15
    public static let embeddingKNNWeight = 1.0
    public static let minimumSectionCandidateConfidence = 0.6
  }

  public struct ReceiptReference: Codable, Equatable, Hashable, Sendable {
    public let imageID: String
    public let receiptID: Int

    public init(imageID: String, receiptID: Int) {
      self.imageID = imageID
      self.receiptID = receiptID
    }
  }

  public struct VisualRowBounds: Codable, Equatable, Sendable {
    public let xMin: Double
    public let yMin: Double
    public let xMax: Double
    public let yMax: Double

    public init(xMin: Double, yMin: Double, xMax: Double, yMax: Double) {
      self.xMin = xMin
      self.yMin = yMin
      self.xMax = xMax
      self.yMax = yMax
    }
  }

  public struct LayoutWordEvidence: Codable, Equatable, Sendable {
    public let lineID: Int
    public let wordID: Int
    public let text: String
    public let label: String
    public let confidence: Double

    public init(
      lineID: Int,
      wordID: Int,
      text: String,
      label: String,
      confidence: Double
    ) {
      self.lineID = lineID
      self.wordID = wordID
      self.text = text
      self.label = label
      self.confidence = confidence
    }
  }

  public struct VisualReceiptRow: Codable, Equatable, Sendable {
    public let rowID: Int
    public let lineIDs: [Int]
    public let text: String
    public let embeddingInput: String
    public let bounds: VisualRowBounds
    public let priceColumnX: Double?
    public let labelText: String?
    public let amountText: String?
    public let layoutEvidence: [LayoutWordEvidence]

    public init(
      rowID: Int,
      lineIDs: [Int],
      text: String,
      embeddingInput: String,
      bounds: VisualRowBounds,
      priceColumnX: Double? = nil,
      labelText: String? = nil,
      amountText: String? = nil,
      layoutEvidence: [LayoutWordEvidence] = []
    ) {
      self.rowID = rowID
      self.lineIDs = lineIDs
      self.text = text
      self.embeddingInput = embeddingInput
      self.bounds = bounds
      self.priceColumnX = priceColumnX
      self.labelText = labelText
      self.amountText = amountText
      self.layoutEvidence = layoutEvidence
    }
  }

  public struct SectionNeighbor: Codable, Equatable, Sendable {
    public let reference: ReceiptReference
    public let lineID: Int
    public let rowLineIDs: [Int]
    public let document: String?
    public let cosineSimilarity: Double
    public let metadataSection: String?
    public let metadataMerchantName: String?

    public init(
      reference: ReceiptReference,
      lineID: Int,
      rowLineIDs: [Int],
      document: String?,
      cosineSimilarity: Double,
      metadataSection: String?,
      metadataMerchantName: String?
    ) {
      self.reference = reference
      self.lineID = lineID
      self.rowLineIDs = rowLineIDs
      self.document = document
      self.cosineSimilarity = cosineSimilarity
      self.metadataSection = metadataSection
      self.metadataMerchantName = metadataMerchantName
    }
  }

  public struct KnownReceiptEvidence: Codable, Equatable, Sendable {
    public let reference: ReceiptReference
    public let placeID: String?
    public let merchantName: String?
    public let formattedAddress: String?
    public let phoneNumber: String?
    public let website: String?
    public let placeValidationStatus: String?
    public let placeConfidence: Double?
    public let validSectionByLine: [Int: String]

    public init(
      reference: ReceiptReference,
      placeID: String? = nil,
      merchantName: String? = nil,
      formattedAddress: String? = nil,
      phoneNumber: String? = nil,
      website: String? = nil,
      placeValidationStatus: String? = nil,
      placeConfidence: Double? = nil,
      validSectionByLine: [Int: String] = [:]
    ) {
      self.reference = reference
      self.placeID = placeID
      self.merchantName = merchantName
      self.formattedAddress = formattedAddress
      self.phoneNumber = phoneNumber
      self.website = website
      self.placeValidationStatus = placeValidationStatus
      self.placeConfidence = placeConfidence
      self.validSectionByLine = validSectionByLine
    }

    public var isHumanValidatedPlace: Bool {
      let status = placeValidationStatus?
        .trimmingCharacters(in: .whitespacesAndNewlines)
        .uppercased()
      return status == "MATCHED" && (placeConfidence ?? 0) >= 0.8
    }
  }

  public struct ReceiptIdentitySignals: Codable, Equatable, Sendable {
    public let merchantNames: [String]
    public let addresses: [String]
    public let phoneNumbers: [String]
    public let websites: [String]

    public init(
      merchantNames: [String] = [],
      addresses: [String] = [],
      phoneNumbers: [String] = [],
      websites: [String] = []
    ) {
      self.merchantNames = merchantNames
      self.addresses = addresses
      self.phoneNumbers = phoneNumbers
      self.websites = websites
    }
  }

  public enum MerchantResolutionSource: String, Codable, Sendable {
    case knownReceipt = "KNOWN_RECEIPT"
    case places = "PLACES"
    case abstained = "ABSTAINED"
  }

  public struct MerchantResolution: Codable, Equatable, Sendable {
    public let merchantName: String?
    public let placeID: String?
    public let formattedAddress: String?
    public let confidence: Double
    public let source: MerchantResolutionSource
    public let matchedFields: [String]
    public let evidence: [String]
    public let abstainReason: String?

    public init(
      merchantName: String?,
      placeID: String?,
      formattedAddress: String?,
      confidence: Double,
      source: MerchantResolutionSource,
      matchedFields: [String] = [],
      evidence: [String] = [],
      abstainReason: String? = nil
    ) {
      self.merchantName = merchantName
      self.placeID = placeID
      self.formattedAddress = formattedAddress
      self.confidence = confidence
      self.source = source
      self.matchedFields = matchedFields
      self.evidence = evidence
      self.abstainReason = abstainReason
    }

    public static func abstained(_ reason: String) -> MerchantResolution {
      MerchantResolution(
        merchantName: nil,
        placeID: nil,
        formattedAddress: nil,
        confidence: 0,
        source: .abstained,
        abstainReason: reason
      )
    }
  }

  public struct PlacesCandidate: Codable, Equatable, Sendable {
    public let merchantName: String
    public let placeID: String
    public let formattedAddress: String?
    public let phoneNumber: String?
    public let website: String?
    public let confidence: Double
    public let matchedFields: [String]

    public init(
      merchantName: String,
      placeID: String,
      formattedAddress: String? = nil,
      phoneNumber: String? = nil,
      website: String? = nil,
      confidence: Double,
      matchedFields: [String] = []
    ) {
      self.merchantName = merchantName
      self.placeID = placeID
      self.formattedAddress = formattedAddress
      self.phoneNumber = phoneNumber
      self.website = website
      self.confidence = confidence
      self.matchedFields = matchedFields
    }
  }

  public struct SectionAssignment: Codable, Equatable, Sendable {
    public let rowID: Int
    public let lineIDs: [Int]
    public let sectionType: String
    public let confidence: Double
    public let neighborConfidence: Double?
    public let neighborCount: Int

    public init(
      rowID: Int,
      lineIDs: [Int],
      sectionType: String,
      confidence: Double,
      neighborConfidence: Double? = nil,
      neighborCount: Int = 0
    ) {
      self.rowID = rowID
      self.lineIDs = lineIDs
      self.sectionType = sectionType
      self.confidence = confidence
      self.neighborConfidence = neighborConfidence
      self.neighborCount = neighborCount
    }
  }

  public enum ConsistencySeverity: String, Codable, Sendable {
    case warning = "WARNING"
    case conflict = "CONFLICT"
  }

  public struct ConsistencyIssue: Codable, Equatable, Sendable {
    public let code: String
    public let severity: ConsistencySeverity
    public let message: String
    public let rowIDs: [Int]

    public init(
      code: String,
      severity: ConsistencySeverity,
      message: String,
      rowIDs: [Int] = []
    ) {
      self.code = code
      self.severity = severity
      self.message = message
      self.rowIDs = rowIDs
    }
  }

  public enum CandidateValidationStatus: String, Codable, Sendable {
    case pending = "PENDING"
    case needsReview = "NEEDS_REVIEW"
  }

  public enum CandidateEntityType: String, Codable, Sendable {
    case section = "RECEIPT_SECTION"
    case place = "RECEIPT_PLACE"
  }

  public struct CandidateWrite: Codable, Equatable, Sendable {
    public let entityType: CandidateEntityType
    public let idempotencyKey: String
    public let conditionExpression: String
    public let validationStatus: CandidateValidationStatus
    public let confidence: Double
    public let modelVersion: String
    public let provenance: [String]
    public let sectionType: String?
    public let lineIDs: [Int]
    public let rowIDs: [Int]
    public let merchantName: String?
    public let placeID: String?
    public let evidence: [String]

    public init(
      entityType: CandidateEntityType,
      idempotencyKey: String,
      conditionExpression: String =
        "attribute_not_exists(PK) AND attribute_not_exists(SK)",
      validationStatus: CandidateValidationStatus,
      confidence: Double,
      modelVersion: String = ReceiptUnderstandingConstants.modelVersion,
      provenance: [String],
      sectionType: String? = nil,
      lineIDs: [Int] = [],
      rowIDs: [Int] = [],
      merchantName: String? = nil,
      placeID: String? = nil,
      evidence: [String] = []
    ) {
      self.entityType = entityType
      self.idempotencyKey = idempotencyKey
      self.conditionExpression = conditionExpression
      self.validationStatus = validationStatus
      self.confidence = confidence
      self.modelVersion = modelVersion
      self.provenance = provenance
      self.sectionType = sectionType
      self.lineIDs = lineIDs
      self.rowIDs = rowIDs
      self.merchantName = merchantName
      self.placeID = placeID
      self.evidence = evidence
    }
  }

  public struct StageTiming: Codable, Equatable, Sendable {
    public let stage: String
    public let milliseconds: Double

    public init(stage: String, milliseconds: Double) {
      self.stage = stage
      self.milliseconds = milliseconds
    }
  }

  public struct ReceiptUnderstandingReport: Codable, Equatable, Sendable {
    public let schemaVersion: Int
    public let mode: String
    public let reference: ReceiptReference
    public let modelVersion: String
    public let groupingVersion: String
    public let embeddingModel: String
    public let embeddingDimensions: Int
    public let rows: [VisualReceiptRow]
    public let identitySignals: ReceiptIdentitySignals
    public let merchantResolution: MerchantResolution
    public let sections: [SectionAssignment]
    public let consistencyIssues: [ConsistencyIssue]
    public let candidates: [CandidateWrite]
    public let timings: [StageTiming]
    public let totalMilliseconds: Double
    public let offlineFallback: Bool
    public let errors: [String]

    public init(
      reference: ReceiptReference,
      rows: [VisualReceiptRow],
      identitySignals: ReceiptIdentitySignals,
      merchantResolution: MerchantResolution,
      sections: [SectionAssignment],
      consistencyIssues: [ConsistencyIssue],
      candidates: [CandidateWrite],
      timings: [StageTiming],
      totalMilliseconds: Double,
      offlineFallback: Bool,
      errors: [String]
    ) {
      self.schemaVersion = ReceiptUnderstandingConstants.schemaVersion
      self.mode = "SHADOW"
      self.reference = reference
      self.modelVersion = ReceiptUnderstandingConstants.modelVersion
      self.groupingVersion = ReceiptUnderstandingConstants.groupingVersion
      self.embeddingModel = ReceiptUnderstandingConstants.embeddingModel
      self.embeddingDimensions =
        ReceiptUnderstandingConstants.embeddingDimensions
      self.rows = rows
      self.identitySignals = identitySignals
      self.merchantResolution = merchantResolution
      self.sections = sections
      self.consistencyIssues = consistencyIssues
      self.candidates = candidates
      self.timings = timings
      self.totalMilliseconds = totalMilliseconds
      self.offlineFallback = offlineFallback
      self.errors = errors
    }
  }

  public protocol RowEmbeddingProviding: Sendable {
    func embedRows(_ inputs: [String]) async throws -> [[Double]]
  }

  public protocol ReceiptNeighborQuerying: Sendable {
    func queryRows(
      embeddings: [[Double]],
      excluding reference: ReceiptReference,
      nResults: Int
    ) async throws -> [[SectionNeighbor]]
  }

  public protocol KnownReceiptEvidenceProviding: Sendable {
    func evidence(
      for references: Set<ReceiptReference>
    ) async throws -> [ReceiptReference: KnownReceiptEvidence]
  }

  public protocol PlacesLookingUp: Sendable {
    func lookup(identity: ReceiptIdentitySignals) async throws -> PlacesCandidate?
  }

  public protocol ReceiptUnderstandingAnalyzing: Sendable {
    func analyze(
      reference: ReceiptReference,
      lines: [Line],
      predictions: [LinePrediction]
    ) async -> ReceiptUnderstandingReport
  }

#endif
