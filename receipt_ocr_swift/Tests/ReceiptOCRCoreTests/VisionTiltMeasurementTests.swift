import Foundation
import Testing

@testable import ReceiptOCRCore

#if os(macOS)
import AppKit
import CoreGraphics
import CoreText

/// Tilt must be MEASURED, not hardcoded. Until 2026-08 VisionOCREngine wrote
/// `angleDegrees: 0.0` for every letter/word/line and synthesized quad
/// corners from the axis-aligned bounding box, so no receipt in the corpus
/// ever carried tilt and tilted receipts could not be fixed downstream
/// (docs/line-items/handoff/SWIFT_AND_GEOMETRY.md). These tests run real
/// Vision OCR on synthetic upright and tilted text and assert the measured
/// geometry. swift-testing so the suite runs under CommandLineTools too.
@Suite struct VisionTiltMeasurementTests {

    /// Live Vision inference stalls on GitHub's headless macOS runners
    /// (the first swift-ci run of live-Vision tests sat >30 min inside
    /// VNRecognizeTextRequest), so these run off-CI only — on real
    /// hardware, where RUNNERS.md declares the mini the authoritative
    /// Swift machine.
    private static var liveVisionAvailable: Bool {
        ProcessInfo.processInfo.environment["CI"] == nil
    }

    /// White canvas with receipt-like black text, drawn rotated by
    /// `tiltDegrees` about the image center (positive = counter-clockwise,
    /// matching Vision's bottom-left-origin angle convention).
    private func makeTextImage(tiltDegrees: CGFloat, width: Int = 800, height: Int = 600) -> CGImage {
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        let ctx = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: width * 4,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        )!
        ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))

        ctx.saveGState()
        ctx.translateBy(x: CGFloat(width) / 2, y: CGFloat(height) / 2)
        ctx.rotate(by: tiltDegrees * .pi / 180)
        ctx.translateBy(x: -CGFloat(width) / 2, y: -CGFloat(height) / 2)

        let font = CTFontCreateWithName("Helvetica" as CFString, 32, nil)
        let lines = [
            "RECEIPT TOTAL 12.34",
            "SUBTOTAL 10.00 TAX 2.34",
            "THANK YOU FOR SHOPPING",
        ]
        for (index, text) in lines.enumerated() {
            let attributes: [CFString: Any] = [
                kCTFontAttributeName: font,
                kCTForegroundColorAttributeName: CGColor(red: 0, green: 0, blue: 0, alpha: 1),
            ]
            let attributed = CFAttributedStringCreate(nil, text as CFString, attributes as CFDictionary)!
            let line = CTLineCreateWithAttributedString(attributed)
            ctx.textPosition = CGPoint(x: 120, y: CGFloat(height) - 180 - CGFloat(index) * 90)
            CTLineDraw(line, ctx)
        }
        ctx.restoreGState()
        return ctx.makeImage()!
    }

    /// Run the real engine on the image and return the parsed result JSON.
    private func ocrJSON(tiltDegrees: CGFloat) throws -> [String: Any] {
        let tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("tilt-test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tempDir) }

        let image = makeTextImage(tiltDegrees: tiltDegrees)
        let pngURL = tempDir.appendingPathComponent("input.png")
        let rep = NSBitmapImageRep(cgImage: image)
        try rep.representation(using: .png, properties: [:])!.write(to: pngURL)

        let engine = VisionOCREngine()
        let outputs = try engine.process(images: [pngURL], outputDirectory: tempDir, includeClassification: false)
        let data = try Data(contentsOf: try #require(outputs.first))
        return try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    private func lineDicts(_ json: [String: Any]) throws -> [[String: Any]] {
        let lines = try #require(json["lines"] as? [[String: Any]])
        // Only lines long enough to carry a stable baseline.
        return lines.filter { (($0["text"] as? String) ?? "").count >= 8 }
    }

    private func angles(_ lines: [[String: Any]]) -> [Double] {
        lines.compactMap { $0["angle_degrees"] as? Double }
    }

    @Test(.enabled(if: Self.liveVisionAvailable)) func uprightTextMeasuresNearZero() throws {
        let json = try ocrJSON(tiltDegrees: 0)
        let lines = try lineDicts(json)
        try #require(!lines.isEmpty)
        for angle in angles(lines) {
            #expect(abs(angle) < 2.0)
        }
    }

    @Test(.enabled(if: Self.liveVisionAvailable)) func tiltedTextMeasuresItsTilt() throws {
        let tilt = 8.0
        let json = try ocrJSON(tiltDegrees: tilt)
        let lines = try lineDicts(json)
        try #require(!lines.isEmpty)

        let measured = angles(lines)
        try #require(!measured.isEmpty)
        // Every long line reports roughly the drawn tilt with the right
        // sign, and words inherit the line angle.
        for angle in measured {
            #expect(abs(angle - tilt) < 3.0, "line angle \(angle) should be ~\(tilt)")
        }
        for line in lines {
            let lineAngle = try #require(line["angle_degrees"] as? Double)
            for word in try #require(line["words"] as? [[String: Any]]) {
                let wordAngle = try #require(word["angle_degrees"] as? Double)
                #expect(abs(wordAngle - lineAngle) < 0.001)
            }
        }
    }

    @Test(.enabled(if: Self.liveVisionAvailable)) func tiltedQuadCornersAreNotAxisAligned() throws {
        let json = try ocrJSON(tiltDegrees: 8.0)
        let lines = try lineDicts(json)
        try #require(!lines.isEmpty)
        for line in lines {
            let tl = try #require(line["top_left"] as? [String: Double])
            let tr = try #require(line["top_right"] as? [String: Double])
            // On an 8-degree tilt the top edge's endpoints differ in y by
            // a clearly non-zero amount (the old code emitted identical y).
            #expect(abs(tr["y"]! - tl["y"]!) > 0.005)
        }
    }

    @Test(.enabled(if: Self.liveVisionAvailable)) func angleRadiansMatchesDegrees() throws {
        let json = try ocrJSON(tiltDegrees: 8.0)
        for line in try lineDicts(json) {
            let deg = try #require(line["angle_degrees"] as? Double)
            let rad = try #require(line["angle_radians"] as? Double)
            #expect(abs(rad - deg * .pi / 180) < 1e-9)
        }
    }
}
#endif
