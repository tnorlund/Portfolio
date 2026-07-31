import CoreGraphics
import Foundation
import ReceiptChroma
import XCTest

@testable import ReceiptOCRCore

final class ReceiptChromaParityTests: XCTestCase {
  private actor Transport: ChromaHTTPTransport {
    private var replies: [(Int, Data)]
    private var captured: [URLRequest] = []

    init(_ replies: [(Int, Data)]) {
      self.replies = replies
    }

    func data(
      for request: URLRequest
    ) async throws -> (Data, HTTPURLResponse) {
      captured.append(request)
      let reply = replies.removeFirst()
      return (
        reply.1,
        HTTPURLResponse(
          url: request.url!,
          statusCode: reply.0,
          httpVersion: nil,
          headerFields: nil
        )!
      )
    }

    func requests() -> [URLRequest] { captured }
  }

  private func line(_ text: String, confidence: Float = 0.9) -> Line {
    let point = CodablePoint(x: 0, y: 0)
    return Line(
      text: text,
      boundingBox: NormalizedRect(
        x: 0.1,
        y: 0.2,
        width: 0.4,
        height: 0.1
      ),
      topLeft: point,
      topRight: point,
      bottomLeft: point,
      bottomRight: point,
      angleDegrees: 0,
      angleRadians: 0,
      confidence: confidence,
      words: []
    )
  }

  func testRowRecordMatchesPythonIDsDocumentsAndMetadata() {
    let row = VisualReceiptRow(
      rowID: 1,
      lineIDs: [1, 2],
      text: "ORGANIC APPLES 4.99",
      embeddingInput: "<EDGE>\nORGANIC APPLES 4.99\nTOTAL 4.99",
      bounds: VisualRowBounds(
        xMin: 0.1,
        yMin: 0.2,
        xMax: 0.9,
        yMax: 0.3
      )
    )

    let record = ReceiptChromaRowContract.record(
      reference: ReceiptReference(imageID: "image", receiptID: 7),
      row: row,
      sourceLines: [
        line("ORGANIC APPLES", confidence: 0.8),
        line("4.99", confidence: 1),
      ],
      merchantName: "smith's market",
      sectionLabel: "ITEMS",
      normalizedPhone10: "4155551212"
    )

    XCTAssertEqual(
      record.id,
      "IMAGE#image#RECEIPT#00007#LINE#00001"
    )
    XCTAssertEqual(record.document, "ORGANIC APPLES 4.99")
    XCTAssertEqual(
      record.embeddingInput,
      "<EDGE>\nORGANIC APPLES 4.99\nTOTAL 4.99"
    )
    XCTAssertEqual(record.metadata["row_line_ids"], .string("[1, 2]"))
    XCTAssertEqual(
      record.metadata["source"],
      .string("openai_embedding_batch")
    )
    // Python `str.title()` treats the apostrophe as a word boundary.
    XCTAssertEqual(
      record.metadata["merchant_name"],
      .string("Smith'S Market")
    )
    XCTAssertEqual(record.metadata["section_label"], .string("ITEMS"))
    XCTAssertEqual(
      record.metadata["normalized_phone_10"],
      .string("4155551212")
    )
  }

  func testQueryUsesExactSelfExclusionAndComputesCosineLocally() async throws {
    let dimension = ReceiptUnderstandingConstants.embeddingDimensions
    let collection = try JSONSerialization.data(
      withJSONObject: [
        "id": "collection-id",
        "name": "lines",
        "dimension": dimension,
      ]
    )
    let vectors = [
      Array(repeating: 1.0, count: dimension),
      Array(repeating: 2.0, count: dimension),
    ]
    let queryResponse = try JSONSerialization.data(
      withJSONObject: [
        "ids": [["self", "other"]],
        "included": ["embeddings", "metadatas", "documents"],
        "embeddings": [vectors],
        "documents": [["SELF", "KNOWN"]],
        "metadatas": [
          [
            [
              "image_id": "current",
              "receipt_id": 1,
              "line_id": 1,
              "row_line_ids": "[1]",
            ],
            [
              "image_id": "known",
              "receipt_id": 2,
              "line_id": 9,
              "row_line_ids": "[9, 10]",
              "section_label": "PENDING_IS_NOT_TRUSTED",
            ],
          ]
        ],
      ]
    )
    let transport = Transport([(200, collection), (200, queryResponse)])
    let configuration = try ChromaConfiguration(
      apiKey: "key",
      tenant: "tenant",
      database: "database",
      baseURL: URL(string: "http://127.0.0.1/api/v2")!
    )
    let client = ChromaClient(
      configuration: configuration,
      transport: transport,
      closeTransportOnClose: false
    )
    let query = ChromaRowNeighborQuery(client: client)

    let result = try await query.queryRows(
      embeddings: [Array(repeating: 1, count: dimension)],
      excluding: ReceiptReference(imageID: "current", receiptID: 1),
      nResults: 15
    )

    XCTAssertEqual(result.count, 1)
    XCTAssertEqual(result[0].count, 1)
    XCTAssertEqual(result[0][0].reference.imageID, "known")
    XCTAssertEqual(result[0][0].rowLineIDs, [9, 10])
    XCTAssertEqual(result[0][0].cosineSimilarity, 1, accuracy: 1e-6)

    let requests = await transport.requests()
    let body = try XCTUnwrap(requests.last?.httpBody)
    let json = try XCTUnwrap(
      JSONSerialization.jsonObject(with: body) as? [String: Any]
    )
    XCTAssertEqual(json["n_results"] as? Int, 15)
    let include = try XCTUnwrap(json["include"] as? [String])
    XCTAssertEqual(Set(include), Set(["embeddings", "metadatas", "documents"]))
    let filter = try XCTUnwrap(json["where"] as? [String: Any])
    let or = try XCTUnwrap(filter["$or"] as? [[String: Any]])
    XCTAssertEqual(or.count, 2)
    XCTAssertEqual(
      ((or[0]["image_id"] as? [String: Any])?["$ne"] as? String),
      "current"
    )
    XCTAssertEqual(
      ((or[1]["receipt_id"] as? [String: Any])?["$ne"] as? Int),
      1
    )
  }
}
