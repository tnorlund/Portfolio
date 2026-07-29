import Foundation

public enum ChromaMode: String, Codable, Sendable {
  case read
  case write
}

public struct ChromaRetryPolicy: Equatable, Sendable {
  public let maxRetries: Int
  public let baseDelay: TimeInterval
  public let maxDelay: TimeInterval

  public init(
    maxRetries: Int = 4,
    baseDelay: TimeInterval = 0.5,
    maxDelay: TimeInterval = 8
  ) {
    self.maxRetries = maxRetries
    self.baseDelay = baseDelay
    self.maxDelay = maxDelay
  }
}

public struct ChromaConfiguration: Equatable, Sendable {
  public let apiKey: String
  public let tenant: String
  public let database: String
  public let mode: ChromaMode
  public let baseURL: URL
  public let requestTimeout: TimeInterval
  public let retryPolicy: ChromaRetryPolicy

  public init(
    apiKey: String,
    tenant: String,
    database: String,
    mode: ChromaMode = .read,
    baseURL: URL = URL(string: "https://api.trychroma.com/api/v2")!,
    requestTimeout: TimeInterval = 30,
    retryPolicy: ChromaRetryPolicy = ChromaRetryPolicy()
  ) throws {
    let normalizedAPIKey = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
    let normalizedTenant = tenant.trimmingCharacters(in: .whitespacesAndNewlines)
    let normalizedDatabase = database.trimmingCharacters(in: .whitespacesAndNewlines)

    guard !normalizedAPIKey.isEmpty else {
      throw ReceiptChromaError.configuration("apiKey must not be blank")
    }
    guard !normalizedTenant.isEmpty else {
      throw ReceiptChromaError.configuration("tenant must not be blank")
    }
    guard !normalizedDatabase.isEmpty else {
      throw ReceiptChromaError.configuration("database must not be blank")
    }
    guard
      normalizedDatabase.utf8.count
        <= ChromaCloudLimits.maximumDatabaseNameBytes
    else {
      throw ReceiptChromaError.configuration(
        "database exceeds \(ChromaCloudLimits.maximumDatabaseNameBytes) UTF-8 bytes"
      )
    }
    guard let scheme = baseURL.scheme?.lowercased(),
      let host = baseURL.host?.lowercased(),
      ["http", "https"].contains(scheme)
    else {
      throw ReceiptChromaError.configuration(
        "baseURL must be an absolute HTTP(S) URL"
      )
    }
    let isLoopback =
      host == "localhost" || host == "::1" || host.hasPrefix("127.")
    guard scheme == "https" || isLoopback else {
      throw ReceiptChromaError.configuration(
        "baseURL must use HTTPS unless it targets a loopback host"
      )
    }
    guard requestTimeout.isFinite, requestTimeout > 0 else {
      throw ReceiptChromaError.configuration("requestTimeout must be positive")
    }
    guard retryPolicy.maxRetries >= 0,
      retryPolicy.baseDelay.isFinite,
      retryPolicy.maxDelay.isFinite,
      retryPolicy.baseDelay >= 0,
      retryPolicy.maxDelay >= retryPolicy.baseDelay
    else {
      throw ReceiptChromaError.configuration(
        "retry delays must be non-negative and maxDelay must be at least baseDelay"
      )
    }

    self.apiKey = normalizedAPIKey
    self.tenant = normalizedTenant
    self.database = normalizedDatabase
    self.mode = mode
    self.baseURL = baseURL
    self.requestTimeout = requestTimeout
    self.retryPolicy = retryPolicy
  }
}

/// Limits enforced by the deployed Chroma Cloud ingestion path.
public enum ChromaCloudLimits {
  public static let upsertBatchSize = 250
  public static let maximumQueryResults = 300
  public static let maximumIDBytes = 128
  public static let maximumURIBytes = 256
  public static let maximumDocumentBytes = 16_384
  public static let maximumDatabaseNameBytes = 128
  public static let maximumCollectionNameBytes = 128
  public static let maximumMetadataKeys = 32
  public static let maximumMetadataKeyBytes = 36
  public static let maximumMetadataValueBytes = 8_182
  public static let maximumCollectionMetadataValueBytes = 256
  public static let maximumEmbeddingDimensions = 4_096
  public static let maximumWherePredicates = 8
  public static let maximumFullTextSearchBytes = 256
}
