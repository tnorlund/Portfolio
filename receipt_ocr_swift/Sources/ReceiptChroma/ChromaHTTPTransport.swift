import Foundation

#if canImport(FoundationNetworking)
  import FoundationNetworking
#endif

public protocol ChromaHTTPTransport: Sendable {
  func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse)
  func close() async
}

extension ChromaHTTPTransport {
  public func close() async {}
}

public struct URLSessionChromaHTTPTransport: ChromaHTTPTransport {
  private let session: URLSession

  public init(configuration: URLSessionConfiguration = .default) {
    self.session = URLSession(configuration: configuration)
  }

  public func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
    let (data, response) = try await session.data(for: request)
    guard let httpResponse = response as? HTTPURLResponse else {
      throw ReceiptChromaError.invalidResponse(
        "transport returned a non-HTTP response"
      )
    }
    return (data, httpResponse)
  }

  public func close() async {
    session.invalidateAndCancel()
  }
}
