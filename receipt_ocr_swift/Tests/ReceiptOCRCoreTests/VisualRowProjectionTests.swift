import Foundation
import Testing

@testable import ReceiptOCRCore

/// Geometry invariants; private receipt replays provide acceptance evidence.
@Suite struct VisualRowProjectionTests {
    private func line(
        _ id: Int, x: Double, width: Double, baseline: Double,
        slope: Double, corners: Bool = true
    ) -> SectionLine {
        let height = 0.012
        let points = [
            SectionPoint(x: x, y: baseline + slope * x + height),
            SectionPoint(x: x + width, y: baseline + slope * (x + width) + height),
            SectionPoint(x: x + width, y: baseline + slope * (x + width)),
            SectionPoint(x: x, y: baseline + slope * x),
        ]
        let ys = points.map(\.y)
        return SectionLine(
            imageId: "", receiptId: 1, lineId: id, text: "",
            boundingBox: SectionRect(
                x: x, y: ys.min()!, width: width,
                height: ys.max()! - ys.min()!
            ),
            words: [], corners: corners ? points : []
        )
    }

    @Test(arguments: [-0.12, 0.0, 0.12])
    func slantedNamesPairWithTheirOwnAmountColumn(slope: Double) {
        let lines = [
            line(1, x: 0.1, width: 0.5, baseline: 0.7, slope: slope),
            line(2, x: 0.8, width: 0.05, baseline: 0.7, slope: slope),
            line(3, x: 0.1, width: 0.5, baseline: 0.66, slope: slope),
            line(4, x: 0.8, width: 0.05, baseline: 0.66, slope: slope),
        ]
        #expect(groupLinesIntoVisualRows(lines).map { $0.map(\.lineId) } == [[1, 2], [3, 4]])
    }

    @Test func cornerlessLegacyGeometryKeepsAxisAlignedRows() {
        let lines = [
            line(1, x: 0.1, width: 0.5, baseline: 0.7, slope: 0, corners: false),
            line(2, x: 0.8, width: 0.05, baseline: 0.7, slope: 0, corners: false),
            line(3, x: 0.1, width: 0.5, baseline: 0.66, slope: 0, corners: false),
        ]
        #expect(groupLinesIntoVisualRows(lines).map { $0.map(\.lineId) } == [[1, 2], [3]])
    }

    @Test func mixedLegacyAmountUsesTheSameProjectionFrame() {
        let lines = [
            line(1, x: 0.1, width: 0.5, baseline: 0.7, slope: 0.12),
            line(2, x: 0.8, width: 0.05, baseline: 0.7, slope: 0.12, corners: false),
            line(3, x: 0.1, width: 0.5, baseline: 0.66, slope: 0.12),
        ]
        #expect(groupLinesIntoVisualRows(lines).map { $0.map(\.lineId) } == [[1, 2], [3]])
    }

    @Test func cornerlessWideLineIsNotWidenedByProjection() {
        let lines = [
            line(1, x: 0.1, width: 0.5, baseline: 0.7, slope: 0.04, corners: false),
            line(2, x: 0.8, width: 0.05, baseline: 0.7, slope: 0.04),
            line(3, x: 0.1, width: 0.5, baseline: 0.675, slope: 0.04),
            line(4, x: 0.8, width: 0.05, baseline: 0.675, slope: 0.04),
        ]
        #expect(groupLinesIntoVisualRows(lines).map { $0.map(\.lineId) } == [[1, 2], [3, 4]])
    }

    @Test func wideTiltedHeaderDoesNotOutweighHorizontalBody() {
        let lines = [
            line(0, x: 0.05, width: 0.8, baseline: 0.85, slope: 0.04),
            line(1, x: 0.1, width: 0.2, baseline: 0.7, slope: 0),
            line(2, x: 0.8, width: 0.05, baseline: 0.7, slope: 0),
            line(3, x: 0.1, width: 0.2, baseline: 0.675, slope: 0),
            line(4, x: 0.8, width: 0.05, baseline: 0.675, slope: 0),
        ]
        #expect(groupLinesIntoVisualRows(lines).map { $0.map(\.lineId) } == [[0], [1, 2], [3, 4]])
    }

    @Test func projectedReadingOrderSurvivesSectionFeatureExtraction() {
        let lines = [
            line(1, x: 0.1, width: 0.5, baseline: 0.7, slope: 0.12),
            line(2, x: 0.1, width: 0.5, baseline: 0.66, slope: 0.12),
            line(3, x: 0.8, width: 0.05, baseline: 0.66, slope: 0.12),
        ]
        let rows = buildReceiptRows(lines: lines)
        #expect(rows.map(\.lineIds) == [[1], [2, 3]])
        let features = extractSectionRowFeatures(rows: Array(rows.reversed()), lines: lines)
        #expect(features.map { $0.row.lineIds } == [[1], [2, 3]])
    }
}
