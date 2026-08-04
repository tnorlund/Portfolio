import Foundation
import Testing

@testable import ReceiptOCRCore

@Suite struct ReceiptStructurePipelineTests {
    struct OCRFixture: Decodable {
        let receipts: [OCRReceipt]
    }

    struct OCRReceipt: Decodable {
        let imageId: String
        let receiptId: Int
        let merchant: String?
        let words: [ZoneWord]

        enum CodingKeys: String, CodingKey {
            case imageId = "image_id"
            case receiptId = "receipt_id"
            case merchant
            case words
        }
    }

    struct ExpectedReceipt: Decodable {
        let imageId: String
        let receiptId: Int
        let sections: [ExpectedSection]
        let lineItems: [ExpectedItem]
        let printedSubtotal: Double?
        let reconciliationStatus: String
        let shouldReocrItemsZone: Bool

        enum CodingKeys: String, CodingKey {
            case imageId = "image_id"
            case receiptId = "receipt_id"
            case sections
            case lineItems = "line_items"
            case printedSubtotal = "printed_subtotal"
            case reconciliationStatus = "reconciliation_status"
            case shouldReocrItemsZone = "should_reocr_items_zone"
        }
    }

    struct ExpectedSection: Decodable {
        let sectionType: String
        let lineIds: [Int]

        enum CodingKeys: String, CodingKey {
            case sectionType = "section_type"
            case lineIds = "line_ids"
        }
    }

    struct ExpectedItem: Decodable {
        let itemIndex: Int
        let name: String
        let price: Double
        let quantity: Double?
        let unitPrice: Double?
        let isDiscount: Bool
        let nameQuality: String
        let lineIds: [Int]
        let reconciliationStatus: String

        enum CodingKeys: String, CodingKey {
            case itemIndex = "item_index"
            case name
            case price
            case quantity
            case unitPrice = "unit_price"
            case isDiscount = "is_discount"
            case nameQuality = "name_quality"
            case lineIds = "line_ids"
            case reconciliationStatus = "reconciliation_status"
        }
    }

    struct ContractEnvelope: Decodable {
        let receipts: [ContractReceipt]
    }

    struct ContractReceipt: Decodable {
        let clusterId: Int
        let lines: [Line]
        let layoutlmPredictions: [LinePrediction]?
    }

    private func load<T: Decodable>(_ name: String, as type: T.Type) throws
        -> T
    {
        let url = try #require(
            Bundle.module.url(
                forResource: name, withExtension: "json",
                subdirectory: "Fixtures"
            )
        )
        return try JSONDecoder().decode(T.self, from: Data(contentsOf: url))
    }

    private func loadContract() throws -> ContractEnvelope {
        let url = try #require(
            Bundle.module.url(
                forResource: "swift_single_pass_contract",
                withExtension: "json", subdirectory: "Fixtures"
            )
        )
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(
            ContractEnvelope.self, from: Data(contentsOf: url)
        )
    }

    private func reconstruct(_ receipt: OCRReceipt) -> [SectionLine] {
        let words = receipt.words.map { word in
            SectionWord(
                lineId: word.lineId, wordId: word.wordId, text: word.text,
                boundingBox: SectionRect(
                    x: word.x, y: word.yMid - word.h / 2,
                    width: 0, height: word.h
                )
            )
        }
        let grouped = Dictionary(grouping: words, by: \.lineId)
        return grouped.keys.sorted().map { lineId in
            let members = grouped[lineId]!
            let ordered = members.stableSorted {
                if $0.boundingBox.x != $1.boundingBox.x {
                    return $0.boundingBox.x < $1.boundingBox.x
                }
                return $0.wordId < $1.wordId
            }
            let xMin = members.map { $0.boundingBox.x }.min()!
            let xMax = members.map { $0.boundingBox.x }.max()!
            let yMin = members.map { $0.boundingBox.y }.min()!
            let yMax = members.map {
                $0.boundingBox.y + $0.boundingBox.height
            }.max()!
            return SectionLine(
                imageId: receipt.imageId, receiptId: receipt.receiptId,
                lineId: lineId,
                text: ordered.map(\.text).joined(separator: " "),
                boundingBox: SectionRect(
                    x: xMin, y: yMin, width: xMax - xMin,
                    height: yMax - yMin
                ),
                words: ordered
            )
        }
    }

    private func equalMoney(_ left: Double?, _ right: Double?) -> Bool {
        switch (left, right) {
        case (nil, nil):
            return true
        case let (left?, right?):
            return abs(left - right) <= 0.005
        default:
            return false
        }
    }

    @Test func goldenEndToEndParityAcrossTheWholeGoldenSet() throws {
        let fixture = try load("line_items_golden_ocr", as: OCRFixture.self)
        let expected = try load(
            "receipt_structure_parity_expected",
            as: [ExpectedReceipt].self
        )
        // Count derived, not pinned: the golden set grows (33 -> 35).
        #expect(fixture.receipts.count == expected.count)
        #expect(expected.count >= 35)

        var passed = 0
        var failures: [String] = []
        for (receipt, expectation) in zip(fixture.receipts, expected) {
            let result = try buildOnDeviceReceiptStructure(
                lines: reconstruct(receipt), merchantName: receipt.merchant
            )
            var differences: [String] = []
            if result.sections.map(\.sectionType)
                != expectation.sections.map(\.sectionType)
            {
                differences.append("section types")
            }
            if result.sections.map(\.lineIds)
                != expectation.sections.map(\.lineIds)
            {
                differences.append("section line_ids")
            }
            if result.lineItems.count != expectation.lineItems.count {
                differences.append(
                    "item count \(result.lineItems.count) != "
                        + "\(expectation.lineItems.count)"
                )
            } else {
                for (got, wanted) in zip(
                    result.lineItems, expectation.lineItems
                ) {
                    if got.itemIndex != wanted.itemIndex
                        || got.name != wanted.name
                        || !equalMoney(got.price, wanted.price)
                        || !equalMoney(got.quantity, wanted.quantity)
                        || !equalMoney(got.unitPrice, wanted.unitPrice)
                        || got.isDiscount != wanted.isDiscount
                        || got.nameQuality != wanted.nameQuality
                        || got.lineIds != wanted.lineIds
                        || got.reconciliationStatus
                            != wanted.reconciliationStatus
                        || got.modelSource != swiftWorkerModelSource
                        || got.extractorVersion
                            != swiftWorkerExtractorVersion
                    {
                        differences.append("item[\(got.itemIndex)]")
                    }
                }
            }
            if !equalMoney(
                result.printedSubtotal, expectation.printedSubtotal
            ) {
                differences.append("printed subtotal")
            }
            if result.reconciliationStatus
                != expectation.reconciliationStatus
            {
                differences.append("reconciliation")
            }
            if result.shouldReocrItemsZone
                != expectation.shouldReocrItemsZone
            {
                differences.append("should_reocr")
            }
            if result.sections.contains(where: {
                $0.modelSource != swiftWorkerModelSource
                    || $0.extractorVersion != swiftWorkerExtractorVersion
            }) {
                differences.append("section provenance")
            }
            if differences.isEmpty {
                passed += 1
            } else {
                failures.append(
                    "\(receipt.merchant ?? "?") \(receipt.imageId)"
                        + "#\(receipt.receiptId): "
                        + differences.joined(separator: ", ")
                )
            }
        }
        #expect(
            passed == expected.count,
            Comment(
                rawValue:
                    "pipeline parity \(passed)/\(expected.count); failures:\n"
                    + failures.joined(separator: "\n")
            )
        )
    }

    @Test func contractReceiptFindsAndReconcilesPrintedSubtotal() throws {
        let fixture = try loadContract()
        let receipt = try #require(fixture.receipts.first)
        let merchant = merchantNameFromLayoutPredictions(
            receipt.layoutlmPredictions
        )
        #expect(merchant == "SPROUTS FARMERS MARKET")
        var lines = receipt.lines
        let total = try #require(lines.last)
        lines[lines.count - 1] = Line(
            text: "SUBTOTAL 3.99",
            boundingBox: total.boundingBox,
            topLeft: total.topLeft,
            topRight: total.topRight,
            bottomLeft: total.bottomLeft,
            bottomRight: total.bottomRight,
            angleDegrees: total.angleDegrees,
            angleRadians: total.angleRadians,
            confidence: total.confidence,
            words: total.words
        )
        let result = try buildOnDeviceReceiptStructure(
            lines: lines, receiptId: receipt.clusterId,
            merchantName: merchant
        )
        #expect(equalMoney(result.printedSubtotal, 3.99))
        #expect(result.reconciliationStatus == "match")
        #expect(result.shouldReocrItemsZone == false)
        #expect(result.lineItems.count == 1)
        #expect(equalMoney(result.lineItems.first?.price, 3.99))
        #expect(result.lineItems.first?.modelSource == swiftWorkerModelSource)
        #expect(
            result.lineItems.first?.extractorVersion
                == swiftWorkerExtractorVersion
        )
    }

    /// The decoder version the worker stamps must stay equal to the canonical
    /// Python `EXTRACTOR_VERSION`; a silent skew is what let the port fork
    /// once already (STATE_OF_THE_SYSTEM warning #1).
    @Test func extractorVersionTracksCanonicalPythonDecoder() throws {
        let processor = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()  // ReceiptOCRCoreTests
            .deletingLastPathComponent()  // Tests
            .deletingLastPathComponent()  // receipt_ocr_swift
            .deletingLastPathComponent()  // repo root
            .appendingPathComponent(
                "infra/receipt_line_item_updater/line_item_processor.py"
            )
        let source = try String(contentsOf: processor, encoding: .utf8)
        let expected =
            "EXTRACTOR_VERSION = \"\(swiftWorkerDecoderVersion)\""
        #expect(
            source.contains(expected),
            Comment(
                rawValue:
                    "Swift decoder version \(swiftWorkerDecoderVersion) no "
                    + "longer matches the canonical Python EXTRACTOR_VERSION"
            )
        )
        #expect(
            swiftWorkerExtractorVersion
                == "\(swiftWorkerModelSource)+\(swiftWorkerDecoderVersion)"
        )
    }
}
