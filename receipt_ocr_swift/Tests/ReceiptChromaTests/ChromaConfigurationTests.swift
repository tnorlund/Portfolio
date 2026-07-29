import Foundation
import XCTest

@testable import ReceiptChroma

final class ChromaConfigurationTests: XCTestCase {
  func testNormalizesRequiredValues() throws {
    let configuration = try ChromaConfiguration(
      apiKey: "  secret \n",
      tenant: " tenant-id ",
      database: " receipt_dev "
    )

    XCTAssertEqual(configuration.apiKey, "secret")
    XCTAssertEqual(configuration.tenant, "tenant-id")
    XCTAssertEqual(configuration.database, "receipt_dev")
    XCTAssertEqual(
      configuration.baseURL.absoluteString,
      "https://api.trychroma.com/api/v2"
    )
  }

  func testRejectsBlankAndInvalidConfiguration() {
    XCTAssertThrowsError(
      try ChromaConfiguration(apiKey: " ", tenant: "tenant", database: "db")
    )
    XCTAssertThrowsError(
      try ChromaConfiguration(apiKey: "key", tenant: "", database: "db")
    )
    XCTAssertThrowsError(
      try ChromaConfiguration(
        apiKey: "key",
        tenant: "tenant",
        database: "db",
        requestTimeout: 0
      )
    )
    XCTAssertThrowsError(
      try ChromaConfiguration(
        apiKey: "key",
        tenant: "tenant",
        database: "db",
        retryPolicy: ChromaRetryPolicy(
          maxRetries: 1,
          baseDelay: 2,
          maxDelay: 1
        )
      )
    )
    XCTAssertThrowsError(
      try ChromaConfiguration(
        apiKey: "key",
        tenant: "tenant",
        database: "db",
        baseURL: URL(string: "http://api.example.com/api/v2")!
      )
    )
  }

  func testAllowsHTTPOnlyForLoopbackHosts() throws {
    let localhost = try ChromaConfiguration(
      apiKey: "key",
      tenant: "tenant",
      database: "db",
      baseURL: URL(string: "http://localhost:8000/api/v2")!
    )
    let loopback = try ChromaConfiguration(
      apiKey: "key",
      tenant: "tenant",
      database: "db",
      baseURL: URL(string: "http://127.0.0.1:8000/api/v2")!
    )

    XCTAssertEqual(localhost.baseURL.host, "localhost")
    XCTAssertEqual(loopback.baseURL.host, "127.0.0.1")
  }
}
