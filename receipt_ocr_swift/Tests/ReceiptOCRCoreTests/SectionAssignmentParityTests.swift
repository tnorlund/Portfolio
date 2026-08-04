import Foundation
import Testing

@testable import ReceiptOCRCore

/// Golden parity gate for the Swift ports of `build_receipt_rows` and the
/// deterministic semi-Markov section assigner.
@Suite struct SectionAssignmentParityTests {
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

        enum CodingKeys: String, CodingKey {
            case imageId = "image_id"
            case receiptId = "receipt_id"
            case sections
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

    /// Reconstruct the same zero-width facades as the Python expectation
    /// generator. The fixture does not retain original word widths or lines.
    private func reconstruct(_ receipt: OCRReceipt) -> (
        lines: [SectionLine], words: [SectionWord]
    ) {
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
        let lines = grouped.keys.sorted().map { lineId in
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
        return (lines, words)
    }

    @Test func goldenParityAcrossTheWholeGoldenSet() throws {
        let fixture = try load("line_items_golden_ocr", as: OCRFixture.self)
        let expected = try load(
            "section_assignment_parity_expected",
            as: [ExpectedReceipt].self
        )
        // Count derived, not pinned: the golden set grows (33 -> 35).
        #expect(fixture.receipts.count == expected.count)
        #expect(expected.count >= 35)
        let model = try loadSectionPriorModel()

        var passed = 0
        var failures: [String] = []
        for (receipt, expectedReceipt) in zip(fixture.receipts, expected) {
            #expect(receipt.imageId == expectedReceipt.imageId)
            #expect(receipt.receiptId == expectedReceipt.receiptId)
            let geometry = reconstruct(receipt)
            let rows = buildReceiptRows(
                lines: geometry.lines, words: geometry.words
            )
            let sections = sectionsFromAssignments(
                assignRowSections(
                    rows: rows, lines: geometry.lines, model: model,
                    merchantName: receipt.merchant
                )
            )
            let gotTypes = sections.map(\.sectionType)
            let expectedTypes = expectedReceipt.sections.map(\.sectionType)
            let gotLineIds = sections.map(\.lineIds)
            let expectedLineIds = expectedReceipt.sections.map(\.lineIds)
            if gotTypes == expectedTypes && gotLineIds == expectedLineIds {
                passed += 1
            } else {
                failures.append(
                    "\(receipt.merchant ?? "?") \(receipt.imageId)"
                        + "#\(receipt.receiptId): types \(gotTypes) != "
                        + "\(expectedTypes); line_ids \(gotLineIds) != "
                        + "\(expectedLineIds)"
                )
            }
        }

        #expect(
            passed == expected.count,
            Comment(
                rawValue:
                    "section parity \(passed)/\(expected.count); failures:\n"
                    + failures.joined(separator: "\n")
            )
        )
    }
}
