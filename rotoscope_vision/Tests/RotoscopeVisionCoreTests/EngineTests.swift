import XCTest

@testable import RotoscopeVisionCore

/// Engine checks; `rotoscope_vision/scripts/engine_check/main.swift` mirrors these
/// for machines with only Command Line Tools (no XCTest).
final class EngineTests: XCTestCase {
    func testRec601GrayMatchesTheBrowserEngine() {
        // Same integer weights as algorithm.ts: (77R + 150G + 29B + 128) >> 8.
        XCTAssertEqual(Engine.rec601Gray(72, 48, 40), 54)
        XCTAssertEqual(Engine.rec601Gray(255, 255, 255), 255)
        XCTAssertEqual(Engine.rec601Gray(0, 0, 0), 0)
    }

    func testBoxBlurAveragesWithClampedEdges() {
        // 1×5 row, radius 1: edges average two samples, interior three.
        let blurred = Engine.boxBlur([0, 90, 0, 90, 0], width: 5, height: 1, radius: 1)
        XCTAssertEqual(blurred, [45, 30, 60, 30, 45])
    }

    func testSobelSeesAVerticalEdge() {
        // 5×3 image, left three columns 0, right two columns 200.
        var image = [UInt8](repeating: 0, count: 15)
        for y in 0..<3 { for x in 3..<5 { image[y * 5 + x] = 200 } }
        let gradient = Engine.sobel(image, width: 5, height: 3)
        XCTAssertEqual(gradient.x[1 * 5 + 2], 800)
        XCTAssertEqual(gradient.y[1 * 5 + 2], 0)
        XCTAssertEqual(gradient.magnitude[1 * 5 + 2], 200)
    }

    func testTierQuotaDistributesRemainderByLargestFraction() {
        XCTAssertEqual(Engine.tierQuota(budget: 10, quotas: TierValues(face: 0.3, body: 0.7, background: 0)), [3, 7, 0])
        XCTAssertEqual(Engine.tierQuota(budget: 7, quotas: TierValues(face: 1, body: 1, background: 1)), [3, 2, 2])
    }

    func testWatershedLabelsEverythingButTheBarrier() {
        let width = 6
        let height = 4
        let gradient = [UInt8](repeating: 0, count: width * height)
        var barrier = [UInt8](repeating: 0, count: width * height)
        for y in 0..<height { for x in 4..<6 { barrier[y * width + x] = 1 } }
        let result = Engine.watershed(
            gradient: gradient, width: width, height: height, markers: [0, 3 * width + 3], barrier: barrier)
        XCTAssertEqual(result.regionCount, 2)
        for y in 0..<height {
            for x in 0..<width {
                let label = result.labels[y * width + x]
                if x >= 4 { XCTAssertEqual(label, 0, "barrier stays unlabeled") } else { XCTAssertNotEqual(label, 0) }
            }
        }
    }

    func testColorizeAveragesEachRegionAndClearsBarrierAlpha() {
        let rgba: [UInt8] = [100, 0, 0, 255, 200, 0, 0, 255, 9, 9, 9, 255]
        let out = Engine.colorize(rgba: rgba, labels: [1, 1, 0], count: 3, regionCount: 1, alpha: [255, 255, 255])
        XCTAssertEqual(Array(out[0..<4]), [150, 0, 0, 255])
        XCTAssertEqual(Array(out[4..<8]), [150, 0, 0, 255])
        XCTAssertEqual(out[11], 0, "barrier pixels come out transparent")
    }

    func testProcessRemovesBackgroundAndKeepsSubjectOpaque() {
        let width = 16
        let height = 12
        let count = width * height
        var rgba = [UInt8](repeating: 0, count: count * 4)
        var tiers = [UInt8](repeating: FocusTier.background.rawValue, count: count)
        var alpha = [UInt8](repeating: 0, count: count)
        for y in 0..<height {
            for x in 0..<width {
                let index = y * width + x
                let subject = x >= 4 && x < 12
                rgba[index * 4] = subject ? 200 : 20
                rgba[index * 4 + 1] = UInt8((x * 13 + y * 7) % 256)
                rgba[index * 4 + 2] = 60
                rgba[index * 4 + 3] = 255
                if subject {
                    tiers[index] = FocusTier.body.rawValue
                    alpha[index] = 255
                }
            }
        }
        var options = EngineOptions()
        options.markerBudget = 8
        options.blurRadius = 1
        let frame = Engine.process(
            rgba: rgba, width: width, height: height, tiers: tiers, alpha: alpha, removeBackground: true,
            options: options)
        XCTAssertGreaterThan(frame.regionCount, 0)
        XCTAssertEqual(frame.markers.backgroundCount, 0)
        for y in 0..<height {
            for x in 0..<width {
                let a = frame.rgba[(y * width + x) * 4 + 3]
                if x >= 4 && x < 12 { XCTAssertEqual(a, 255) } else { XCTAssertEqual(a, 0) }
            }
        }
    }
}
