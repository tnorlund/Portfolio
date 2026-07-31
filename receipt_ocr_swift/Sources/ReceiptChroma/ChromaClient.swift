import Foundation
import Logging

#if canImport(FoundationNetworking)
  import FoundationNetworking
#endif

/// An async Chroma Cloud v2 client for receipt vector collections.
///
/// This client intentionally covers the deployed Cloud read/write boundary.
/// The Python pipeline remains responsible for embedding generation, S3
/// deltas, compaction, and local persistent Chroma databases.
public actor ChromaClient {
  public typealias Sleeper = @Sendable (UInt64) async throws -> Void
  public typealias Jitter = @Sendable (TimeInterval) -> TimeInterval

  private let configuration: ChromaConfiguration
  private let transport: any ChromaHTTPTransport
  private let closeTransportOnClose: Bool
  private let sleeper: Sleeper
  private let jitter: Jitter
  private var logger: Logger
  private var collections: [String: ChromaCollection] = [:]
  private var closed = false

  private let encoder: JSONEncoder = {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    return encoder
  }()

  private let decoder = JSONDecoder()

  public init(
    configuration: ChromaConfiguration,
    transport: any ChromaHTTPTransport = URLSessionChromaHTTPTransport(),
    closeTransportOnClose: Bool = true,
    logger: Logger = Logger(label: "receipt-chroma"),
    sleeper: @escaping Sleeper = { nanoseconds in
      try await Task.sleep(nanoseconds: nanoseconds)
    },
    jitter: @escaping Jitter = { upperBound in
      guard upperBound > 0 else { return 0 }
      return Double.random(in: 0...upperBound)
    }
  ) {
    self.configuration = configuration
    self.transport = transport
    self.closeTransportOnClose = closeTransportOnClose
    self.logger = logger
    self.sleeper = sleeper
    self.jitter = jitter
  }

  /// Marks the client closed and releases its collection cache.
  ///
  /// Closing is idempotent. Any later operation throws ``ReceiptChromaError/closed``.
  public func close() async {
    guard !closed else { return }
    collections.removeAll()
    closed = true
    if closeTransportOnClose {
      await transport.close()
    }
  }

  public func getCollection(
    _ name: String,
    createIfMissing: Bool = false,
    metadata: ChromaMetadata? = nil
  ) async throws -> ChromaCollection {
    try ensureOpen()
    let normalizedName = try validateCollectionName(name)
    if let metadata {
      try validateMetadata(
        metadata,
        maximumValueBytes: ChromaCloudLimits.maximumCollectionMetadataValueBytes
      )
    }

    if let collection = collections[normalizedName] {
      return collection
    }

    do {
      let request = try makeRequest(
        method: "GET",
        path: collectionPath + [normalizedName]
      )
      let collection = try await send(request, as: ChromaCollection.self)
      collections[normalizedName] = collection
      return collection
    } catch ReceiptChromaError.http(let status, _, _, _) where status == 404 {
      guard createIfMissing, configuration.mode == .write else {
        throw ReceiptChromaError.collectionNotFound(normalizedName)
      }

      let payload = CreateCollectionRequest(
        name: normalizedName,
        metadata: metadata,
        getOrCreate: true
      )
      let request = try makeRequest(
        method: "POST",
        path: collectionPath,
        body: payload
      )
      let collection = try await send(request, as: ChromaCollection.self)
      collections[normalizedName] = collection
      return collection
    }
  }

  @discardableResult
  public func upsert(
    collectionName: String,
    ids: [String],
    embeddings: [[Double]],
    documents: [String?]? = nil,
    metadatas: [ChromaMetadata?]? = nil,
    uris: [String?]? = nil
  ) async throws -> Int {
    try ensureWriteable()
    try validateUpsert(
      ids: ids,
      embeddings: embeddings,
      documents: documents,
      metadatas: metadatas,
      uris: uris
    )

    let collection = try await getCollection(
      collectionName,
      createIfMissing: true
    )
    var completed = 0

    for lowerBound in stride(
      from: 0,
      to: ids.count,
      by: ChromaCloudLimits.upsertBatchSize
    ) {
      let upperBound = min(
        lowerBound + ChromaCloudLimits.upsertBatchSize,
        ids.count
      )
      let range = lowerBound..<upperBound
      let payload = UpsertRequest(
        ids: Array(ids[range]),
        embeddings: Array(embeddings[range]),
        documents: documents.map { Array($0[range]) },
        metadatas: metadatas.map { Array($0[range]) },
        uris: uris.map { Array($0[range]) }
      )

      do {
        let request = try makeRequest(
          method: "POST",
          path: collectionPath + [collection.id, "upsert"],
          body: payload
        )
        _ = try await send(request, as: EmptyResponse.self)
        completed = upperBound
      } catch {
        guard completed > 0 else { throw error }
        throw ReceiptChromaError.partialBatch(
          completed: completed,
          total: ids.count,
          message: error.localizedDescription
        )
      }
    }

    return completed
  }

  public func query(
    collectionName: String,
    queryEmbeddings: [[Double]],
    nResults: Int = 10,
    where whereFilter: ChromaFilter? = nil,
    whereDocument: ChromaFilter? = nil,
    ids: [String]? = nil,
    include: [ChromaInclude] = [.metadatas, .documents, .distances]
  ) async throws -> ChromaQueryResult {
    try ensureOpen()
    try validateEmbeddings(queryEmbeddings, field: "queryEmbeddings")
    guard nResults > 0 else {
      throw ReceiptChromaError.invalidRequest(
        "nResults must be positive"
      )
    }
    if let ids {
      try validateIDs(ids, maximumCount: ChromaCloudLimits.maximumQueryResults)
    }
    try validateFilter(whereFilter)
    try validateFilter(whereDocument, fullText: true)

    let collection = try await getCollection(collectionName)
    let payload = QueryRequest(
      queryEmbeddings: queryEmbeddings,
      nResults: min(nResults, ChromaCloudLimits.maximumQueryResults),
      whereFilter: whereFilter,
      whereDocument: whereDocument,
      ids: ids,
      include: include
    )
    let request = try makeRequest(
      method: "POST",
      path: collectionPath + [collection.id, "query"],
      body: payload
    )
    return try await send(request, as: ChromaQueryResult.self)
  }

  public func get(
    collectionName: String,
    ids: [String]? = nil,
    where whereFilter: ChromaFilter? = nil,
    whereDocument: ChromaFilter? = nil,
    include: [ChromaInclude] = [.metadatas, .documents, .embeddings],
    limit: Int? = nil,
    offset: Int? = nil
  ) async throws -> ChromaGetResult {
    try ensureOpen()
    guard ids != nil || whereFilter != nil || whereDocument != nil else {
      throw ReceiptChromaError.invalidRequest(
        "get requires ids, where, or whereDocument"
      )
    }
    try validatePagination(limit: limit, offset: offset)
    if let limit, limit > ChromaCloudLimits.maximumQueryResults {
      throw ReceiptChromaError.invalidRequest(
        "limit must not exceed \(ChromaCloudLimits.maximumQueryResults)"
      )
    }
    guard !include.contains(.distances) else {
      throw ReceiptChromaError.invalidRequest(
        "get does not support the distances include"
      )
    }
    if let ids {
      try validateIDs(ids, maximumCount: ChromaCloudLimits.maximumQueryResults)
    }
    try validateFilter(whereFilter)
    try validateFilter(whereDocument, fullText: true)

    let collection = try await getCollection(collectionName)
    let payload = GetRequest(
      ids: ids,
      whereFilter: whereFilter,
      whereDocument: whereDocument,
      include: include,
      limit: limit,
      offset: offset
    )
    let request = try makeRequest(
      method: "POST",
      path: collectionPath + [collection.id, "get"],
      body: payload
    )
    return try await send(request, as: ChromaGetResult.self)
  }

  public func delete(
    collectionName: String,
    ids: [String]? = nil,
    where whereFilter: ChromaFilter? = nil,
    whereDocument: ChromaFilter? = nil
  ) async throws {
    try ensureWriteable()
    guard ids != nil || whereFilter != nil || whereDocument != nil else {
      throw ReceiptChromaError.invalidRequest(
        "delete requires ids, where, or whereDocument"
      )
    }
    if let ids {
      try validateIDs(ids, maximumCount: ChromaCloudLimits.maximumQueryResults)
    }
    try validateFilter(whereFilter)
    try validateFilter(whereDocument, fullText: true)

    let collection = try await getCollection(collectionName)
    let payload = DeleteRequest(
      ids: ids,
      whereFilter: whereFilter,
      whereDocument: whereDocument
    )
    let request = try makeRequest(
      method: "POST",
      path: collectionPath + [collection.id, "delete"],
      body: payload
    )
    _ = try await send(request, as: EmptyResponse.self)
  }

  public func count(collectionName: String) async throws -> Int {
    try ensureOpen()
    let collection = try await getCollection(collectionName)
    let request = try makeRequest(
      method: "GET",
      path: collectionPath + [collection.id, "count"]
    )
    return try await send(request, as: Int.self)
  }

  public func listCollections() async throws -> [ChromaCollection] {
    try ensureOpen()
    let request = try makeRequest(method: "GET", path: collectionPath)
    let result = try await send(request, as: [ChromaCollection].self)
    for collection in result {
      collections[collection.name] = collection
    }
    return result
  }

  public func collectionExists(_ name: String) async throws -> Bool {
    try ensureOpen()
    let normalizedName = try validateCollectionName(name)
    let request = try makeRequest(
      method: "GET",
      path: collectionPath + [normalizedName]
    )

    do {
      let collection = try await send(request, as: ChromaCollection.self)
      collections[normalizedName] = collection
      return true
    } catch ReceiptChromaError.http(let status, _, _, _) where status == 404 {
      collections[normalizedName] = nil
      return false
    }
  }

  private var collectionPath: [String] {
    [
      "tenants",
      configuration.tenant,
      "databases",
      configuration.database,
      "collections",
    ]
  }

  private func ensureOpen() throws {
    guard !closed else {
      throw ReceiptChromaError.closed
    }
  }

  private func ensureWriteable() throws {
    try ensureOpen()
    guard configuration.mode == .write else {
      throw ReceiptChromaError.readOnly
    }
  }

  private func validateCollectionName(_ name: String) throws -> String {
    let normalizedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !normalizedName.isEmpty else {
      throw ReceiptChromaError.invalidRequest(
        "collection name must not be blank"
      )
    }
    guard
      normalizedName.utf8.count <= ChromaCloudLimits.maximumCollectionNameBytes
    else {
      throw ReceiptChromaError.invalidRequest(
        "collection name exceeds \(ChromaCloudLimits.maximumCollectionNameBytes) UTF-8 bytes"
      )
    }
    return normalizedName
  }

  private func makeRequest<Body: Encodable>(
    method: String,
    path: [String],
    body: Body
  ) throws -> URLRequest {
    var request = try makeRequest(method: method, path: path)
    request.httpBody = try encoder.encode(body)
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    return request
  }

  private func makeRequest(method: String, path: [String]) throws -> URLRequest {
    var url = configuration.baseURL
    for component in path {
      url.appendPathComponent(component)
    }

    guard let scheme = url.scheme, let host = url.host,
      !scheme.isEmpty, !host.isEmpty
    else {
      throw ReceiptChromaError.configuration(
        "could not build request URL"
      )
    }

    var request = URLRequest(
      url: url,
      timeoutInterval: configuration.requestTimeout
    )
    request.httpMethod = method
    request.setValue("application/json", forHTTPHeaderField: "Accept")
    request.setValue(
      configuration.apiKey,
      forHTTPHeaderField: "x-chroma-token"
    )
    return request
  }

  private func send<Response: Decodable>(
    _ request: URLRequest,
    as responseType: Response.Type
  ) async throws -> Response {
    let policy = configuration.retryPolicy

    for attempt in 0...policy.maxRetries {
      try Task.checkCancellation()

      let data: Data
      let response: HTTPURLResponse
      do {
        (data, response) = try await transport.data(for: request)
      } catch is CancellationError {
        throw CancellationError()
      } catch let error as ReceiptChromaError {
        throw error
      } catch {
        throw ReceiptChromaError.transport(error.localizedDescription)
      }

      if response.statusCode == 429, attempt < policy.maxRetries {
        let baseDelay = min(
          policy.baseDelay * pow(2, Double(attempt)),
          policy.maxDelay
        )
        let sampledJitter = jitter(baseDelay * 0.5)
        let delay = min(
          baseDelay
            + (sampledJitter.isFinite ? max(0, sampledJitter) : 0),
          policy.maxDelay
        )
        logger.warning(
          "Chroma rate limited; retrying",
          metadata: [
            "attempt": "\(attempt + 1)",
            "max_attempts": "\(policy.maxRetries + 1)",
            "delay_seconds": "\(delay)",
          ]
        )
        let maximumConvertibleNanoseconds = Double(UInt64.max - 2_047)
        let nanoseconds = UInt64(
          min(delay * 1_000_000_000, maximumConvertibleNanoseconds)
        )
        try await sleeper(nanoseconds)
        continue
      }

      guard (200..<300).contains(response.statusCode) else {
        throw decodeHTTPError(data: data, response: response)
      }

      let responseData = data.isEmpty ? Data("{}".utf8) : data
      do {
        return try decoder.decode(responseType, from: responseData)
      } catch {
        throw ReceiptChromaError.invalidResponse(
          "could not decode \(responseType): \(error.localizedDescription)"
        )
      }
    }

    throw ReceiptChromaError.invalidResponse(
      "request exhausted retries without a response"
    )
  }

  private func decodeHTTPError(
    data: Data,
    response: HTTPURLResponse
  ) -> ReceiptChromaError {
    let payload = try? decoder.decode(ChromaAPIError.self, from: data)
    let fallback = String(data: data, encoding: .utf8)?
      .trimmingCharacters(in: .whitespacesAndNewlines)
    let message =
      payload?.message ?? payload?.detail
      ?? (fallback?.isEmpty == false ? fallback! : "request failed")
    let traceID =
      response.value(forHTTPHeaderField: "chroma-trace-id")
      ?? response.value(forHTTPHeaderField: "x-chroma-trace-id")
    return .http(
      status: response.statusCode,
      type: payload?.error,
      message: message,
      traceID: traceID
    )
  }

  private func validateUpsert(
    ids: [String],
    embeddings: [[Double]],
    documents: [String?]?,
    metadatas: [ChromaMetadata?]?,
    uris: [String?]?
  ) throws {
    guard !ids.isEmpty else {
      throw ReceiptChromaError.invalidRequest(
        "upsert requires at least one record"
      )
    }
    try validateIDs(ids)
    try validateEmbeddings(embeddings, field: "embeddings")
    try validateCount(embeddings.count, field: "embeddings", expected: ids.count)

    if let documents {
      try validateCount(documents.count, field: "documents", expected: ids.count)
      for document in documents.compactMap({ $0 }) {
        guard document.utf8.count <= ChromaCloudLimits.maximumDocumentBytes else {
          throw ReceiptChromaError.invalidRequest(
            "document exceeds \(ChromaCloudLimits.maximumDocumentBytes) UTF-8 bytes"
          )
        }
      }
    }

    if let metadatas {
      try validateCount(metadatas.count, field: "metadatas", expected: ids.count)
      for metadata in metadatas.compactMap({ $0 }) {
        try validateMetadata(metadata)
      }
    }

    if let uris {
      try validateCount(uris.count, field: "uris", expected: ids.count)
      for uri in uris.compactMap({ $0 }) {
        guard uri.utf8.count <= ChromaCloudLimits.maximumURIBytes else {
          throw ReceiptChromaError.invalidRequest(
            "URI exceeds \(ChromaCloudLimits.maximumURIBytes) UTF-8 bytes"
          )
        }
      }
    }
  }

  private func validateIDs(
    _ ids: [String],
    maximumCount: Int? = nil
  ) throws {
    guard !ids.isEmpty else {
      throw ReceiptChromaError.invalidRequest(
        "record IDs must not be empty"
      )
    }
    if let maximumCount, ids.count > maximumCount {
      throw ReceiptChromaError.invalidRequest(
        "record IDs must not exceed \(maximumCount) per request"
      )
    }
    var seen = Set<String>()
    for id in ids {
      guard !id.isEmpty else {
        throw ReceiptChromaError.invalidRequest("record IDs must not be empty")
      }
      guard id.utf8.count <= ChromaCloudLimits.maximumIDBytes else {
        throw ReceiptChromaError.invalidRequest(
          "record ID exceeds \(ChromaCloudLimits.maximumIDBytes) UTF-8 bytes"
        )
      }
      guard seen.insert(id).inserted else {
        throw ReceiptChromaError.invalidRequest(
          "record IDs must be unique; duplicate '\(id)'"
        )
      }
    }
  }

  private func validateEmbeddings(
    _ embeddings: [[Double]],
    field: String
  ) throws {
    guard !embeddings.isEmpty else {
      throw ReceiptChromaError.invalidRequest(
        "\(field) must not be empty"
      )
    }
    guard let dimensions = embeddings.first?.count, dimensions > 0 else {
      throw ReceiptChromaError.invalidRequest(
        "\(field) vectors must not be empty"
      )
    }
    guard dimensions <= ChromaCloudLimits.maximumEmbeddingDimensions else {
      throw ReceiptChromaError.invalidRequest(
        "\(field) exceeds \(ChromaCloudLimits.maximumEmbeddingDimensions) dimensions"
      )
    }
    guard embeddings.allSatisfy({ $0.count == dimensions }) else {
      throw ReceiptChromaError.invalidRequest(
        "\(field) vectors must have equal dimensions"
      )
    }
    guard embeddings.joined().allSatisfy(\.isFinite) else {
      throw ReceiptChromaError.invalidRequest(
        "\(field) must contain only finite values"
      )
    }
  }

  private func validateMetadata(
    _ metadata: ChromaMetadata,
    maximumValueBytes: Int = ChromaCloudLimits.maximumMetadataValueBytes
  ) throws {
    guard !metadata.isEmpty else {
      throw ReceiptChromaError.invalidRequest(
        "metadata dictionaries must not be empty; use nil to omit metadata"
      )
    }
    guard metadata.count <= ChromaCloudLimits.maximumMetadataKeys else {
      throw ReceiptChromaError.invalidRequest(
        "metadata exceeds \(ChromaCloudLimits.maximumMetadataKeys) keys"
      )
    }

    for (key, value) in metadata {
      guard !key.isEmpty,
        !key.hasPrefix("#"),
        !key.hasPrefix("$"),
        key.utf8.count <= ChromaCloudLimits.maximumMetadataKeyBytes
      else {
        throw ReceiptChromaError.invalidRequest(
          "metadata keys must be 1...\(ChromaCloudLimits.maximumMetadataKeyBytes) UTF-8 bytes and must not start with '#' or '$'"
        )
      }
      guard isMetadataValue(value) else {
        throw ReceiptChromaError.invalidRequest(
          "metadata '\(key)' must be a scalar, null, or an array of scalars"
        )
      }
      guard metadataValueBytes(value) <= maximumValueBytes else {
        throw ReceiptChromaError.invalidRequest(
          "metadata '\(key)' exceeds \(maximumValueBytes) bytes"
        )
      }
    }
  }

  private func isMetadataValue(_ value: JSONValue) -> Bool {
    switch value {
    case .string, .integer, .bool, .null:
      return true
    case .number(let number):
      return number.isFinite
    case .array(let values):
      guard let first = values.first else { return false }
      let expectedKind = metadataArrayKind(first)
      guard expectedKind != nil else { return false }
      return values.allSatisfy { value in
        metadataArrayKind(value) == expectedKind
      }
    case .object:
      return false
    }
  }

  private func metadataArrayKind(_ value: JSONValue) -> Int? {
    switch value {
    case .string:
      return 0
    case .integer:
      return 1
    case .number(let number):
      return number.isFinite ? 1 : nil
    case .bool:
      return 2
    default:
      return nil
    }
  }

  private func metadataValueBytes(_ value: JSONValue) -> Int {
    switch value {
    case .string(let string):
      return string.utf8.count
    case .integer(let integer):
      return String(integer).utf8.count
    case .number(let number):
      return String(number).utf8.count
    case .bool(let boolean):
      return String(boolean).utf8.count
    case .null:
      return 4
    case .array(let values):
      return values.reduce(0) { $0 + metadataValueBytes($1) }
    case .object:
      return 0
    }
  }

  private func validateCount(
    _ actual: Int,
    field: String,
    expected: Int
  ) throws {
    guard actual == expected else {
      throw ReceiptChromaError.invalidRequest(
        "\(field) has \(actual) values; expected \(expected)"
      )
    }
  }

  private func validatePagination(limit: Int?, offset: Int?) throws {
    if let limit, limit <= 0 {
      throw ReceiptChromaError.invalidRequest("limit must be positive")
    }
    if let offset, offset < 0 {
      throw ReceiptChromaError.invalidRequest("offset must not be negative")
    }
  }

  private func validateFilter(
    _ filter: ChromaFilter?,
    fullText: Bool = false
  ) throws {
    guard let filter else { return }
    let predicateCount = countPredicates(in: .object(filter))
    guard predicateCount <= ChromaCloudLimits.maximumWherePredicates else {
      throw ReceiptChromaError.invalidRequest(
        "where filter exceeds \(ChromaCloudLimits.maximumWherePredicates) predicates"
      )
    }
    if fullText, containsOversizedString(in: .object(filter)) {
      throw ReceiptChromaError.invalidRequest(
        "full-text or regex filter exceeds \(ChromaCloudLimits.maximumFullTextSearchBytes) UTF-8 bytes"
      )
    }
  }

  private func countPredicates(in value: JSONValue) -> Int {
    switch value {
    case .object(let object):
      return object.reduce(into: 0) { count, entry in
        if entry.key == "$and" || entry.key == "$or" {
          count += countPredicates(in: entry.value)
        } else if entry.key.hasPrefix("$") {
          count += max(1, countPredicates(in: entry.value))
        } else {
          count += 1
        }
      }
    case .array(let values):
      return values.reduce(0) { $0 + countPredicates(in: $1) }
    default:
      return 0
    }
  }

  private func containsOversizedString(in value: JSONValue) -> Bool {
    switch value {
    case .string(let string):
      return string.utf8.count > ChromaCloudLimits.maximumFullTextSearchBytes
    case .array(let values):
      return values.contains(where: containsOversizedString)
    case .object(let object):
      return object.values.contains(where: containsOversizedString)
    default:
      return false
    }
  }
}

private struct CreateCollectionRequest: Encodable {
  let name: String
  let metadata: ChromaMetadata?
  let getOrCreate: Bool

  enum CodingKeys: String, CodingKey {
    case name
    case metadata
    case getOrCreate = "get_or_create"
  }
}

private struct UpsertRequest: Encodable {
  let ids: [String]
  let embeddings: [[Double]]
  let documents: [String?]?
  let metadatas: [ChromaMetadata?]?
  let uris: [String?]?
}

private struct QueryRequest: Encodable {
  let queryEmbeddings: [[Double]]
  let nResults: Int
  let whereFilter: ChromaFilter?
  let whereDocument: ChromaFilter?
  let ids: [String]?
  let include: [ChromaInclude]

  enum CodingKeys: String, CodingKey {
    case queryEmbeddings = "query_embeddings"
    case nResults = "n_results"
    case whereFilter = "where"
    case whereDocument = "where_document"
    case ids
    case include
  }
}

private struct GetRequest: Encodable {
  let ids: [String]?
  let whereFilter: ChromaFilter?
  let whereDocument: ChromaFilter?
  let include: [ChromaInclude]
  let limit: Int?
  let offset: Int?

  enum CodingKeys: String, CodingKey {
    case ids
    case whereFilter = "where"
    case whereDocument = "where_document"
    case include
    case limit
    case offset
  }
}

private struct DeleteRequest: Encodable {
  let ids: [String]?
  let whereFilter: ChromaFilter?
  let whereDocument: ChromaFilter?

  enum CodingKeys: String, CodingKey {
    case ids
    case whereFilter = "where"
    case whereDocument = "where_document"
  }
}

private struct EmptyResponse: Decodable {}

private struct ChromaAPIError: Decodable {
  let error: String?
  let message: String?
  let detail: String?
}
