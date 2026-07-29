import Foundation

/// Errors surfaced by the Swift receipt-Chroma boundary.
public enum ReceiptChromaError: Error, Equatable, Sendable {
  case configuration(String)
  case closed
  case readOnly
  case collectionNotFound(String)
  case invalidRequest(String)
  case http(status: Int, type: String?, message: String, traceID: String?)
  case invalidResponse(String)
  case transport(String)
  case partialBatch(completed: Int, total: Int, message: String)
}

extension ReceiptChromaError: LocalizedError {
  public var errorDescription: String? {
    switch self {
    case .configuration(let message):
      return "Invalid Chroma configuration: \(message)"
    case .closed:
      return "Cannot use a closed Chroma client"
    case .readOnly:
      return "This Chroma client is read-only"
    case .collectionNotFound(let name):
      return "Chroma collection '\(name)' was not found"
    case .invalidRequest(let message):
      return "Invalid Chroma request: \(message)"
    case .http(let status, let type, let message, let traceID):
      let typeSuffix = type.map { " (\($0))" } ?? ""
      let traceSuffix = traceID.map { " [trace \($0)]" } ?? ""
      return "Chroma HTTP \(status)\(typeSuffix): \(message)\(traceSuffix)"
    case .invalidResponse(let message):
      return "Invalid Chroma response: \(message)"
    case .transport(let message):
      return "Chroma transport failed: \(message)"
    case .partialBatch(let completed, let total, let message):
      return "Chroma upsert stopped after \(completed) of \(total) records: \(message)"
    }
  }
}
