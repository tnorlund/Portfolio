import Foundation

@testable import ReceiptChroma

#if canImport(FoundationNetworking)
  import FoundationNetworking
#endif

struct RecordedChromaRequest: Sendable {
  let url: URL
  let method: String
  let headers: [String: String]
  let body: Data?
}

actor MockChromaTransport: ChromaHTTPTransport {
  struct Stub: Sendable {
    let status: Int
    let data: Data
    let headers: [String: String]

    init(
      status: Int = 200,
      json: String,
      headers: [String: String] = [:]
    ) {
      self.status = status
      self.data = Data(json.utf8)
      self.headers = headers
    }
  }

  private var stubs: [Stub]
  private var recordedRequests: [RecordedChromaRequest] = []
  private var closeCalls = 0

  init(_ stubs: [Stub]) {
    self.stubs = stubs
  }

  func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
    recordedRequests.append(
      RecordedChromaRequest(
        url: request.url!,
        method: request.httpMethod ?? "GET",
        headers: request.allHTTPHeaderFields ?? [:],
        body: request.httpBody
      )
    )

    guard !stubs.isEmpty else {
      throw ReceiptChromaError.transport("No mock response queued")
    }
    let stub = stubs.removeFirst()
    let response = HTTPURLResponse(
      url: request.url!,
      statusCode: stub.status,
      httpVersion: "HTTP/1.1",
      headerFields: stub.headers
    )!
    return (stub.data, response)
  }

  func requests() -> [RecordedChromaRequest] {
    recordedRequests
  }

  func close() async {
    closeCalls += 1
  }

  func closeCount() -> Int {
    closeCalls
  }
}

actor SleepRecorder {
  private var recorded: [UInt64] = []

  func sleep(_ nanoseconds: UInt64) {
    recorded.append(nanoseconds)
  }

  func values() -> [UInt64] {
    recorded
  }
}
