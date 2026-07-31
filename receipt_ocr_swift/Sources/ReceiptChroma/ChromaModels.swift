import Foundation

public enum ChromaInclude: String, Codable, CaseIterable, Sendable {
  case embeddings
  case documents
  case metadatas
  case distances
  case uris
}

public struct ChromaCollection: Codable, Equatable, Sendable {
  public let id: String
  public let name: String
  public let metadata: ChromaMetadata?
  public let dimension: Int?
  public let tenant: String?
  public let database: String?
  public let version: Int?
  public let logPosition: Int?
  public let configuration: JSONValue?
  public let schema: JSONValue?

  enum CodingKeys: String, CodingKey {
    case id
    case name
    case metadata
    case dimension
    case tenant
    case database
    case version
    case logPosition = "log_position"
    case configuration = "configuration_json"
    case schema
  }
}

public struct ChromaGetResult: Codable, Equatable, Sendable {
  public let ids: [String]
  public let included: [ChromaInclude]?
  public let embeddings: [[Double]?]?
  public let documents: [String?]?
  public let metadatas: [ChromaMetadata?]?
  public let uris: [String?]?
}

public struct ChromaQueryResult: Codable, Equatable, Sendable {
  public let ids: [[String]]
  public let included: [ChromaInclude]?
  public let embeddings: [[[Double]?]]?
  public let documents: [[String?]]?
  public let metadatas: [[ChromaMetadata?]]?
  public let distances: [[Double?]]?
  public let uris: [[String?]]?
}
