import Foundation
import XCTest

@testable import ReceiptChroma

final class ChromaClientTests: XCTestCase {
  func testGetsCollectionWithAuthAndCachesByName() async throws {
    let transport = MockChromaTransport([
      .init(json: collectionJSON(name: "receipt rows", id: "collection-1"))
    ])
    let client = try makeClient(transport: transport)

    let first = try await client.getCollection("receipt rows")
    let second = try await client.getCollection("receipt rows")

    XCTAssertEqual(first, second)
    let requests = await transport.requests()
    XCTAssertEqual(requests.count, 1)
    XCTAssertEqual(requests[0].method, "GET")
    XCTAssertEqual(
      requests[0].url.absoluteString,
      "https://example.test/api/v2/tenants/tenant-id/databases/receipt_dev/collections/receipt%20rows"
    )
    XCTAssertEqual(requests[0].headers["X-Chroma-Token"], "test-key")
    XCTAssertEqual(requests[0].headers["Accept"], "application/json")
  }

  func testMissingWritableCollectionUsesAtomicGetOrCreate() async throws {
    let transport = MockChromaTransport([
      .init(
        status: 404,
        json: #"{"error":"NotFoundError","message":"missing"}"#
      ),
      .init(json: collectionJSON(name: "lines", id: "lines-id")),
    ])
    let client = try makeClient(mode: .write, transport: transport)

    let collection = try await client.getCollection(
      "lines",
      createIfMissing: true,
      metadata: ["description": "Receipt lines"]
    )

    XCTAssertEqual(collection.id, "lines-id")
    let requests = await transport.requests()
    XCTAssertEqual(requests.map(\.method), ["GET", "POST"])
    let body = try XCTUnwrap(requests[1].body)
    let json = try XCTUnwrap(
      JSONSerialization.jsonObject(with: body) as? [String: Any]
    )
    XCTAssertEqual(json["name"] as? String, "lines")
    XCTAssertEqual(json["get_or_create"] as? Bool, true)
    XCTAssertEqual(
      (json["metadata"] as? [String: Any])?["description"] as? String,
      "Receipt lines"
    )
  }

  func testReadModeNeverCreatesMissingCollection() async throws {
    let transport = MockChromaTransport([
      .init(status: 404, json: #"{"message":"missing"}"#)
    ])
    let client = try makeClient(mode: .read, transport: transport)

    do {
      _ = try await client.getCollection("words", createIfMissing: true)
      XCTFail("Expected collectionNotFound")
    } catch {
      XCTAssertEqual(
        error as? ReceiptChromaError,
        .collectionNotFound("words")
      )
    }

    let requestCount = await transport.requests().count
    XCTAssertEqual(requestCount, 1)
  }

  func testUpsertBatchesAt250AndPreservesNullMetadata() async throws {
    let transport = MockChromaTransport([
      .init(json: collectionJSON(name: "words", id: "words-id")),
      .init(json: "{}"),
      .init(json: "{}"),
    ])
    let client = try makeClient(mode: .write, transport: transport)
    let ids = (0..<251).map { "word-\($0)" }
    let embeddings = (0..<251).map { [Double($0), 0.5] }
    var metadatas = [ChromaMetadata?](repeating: nil, count: 251)
    metadatas[0] = ["obsolete_label": .null, "validated": true]

    let count = try await client.upsert(
      collectionName: "words",
      ids: ids,
      embeddings: embeddings,
      metadatas: metadatas
    )

    XCTAssertEqual(count, 251)
    let requests = await transport.requests()
    XCTAssertEqual(requests.count, 3)
    let firstBody = try jsonBody(requests[1])
    let secondBody = try jsonBody(requests[2])
    XCTAssertEqual((firstBody["ids"] as? [String])?.count, 250)
    XCTAssertEqual((secondBody["ids"] as? [String])?.count, 1)
    let firstMetadatas = try XCTUnwrap(firstBody["metadatas"] as? [Any])
    let firstMetadata = try XCTUnwrap(firstMetadatas[0] as? [String: Any])
    XCTAssertTrue(firstMetadata["obsolete_label"] is NSNull)
    XCTAssertEqual(firstMetadata["validated"] as? Bool, true)
  }

  func testUpsertReportsPartialBatchProgress() async throws {
    let transport = MockChromaTransport([
      .init(json: collectionJSON(name: "words", id: "words-id")),
      .init(json: "{}"),
      .init(status: 500, json: #"{"message":"server failed"}"#),
    ])
    let client = try makeClient(mode: .write, transport: transport)
    let ids = (0..<251).map { "word-\($0)" }
    let embeddings = ids.map { _ in [0.1] }

    do {
      _ = try await client.upsert(
        collectionName: "words",
        ids: ids,
        embeddings: embeddings
      )
      XCTFail("Expected partial batch error")
    } catch ReceiptChromaError.partialBatch(
      let completed,
      let total,
      let message
    ) {
      XCTAssertEqual(completed, 250)
      XCTAssertEqual(total, 251)
      XCTAssertTrue(message.contains("500"))
    } catch {
      XCTFail("Unexpected error: \(error)")
    }
  }

  func testQueryClampsCloudLimitAndDecodesNestedResults() async throws {
    let transport = MockChromaTransport([
      .init(json: collectionJSON(name: "lines", id: "lines-id")),
      .init(
        json: """
          {
            "ids": [["row-1"]],
            "included": ["metadatas", "documents", "distances"],
            "embeddings": null,
            "documents": [["MILK ORGANIC HALF GALLON"]],
            "metadatas": [[{"receipt_id": 1}]],
            "distances": [[0.125]],
            "uris": null
          }
          """
      ),
    ])
    let client = try makeClient(transport: transport)

    let result = try await client.query(
      collectionName: "lines",
      queryEmbeddings: [[0.1, 0.2]],
      nResults: 999,
      where: ["merchant": ["$eq": "Trader Joe's"]]
    )

    XCTAssertEqual(result.ids, [["row-1"]])
    XCTAssertEqual(result.included, [.metadatas, .documents, .distances])
    XCTAssertEqual(result.documents, [["MILK ORGANIC HALF GALLON"]])
    XCTAssertEqual(result.metadatas?[0][0]?["receipt_id"], .integer(1))
    XCTAssertEqual(result.distances?[0][0], 0.125)

    let requests = await transport.requests()
    let body = try jsonBody(requests[1])
    XCTAssertEqual(body["n_results"] as? Int, 300)
    XCTAssertNotNil(body["where"])
    XCTAssertEqual(
      body["include"] as? [String],
      ["metadatas", "documents", "distances"]
    )
  }

  func testGetSendsRecursiveFiltersAndDecodesNullFields() async throws {
    let transport = MockChromaTransport([
      .init(json: collectionJSON(name: "words", id: "words-id")),
      .init(
        json: """
          {
            "ids": ["word-1"],
            "included": ["metadatas", "documents", "embeddings"],
            "embeddings": [[0.1, 0.2]],
            "documents": [null],
            "metadatas": [{"label": null}],
            "uris": null
          }
          """
      ),
    ])
    let client = try makeClient(transport: transport)

    let result = try await client.get(
      collectionName: "words",
      where: [
        "$and": [
          ["receipt_id": ["$eq": 1]],
          ["label": ["$ne": "INVALID"]],
        ]
      ],
      limit: 20,
      offset: 5
    )

    XCTAssertEqual(result.ids, ["word-1"])
    XCTAssertEqual(result.included, [.metadatas, .documents, .embeddings])
    let documents = try XCTUnwrap(result.documents)
    XCTAssertNil(documents[0])
    XCTAssertEqual(result.metadatas?[0]?["label"], .null)
    let requests = await transport.requests()
    let body = try jsonBody(requests[1])
    XCTAssertEqual(body["limit"] as? Int, 20)
    XCTAssertEqual(body["offset"] as? Int, 5)
    XCTAssertNotNil(body["where"])
  }

  func testReadOnlyAndIdsOrFilterGuardsRunBeforeTransport() async throws {
    let readTransport = MockChromaTransport([])
    let readClient = try makeClient(mode: .read, transport: readTransport)

    do {
      _ = try await readClient.upsert(
        collectionName: "words",
        ids: ["id"],
        embeddings: [[0.1]]
      )
      XCTFail("Expected readOnly")
    } catch {
      XCTAssertEqual(error as? ReceiptChromaError, .readOnly)
    }

    let writeTransport = MockChromaTransport([])
    let writeClient = try makeClient(mode: .write, transport: writeTransport)
    do {
      try await writeClient.delete(collectionName: "words")
      XCTFail("Expected invalidRequest")
    } catch ReceiptChromaError.invalidRequest(let message) {
      XCTAssertTrue(message.contains("requires"))
    } catch {
      XCTFail("Unexpected error: \(error)")
    }

    let readRequestCount = await readTransport.requests().count
    let writeRequestCount = await writeTransport.requests().count
    XCTAssertEqual(readRequestCount, 0)
    XCTAssertEqual(writeRequestCount, 0)
  }

  func testRetriesOnlyRateLimitsWithExponentialDelay() async throws {
    let transport = MockChromaTransport([
      .init(status: 429, json: #"{"message":"Too many requests"}"#),
      .init(json: collectionJSON(name: "lines", id: "lines-id")),
    ])
    let recorder = SleepRecorder()
    let client = try makeClient(
      transport: transport,
      retryPolicy: ChromaRetryPolicy(
        maxRetries: 2,
        baseDelay: 0.5,
        maxDelay: 8
      ),
      sleeper: { nanoseconds in
        await recorder.sleep(nanoseconds)
      }
    )

    _ = try await client.getCollection("lines")

    let requestCount = await transport.requests().count
    let recordedSleeps = await recorder.values()
    XCTAssertEqual(requestCount, 2)
    XCTAssertEqual(recordedSleeps, [500_000_000])
  }

  func testHTTPErrorPreservesTypeMessageAndTraceIDWithoutRetry() async throws {
    let transport = MockChromaTransport([
      .init(
        status: 401,
        json: #"{"error":"AuthorizationError","message":"bad token"}"#,
        headers: ["chroma-trace-id": "trace-123"]
      )
    ])
    let client = try makeClient(transport: transport)

    do {
      _ = try await client.getCollection("lines")
      XCTFail("Expected HTTP error")
    } catch {
      XCTAssertEqual(
        error as? ReceiptChromaError,
        .http(
          status: 401,
          type: "AuthorizationError",
          message: "bad token",
          traceID: "trace-123"
        )
      )
    }

    let requestCount = await transport.requests().count
    XCTAssertEqual(requestCount, 1)
  }

  func testCloseIsIdempotentAndRejectsLaterUse() async throws {
    let transport = MockChromaTransport([])
    let client = try makeClient(transport: transport)

    await client.close()
    await client.close()
    let closeCount = await transport.closeCount()
    XCTAssertEqual(closeCount, 1)

    do {
      _ = try await client.listCollections()
      XCTFail("Expected closed")
    } catch {
      XCTAssertEqual(error as? ReceiptChromaError, .closed)
    }
  }

  func testRejectsMisalignedAndOversizedPayloadsBeforeTransport() async throws {
    let transport = MockChromaTransport([])
    let client = try makeClient(mode: .write, transport: transport)

    do {
      _ = try await client.upsert(
        collectionName: "words",
        ids: ["one", "two"],
        embeddings: [[0.1]]
      )
      XCTFail("Expected invalidRequest")
    } catch ReceiptChromaError.invalidRequest(let message) {
      XCTAssertTrue(message.contains("expected 2"))
    } catch {
      XCTFail("Unexpected error: \(error)")
    }

    do {
      _ = try await client.upsert(
        collectionName: "words",
        ids: [String(repeating: "x", count: 129)],
        embeddings: [[0.1]]
      )
      XCTFail("Expected invalidRequest")
    } catch ReceiptChromaError.invalidRequest(let message) {
      XCTAssertTrue(message.contains("128"))
    } catch {
      XCTFail("Unexpected error: \(error)")
    }

    let requestCount = await transport.requests().count
    XCTAssertEqual(requestCount, 0)
  }

  func testRejectsDuplicateIDsAcrossBatchBoundary() async throws {
    let transport = MockChromaTransport([])
    let client = try makeClient(mode: .write, transport: transport)
    var ids = (0..<251).map { "word-\($0)" }
    ids[250] = ids[0]

    do {
      _ = try await client.upsert(
        collectionName: "words",
        ids: ids,
        embeddings: ids.map { _ in [0.1] }
      )
      XCTFail("Expected invalidRequest")
    } catch ReceiptChromaError.invalidRequest(let message) {
      XCTAssertTrue(message.contains("unique"))
    } catch {
      XCTFail("Unexpected error: \(error)")
    }

    let requestCount = await transport.requests().count
    XCTAssertEqual(requestCount, 0)
  }

  func testCollectionExistsBypassesStaleCache() async throws {
    let transport = MockChromaTransport([
      .init(json: collectionJSON(name: "lines", id: "lines-id")),
      .init(status: 404, json: #"{"message":"missing"}"#),
    ])
    let client = try makeClient(transport: transport)

    _ = try await client.getCollection("lines")
    let exists = try await client.collectionExists("lines")

    XCTAssertEqual(exists, false)
    let requestCount = await transport.requests().count
    XCTAssertEqual(requestCount, 2)
  }

  func testRejectsUnsupportedGetIncludeAndOversizedReadBeforeTransport()
    async throws
  {
    let transport = MockChromaTransport([])
    let client = try makeClient(transport: transport)

    do {
      _ = try await client.get(
        collectionName: "words",
        ids: ["word-1"],
        include: [.distances]
      )
      XCTFail("Expected invalidRequest")
    } catch ReceiptChromaError.invalidRequest(let message) {
      XCTAssertTrue(message.contains("distances"))
    } catch {
      XCTFail("Unexpected error: \(error)")
    }

    do {
      _ = try await client.get(
        collectionName: "words",
        ids: ["word-1"],
        limit: 301
      )
      XCTFail("Expected invalidRequest")
    } catch ReceiptChromaError.invalidRequest(let message) {
      XCTAssertTrue(message.contains("300"))
    } catch {
      XCTFail("Unexpected error: \(error)")
    }

    let requestCount = await transport.requests().count
    XCTAssertEqual(requestCount, 0)
  }

  func testRejectsInvalidMetadataShapesAndHonorsRawByteLimit() async throws {
    let transport = MockChromaTransport([])
    let client = try makeClient(mode: .write, transport: transport)

    let invalidMetadatas: [ChromaMetadata] = [
      [:],
      ["mixed": [.string("a"), .integer(2)]],
      ["empty_array": .array([])],
      ["$reserved": "value"],
      ["not_finite": .number(.infinity)],
      ["too_long": .string(String(repeating: "x", count: 8_183))],
    ]
    for metadata in invalidMetadatas {
      do {
        _ = try await client.upsert(
          collectionName: "words",
          ids: ["word-1"],
          embeddings: [[0.1]],
          metadatas: [metadata]
        )
        XCTFail("Expected invalidRequest")
      } catch ReceiptChromaError.invalidRequest {
      } catch {
        XCTFail("Unexpected error: \(error)")
      }
    }

    let boundaryTransport = MockChromaTransport([
      .init(json: collectionJSON(name: "words", id: "words-id")),
      .init(json: "{}"),
    ])
    let boundaryClient = try makeClient(
      mode: .write,
      transport: boundaryTransport
    )
    let completed = try await boundaryClient.upsert(
      collectionName: "words",
      ids: ["word-1"],
      embeddings: [[0.1]],
      metadatas: [
        ["at_limit": .string(String(repeating: "x", count: 8_182))]
      ]
    )

    XCTAssertEqual(completed, 1)
    let invalidRequestCount = await transport.requests().count
    XCTAssertEqual(invalidRequestCount, 0)
  }

  func testCountsFullTextLeafPredicatesBeforeTransport() async throws {
    let transport = MockChromaTransport([])
    let client = try makeClient(transport: transport)
    let predicates = (0..<9).map { index in
      JSONValue.object(["$contains": .string("term-\(index)")])
    }

    do {
      _ = try await client.get(
        collectionName: "words",
        whereDocument: ["$and": .array(predicates)]
      )
      XCTFail("Expected invalidRequest")
    } catch ReceiptChromaError.invalidRequest(let message) {
      XCTAssertTrue(message.contains("8 predicates"))
    } catch {
      XCTFail("Unexpected error: \(error)")
    }

    let requestCount = await transport.requests().count
    XCTAssertEqual(requestCount, 0)
  }

  private func makeClient(
    mode: ChromaMode = .read,
    transport: MockChromaTransport,
    retryPolicy: ChromaRetryPolicy = ChromaRetryPolicy(),
    sleeper: @escaping ChromaClient.Sleeper = { _ in }
  ) throws -> ChromaClient {
    let configuration = try ChromaConfiguration(
      apiKey: "test-key",
      tenant: "tenant-id",
      database: "receipt_dev",
      mode: mode,
      baseURL: URL(string: "https://example.test/api/v2")!,
      retryPolicy: retryPolicy
    )
    return ChromaClient(
      configuration: configuration,
      transport: transport,
      sleeper: sleeper,
      jitter: { _ in 0 }
    )
  }

  private func collectionJSON(name: String, id: String) -> String {
    """
    {
      "id": "\(id)",
      "name": "\(name)",
      "metadata": null,
      "dimension": 1536,
      "tenant": "tenant-id",
      "database": "receipt_dev",
      "version": 1,
      "log_position": 10,
      "configuration_json": {},
      "schema": null
    }
    """
  }

  private func jsonBody(
    _ request: RecordedChromaRequest
  ) throws -> [String: Any] {
    let body = try XCTUnwrap(request.body)
    return try XCTUnwrap(
      JSONSerialization.jsonObject(with: body) as? [String: Any]
    )
  }
}
