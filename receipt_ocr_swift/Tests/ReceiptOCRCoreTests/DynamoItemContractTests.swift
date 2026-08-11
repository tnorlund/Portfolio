import Foundation
import SotoDynamoDB
import Testing

@testable import ReceiptOCRCore

/// Contract tests for the worker's direct DynamoDB item serialization.
///
/// The fixture `swift_dynamo_items_contract.json` is pinned on both sides
/// of the boundary: this suite asserts `ReceiptStructureItems` reproduces
/// it exactly, and `test_swift_dynamo_items_contract.py` (Python) asserts
/// `item_to_receipt_section` / `item_to_receipt_line_item` parse it back
/// into valid `receipt_dynamo` entities. A drift on either side fails a
/// local test before it fails against the real table.
struct DynamoItemContractTests {
    static let imageId = "12345678-1234-4123-8123-123456789012"

    static var fixtureTimestamp: Date {
        var components = DateComponents()
        components.year = 2026
        components.month = 8
        components.day = 11
        components.timeZone = TimeZone(secondsFromGMT: 0)
        return Calendar(identifier: .gregorian).date(from: components)!
    }

    private func loadFixture() throws -> [String: [String: DynamoDB.AttributeValue]] {
        let url = try #require(
            Bundle.module.url(
                forResource: "swift_dynamo_items_contract",
                withExtension: "json", subdirectory: "Fixtures"
            )
        )
        return try JSONDecoder().decode(
            [String: [String: DynamoDB.AttributeValue]].self,
            from: Data(contentsOf: url)
        )
    }

    private func canonical(
        _ item: [String: DynamoDB.AttributeValue]
    ) throws -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return String(decoding: try encoder.encode(item), as: UTF8.self)
    }

    @Test func sectionItemMatchesTheContractFixture() throws {
        let fixture = try loadFixture()
        let section = ReceiptSectionPayload(
            sectionType: "ITEMS",
            lineIds: [3, 4],
            rowIds: [3],
            confidence: 0.95,
            modelSource: swiftWorkerModelSource,
            extractorVersion: swiftWorkerExtractorVersion
        )
        let item = ReceiptStructureItems.sectionItem(
            imageId: Self.imageId, receiptId: 1,
            section: section, createdAt: Self.fixtureTimestamp
        )
        #expect(
            try canonical(item) == canonical(try #require(fixture["section"]))
        )
    }

    @Test func lineItemItemMatchesTheContractFixture() throws {
        let fixture = try loadFixture()
        let payload = ReceiptLineItemPayload(
            itemIndex: 0,
            name: "ORGANIC",
            price: 3.99,
            quantity: 2.0,
            unitPrice: 1.99,
            isDiscount: false,
            nameQuality: "ok",
            lineIds: [3],
            reconciliationStatus: "match",
            modelSource: swiftWorkerModelSource,
            extractorVersion: swiftWorkerExtractorVersion,
            rawText: "ORGANIC 3.99"
        )
        let item = ReceiptStructureItems.lineItemItem(
            imageId: Self.imageId, receiptId: 1,
            item: payload, extractedAt: Self.fixtureTimestamp,
            baselineFiguresAgreeing: 2
        )
        #expect(
            try canonical(item)
                == canonical(try #require(fixture["line_item"]))
        )
    }

    @Test func optionalFieldsAreOmittedNotNulled() throws {
        let payload = ReceiptLineItemPayload(
            itemIndex: 1,
            name: "",
            price: 1.5,
            quantity: nil,
            unitPrice: nil,
            isDiscount: false,
            nameQuality: "low",
            lineIds: [7],
            reconciliationStatus: "no-baseline",
            modelSource: swiftWorkerModelSource,
            extractorVersion: swiftWorkerExtractorVersion,
            rawText: nil
        )
        let item = ReceiptStructureItems.lineItemItem(
            imageId: Self.imageId, receiptId: 1,
            item: payload, extractedAt: Self.fixtureTimestamp,
            baselineFiguresAgreeing: nil
        )
        #expect(item["quantity"] == nil)
        #expect(item["unit_price"] == nil)
        #expect(item["baseline_figures_agreeing"] == nil)
        // The sparse merchant GSI must never appear on worker rows.
        #expect(item["GSI1PK"] == nil)
        #expect(item["GSI1SK"] == nil)
        if case .s(let raw)? = item["raw_text"] {
            #expect(raw == "")
        } else {
            Issue.record("raw_text must serialize as an empty string")
        }
        if case .s(let price)? = item["price"] {
            #expect(price == "1.50")
        } else {
            Issue.record("price must serialize as a 2-decimal string")
        }
    }
}
