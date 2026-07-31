import Foundation
import XCTest

@testable import ReceiptOCRCore

final class OpenAIRowEmbeddingProviderTests: XCTestCase {
  private actor Transport: OpenAIEmbeddingHTTPTransport {
    struct Reply: Sendable {
      let status: Int
      let data: Data
    }

    private var replies: [Reply]
    private(set) var requests: [URLRequest] = []

    init(_ replies: [Reply]) {
      self.replies = replies
    }

    func send(
      _ request: URLRequest
    ) async throws -> (Data, HTTPURLResponse) {
      requests.append(request)
      let reply = replies.removeFirst()
      let response = HTTPURLResponse(
        url: request.url!,
        statusCode: reply.status,
        httpVersion: nil,
        headerFields: nil
      )!
      return (reply.data, response)
    }

    func capturedRequests() -> [URLRequest] { requests }
  }

  private actor SleepProbe {
    private(set) var delays: [UInt64] = []
    func record(_ delay: UInt64) { delays.append(delay) }
    func count() -> Int { delays.count }
  }

  private func response(
    count: Int,
    dimension: Int = ReceiptUnderstandingConstants.embeddingDimensions,
    reversed: Bool = false
  ) throws -> Data {
    let indexes =
      reversed
      ? Array((0..<count).reversed()) : Array(0..<count)
    return try JSONSerialization.data(
      withJSONObject: [
        "data": indexes.map {
          [
            "index": $0,
            "embedding": Array(repeating: Double($0 + 1), count: dimension),
          ]
        }
      ]
    )
  }

  func testRequestMatchesPythonAndResponseIsSortedByIndex() async throws {
    let transport = Transport([
      .init(status: 200, data: try response(count: 2, reversed: true))
    ])
    let provider = try OpenAIRowEmbeddingProvider(
      apiKey: "secret",
      endpoint: URL(string: "http://127.0.0.1/embeddings")!,
      transport: transport
    )

    let vectors = try await provider.embedRows(["A\nB\nC", "D\nE\nF"])

    XCTAssertEqual(vectors.count, 2)
    XCTAssertEqual(vectors[0][0], 1)
    XCTAssertEqual(vectors[1][0], 2)
    let requests = await transport.capturedRequests()
    let request = try XCTUnwrap(requests.first)
    let body = try XCTUnwrap(
      JSONSerialization.jsonObject(with: request.httpBody!) as? [String: Any]
    )
    XCTAssertEqual(
      body["model"] as? String,
      ReceiptUnderstandingConstants.embeddingModel
    )
    XCTAssertEqual(body["input"] as? [String], ["A\nB\nC", "D\nE\nF"])
    XCTAssertNil(body["dimensions"])
  }

  func testRetriesRateLimitsAndServerFailures() async throws {
    let transport = Transport([
      .init(status: 429, data: Data("rate limited".utf8)),
      .init(status: 500, data: Data("server".utf8)),
      .init(status: 200, data: try response(count: 1)),
    ])
    let sleep = SleepProbe()
    let provider = try OpenAIRowEmbeddingProvider(
      apiKey: "secret",
      endpoint: URL(string: "http://localhost/embeddings")!,
      transport: transport,
      maximumRetries: 2,
      sleeper: { await sleep.record($0) }
    )

    let vectors = try await provider.embedRows(["row"])

    XCTAssertEqual(vectors.count, 1)
    let sleepCount = await sleep.count()
    let requestCount = await transport.capturedRequests().count
    XCTAssertEqual(sleepCount, 2)
    XCTAssertEqual(requestCount, 3)
  }

  func testRejectsMissingOrWrongDimensionEmbeddings() async throws {
    let transport = Transport([
      .init(status: 200, data: try response(count: 1, dimension: 8))
    ])
    let provider = try OpenAIRowEmbeddingProvider(
      apiKey: "secret",
      endpoint: URL(string: "http://127.0.0.1/embeddings")!,
      transport: transport
    )

    do {
      _ = try await provider.embedRows(["row"])
      XCTFail("expected dimension validation to fail")
    } catch {
      XCTAssertTrue(error.localizedDescription.contains("dimensions"))
    }
  }
}
