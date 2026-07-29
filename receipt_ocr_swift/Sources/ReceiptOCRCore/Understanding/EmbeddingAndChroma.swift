import Foundation
import Logging
import ReceiptChroma

#if canImport(FoundationNetworking)
  import FoundationNetworking
#endif

#if os(macOS)

  public enum ReceiptEmbeddingError: Error, LocalizedError {
    case invalidConfiguration(String)
    case invalidResponse(String)
    case http(status: Int, body: String)

    public var errorDescription: String? {
      switch self {
      case .invalidConfiguration(let message):
        return message
      case .invalidResponse(let message):
        return "Invalid OpenAI embedding response: \(message)"
      case .http(let status, let body):
        return "OpenAI embedding request failed (\(status)): \(body)"
      }
    }
  }

  public protocol OpenAIEmbeddingHTTPTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
  }

  public actor URLSessionOpenAIEmbeddingTransport:
    OpenAIEmbeddingHTTPTransport
  {
    private let session: URLSession

    public init(timeout: TimeInterval = 30) {
      let configuration = URLSessionConfiguration.ephemeral
      configuration.timeoutIntervalForRequest = timeout
      configuration.timeoutIntervalForResource = timeout
      self.session = URLSession(configuration: configuration)
    }

    public func send(
      _ request: URLRequest
    ) async throws -> (Data, HTTPURLResponse) {
      let (data, response) = try await session.data(for: request)
      guard let http = response as? HTTPURLResponse else {
        throw ReceiptEmbeddingError.invalidResponse("non-HTTP response")
      }
      return (data, http)
    }
  }

  /// OpenAI embeddings provider pinned to the exact Python request contract.
  ///
  /// The payload contains only `input` and `model`; it intentionally omits a
  /// `dimensions` field because Python relies on the model's native 1,536
  /// dimensions. Response items are restored to request order by `index`.
  public struct OpenAIRowEmbeddingProvider: RowEmbeddingProviding {
    public typealias Sleeper = @Sendable (UInt64) async throws -> Void

    private let apiKey: String
    private let endpoint: URL
    private let transport: any OpenAIEmbeddingHTTPTransport
    private let maximumRetries: Int
    private let sleeper: Sleeper

    public init(
      apiKey: String,
      endpoint: URL = URL(string: "https://api.openai.com/v1/embeddings")!,
      transport: any OpenAIEmbeddingHTTPTransport =
        URLSessionOpenAIEmbeddingTransport(),
      maximumRetries: Int = 3,
      sleeper: @escaping Sleeper = {
        try await Task.sleep(nanoseconds: $0)
      }
    ) throws {
      let key = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
      guard !key.isEmpty else {
        throw ReceiptEmbeddingError.invalidConfiguration(
          "OpenAI API key must not be blank"
        )
      }
      guard
        endpoint.scheme?.lowercased() == "https"
          || endpoint.host == "localhost"
          || endpoint.host?.hasPrefix("127.") == true
      else {
        throw ReceiptEmbeddingError.invalidConfiguration(
          "OpenAI endpoint must use HTTPS unless it is loopback"
        )
      }
      guard maximumRetries >= 0 else {
        throw ReceiptEmbeddingError.invalidConfiguration(
          "maximumRetries must not be negative"
        )
      }
      self.apiKey = key
      self.endpoint = endpoint
      self.transport = transport
      self.maximumRetries = maximumRetries
      self.sleeper = sleeper
    }

    public func embedRows(_ inputs: [String]) async throws -> [[Double]] {
      guard !inputs.isEmpty else { return [] }
      let body = try JSONSerialization.data(
        withJSONObject: [
          "input": inputs,
          "model": ReceiptUnderstandingConstants.embeddingModel,
        ],
        options: [.sortedKeys]
      )
      var request = URLRequest(url: endpoint)
      request.httpMethod = "POST"
      request.httpBody = body
      request.setValue("application/json", forHTTPHeaderField: "Content-Type")
      request.setValue(
        "Bearer \(apiKey)",
        forHTTPHeaderField: "Authorization"
      )

      var attempt = 0
      while true {
        do {
          let (data, response) = try await transport.send(request)
          if (200..<300).contains(response.statusCode) {
            return try decode(data, expectedCount: inputs.count)
          }
          let message =
            String(data: data, encoding: .utf8)
            ?? "<\(data.count) response bytes>"
          let retryable =
            response.statusCode == 429 || response.statusCode >= 500
          guard retryable, attempt < maximumRetries else {
            throw ReceiptEmbeddingError.http(
              status: response.statusCode,
              body: message
            )
          }
        } catch let error as ReceiptEmbeddingError {
          throw error
        } catch {
          guard attempt < maximumRetries else { throw error }
        }
        let delay = UInt64(250 * (1 << attempt)) * 1_000_000
        attempt += 1
        try await sleeper(delay)
      }
    }

    private func decode(
      _ data: Data,
      expectedCount: Int
    ) throws -> [[Double]] {
      struct Response: Decodable {
        struct Item: Decodable {
          let index: Int
          let embedding: [Double]
        }
        let data: [Item]
      }
      let response = try JSONDecoder().decode(Response.self, from: data)
      guard response.data.count == expectedCount else {
        throw ReceiptEmbeddingError.invalidResponse(
          "expected \(expectedCount) vectors, got \(response.data.count)"
        )
      }
      let ordered = response.data.sorted { $0.index < $1.index }
      guard ordered.map(\.index) == Array(0..<expectedCount) else {
        throw ReceiptEmbeddingError.invalidResponse(
          "response indexes are missing or duplicated"
        )
      }
      for item in ordered
      where item.embedding.count
        != ReceiptUnderstandingConstants.embeddingDimensions
      {
        throw ReceiptEmbeddingError.invalidResponse(
          "index \(item.index) has \(item.embedding.count) dimensions; "
            + "expected \(ReceiptUnderstandingConstants.embeddingDimensions)"
        )
      }
      return ordered.map(\.embedding)
    }
  }

  public struct ReceiptChromaRowRecord: Equatable, Sendable {
    public let id: String
    public let document: String
    public let embeddingInput: String
    public let metadata: ChromaMetadata

    public init(
      id: String,
      document: String,
      embeddingInput: String,
      metadata: ChromaMetadata
    ) {
      self.id = id
      self.document = document
      self.embeddingInput = embeddingInput
      self.metadata = metadata
    }
  }

  /// Shared Swift representation of the Python `lines` collection contract.
  public enum ReceiptChromaRowContract {
    public static func record(
      reference: ReceiptReference,
      row: VisualReceiptRow,
      sourceLines: [Line],
      merchantName: String? = nil,
      sectionLabel: String? = nil,
      normalizedPhone10: String? = nil,
      normalizedFullAddress: String? = nil,
      normalizedURL: String? = nil
    ) -> ReceiptChromaRowRecord {
      let sourceByID = Dictionary(
        uniqueKeysWithValues: sourceLines.enumerated().map {
          ($0.offset + 1, $0.element)
        }
      )
      let rowLines = row.lineIDs.compactMap { sourceByID[$0] }
      let confidence =
        rowLines.isEmpty
        ? 0
        : rowLines.reduce(0.0) { $0 + Double($1.confidence) }
          / Double(rowLines.count)
      let id =
        "IMAGE#\(reference.imageID)#RECEIPT#"
        + String(format: "%05d", reference.receiptID)
        + "#LINE#" + String(format: "%05d", row.rowID)
      var metadata: ChromaMetadata = [
        "image_id": .string(reference.imageID),
        "receipt_id": .integer(Int64(reference.receiptID)),
        "line_id": .integer(Int64(row.rowID)),
        "text": .string(row.text),
        "confidence": .number(confidence),
        "avg_word_confidence": .number(confidence),
        "x": .number(row.bounds.xMin),
        "y": .number(row.bounds.yMin),
        "width": .number(row.bounds.xMax - row.bounds.xMin),
        "height": .number(row.bounds.yMax - row.bounds.yMin),
        "source": .string("openai_embedding_batch"),
        "row_line_ids": .string(
          "[" + row.lineIDs.map(String.init).joined(separator: ", ") + "]"
        ),
      ]
      if let merchantName = normalizedNonempty(merchantName) {
        metadata["merchant_name"] = .string(pythonTitle(merchantName))
      }
      if let sectionLabel = normalizedNonempty(sectionLabel) {
        metadata["section_label"] = .string(sectionLabel)
      }
      if let normalizedPhone10 = normalizedNonempty(normalizedPhone10) {
        metadata["normalized_phone_10"] = .string(normalizedPhone10)
      }
      if let normalizedFullAddress = normalizedNonempty(
        normalizedFullAddress
      ) {
        metadata["normalized_full_address"] = .string(normalizedFullAddress)
      }
      if let normalizedURL = normalizedNonempty(normalizedURL) {
        metadata["normalized_url"] = .string(normalizedURL)
      }
      return ReceiptChromaRowRecord(
        id: id,
        document: row.text,
        embeddingInput: row.embeddingInput,
        metadata: metadata
      )
    }

    private static func normalizedNonempty(_ value: String?) -> String? {
      let result = value?.trimmingCharacters(in: .whitespacesAndNewlines)
      return result?.isEmpty == false ? result : nil
    }

    private static func pythonTitle(_ value: String) -> String {
      var result = ""
      var previousWasCased = false
      for character in value {
        let isCased = character.isLetter
        if isCased {
          result +=
            previousWasCased
            ? String(character).lowercased()
            : String(character).uppercased()
        } else {
          result.append(character)
        }
        previousWasCased = isCased
      }
      return result
    }
  }

  /// Read-only Chroma neighbor adapter with server and client self-exclusion.
  public struct ChromaRowNeighborQuery: ReceiptNeighborQuerying {
    private let client: ChromaClient
    private let collectionName: String

    public init(client: ChromaClient, collectionName: String = "lines") {
      self.client = client
      self.collectionName = collectionName
    }

    public func queryRows(
      embeddings: [[Double]],
      excluding reference: ReceiptReference,
      nResults: Int
    ) async throws -> [[SectionNeighbor]] {
      guard !embeddings.isEmpty else { return [] }
      for vector in embeddings
      where vector.count != ReceiptUnderstandingConstants.embeddingDimensions {
        throw ReceiptEmbeddingError.invalidConfiguration(
          "Chroma query vector dimension \(vector.count) does not match "
            + "\(ReceiptUnderstandingConstants.embeddingDimensions)"
        )
      }
      let collection = try await client.getCollection(collectionName)
      if let dimension = collection.dimension,
        dimension != ReceiptUnderstandingConstants.embeddingDimensions
      {
        throw ReceiptEmbeddingError.invalidConfiguration(
          "Chroma collection dimension \(dimension) does not match "
            + "\(ReceiptUnderstandingConstants.embeddingDimensions)"
        )
      }
      let filter: ChromaFilter = [
        "$or": .array([
          .object([
            "image_id": .object(["$ne": .string(reference.imageID)])
          ]),
          .object([
            "receipt_id": .object([
              "$ne": .integer(Int64(reference.receiptID))
            ])
          ]),
        ])
      ]
      let result = try await client.query(
        collectionName: collectionName,
        queryEmbeddings: embeddings,
        nResults: nResults,
        where: filter,
        include: [.embeddings, .metadatas, .documents]
      )

      return embeddings.indices.map { queryIndex in
        let ids = item(result.ids, queryIndex) ?? []
        let metadata = item(result.metadatas, queryIndex) ?? []
        let documents = item(result.documents, queryIndex) ?? []
        let neighborEmbeddings = item(result.embeddings, queryIndex) ?? []
        return ids.indices.compactMap { neighborIndex -> SectionNeighbor? in
          guard
            neighborIndex < metadata.count,
            let metadata = metadata[neighborIndex],
            let imageID = string(metadata["image_id"]),
            let receiptID = integer(metadata["receipt_id"]),
            let lineID = integer(metadata["line_id"]),
            imageID != reference.imageID || receiptID != reference.receiptID,
            neighborIndex < neighborEmbeddings.count,
            let neighborEmbedding = neighborEmbeddings[neighborIndex],
            neighborEmbedding.count == embeddings[queryIndex].count
          else { return nil }
          return SectionNeighbor(
            reference: ReceiptReference(
              imageID: imageID,
              receiptID: receiptID
            ),
            lineID: lineID,
            rowLineIDs: parseRowLineIDs(metadata["row_line_ids"])
              ?? [lineID],
            document: neighborIndex < documents.count
              ? documents[neighborIndex] : nil,
            cosineSimilarity: cosine(
              embeddings[queryIndex],
              neighborEmbedding
            ),
            metadataSection: string(metadata["section_label"]),
            metadataMerchantName: string(metadata["merchant_name"])
          )
        }
      }
    }

    private func item<T>(_ values: [T]?, _ index: Int) -> T? {
      guard let values, index < values.count else { return nil }
      return values[index]
    }

    private func string(_ value: JSONValue?) -> String? {
      guard case .string(let result)? = value else { return nil }
      return result
    }

    private func integer(_ value: JSONValue?) -> Int? {
      switch value {
      case .integer(let result): return Int(result)
      case .number(let result): return Int(result)
      default: return nil
      }
    }

    private func parseRowLineIDs(_ value: JSONValue?) -> [Int]? {
      guard case .string(let raw)? = value,
        let data = raw.data(using: .utf8)
      else { return nil }
      return try? JSONDecoder().decode([Int].self, from: data)
    }

    private func cosine(_ lhs: [Double], _ rhs: [Double]) -> Double {
      var dot: Float = 0
      var lhsMagnitude: Float = 0
      var rhsMagnitude: Float = 0
      for index in lhs.indices {
        let left = Float(lhs[index])
        let right = Float(rhs[index])
        dot += left * right
        lhsMagnitude += left * left
        rhsMagnitude += right * right
      }
      let denominator = sqrt(lhsMagnitude) * sqrt(rhsMagnitude) + 1e-8
      return Double(dot / denominator)
    }
  }

#endif
