import XCTest

@testable import ReceiptChroma

final class JSONValueTests: XCTestCase {
  func testRoundTripsEveryJSONShapeAndPreservesNull() throws {
    let value: JSONValue = [
      "string": "receipt",
      "integer": 7,
      "number": 1.25,
      "bool": true,
      "array": ["a", 2, false],
      "object": ["nested": "value"],
      "tombstone": nil,
    ]

    let data = try JSONEncoder().encode(value)
    let decoded = try JSONDecoder().decode(JSONValue.self, from: data)

    XCTAssertEqual(decoded, value)
    XCTAssertTrue(String(data: data, encoding: .utf8)!.contains("\"tombstone\":null"))
  }

  func testDecodesIntegerBeforeFloatingPointNumber() throws {
    XCTAssertEqual(
      try JSONDecoder().decode(JSONValue.self, from: Data("42".utf8)),
      .integer(42)
    )
    XCTAssertEqual(
      try JSONDecoder().decode(JSONValue.self, from: Data("42.5".utf8)),
      .number(42.5)
    )
  }
}
