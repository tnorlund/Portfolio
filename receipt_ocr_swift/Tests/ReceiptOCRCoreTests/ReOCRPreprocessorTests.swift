import Foundation
import Testing

@testable import ReceiptOCRCore

#if os(macOS)
import CoreGraphics

/// Unit tests for the REGIONAL_REOCR preprocess transforms on synthetic
/// images. Uses swift-testing (not XCTest) so the suite also runs on
/// machines with only CommandLineTools.
@Suite struct ReOCRPreprocessorTests {

    // MARK: - plain

    @Test func plainIsPassthrough() {
        let image = ReOCRTestImages.makeImage(width: 40, height: 30) { ctx in
            ctx.setFillColor(CGColor(red: 0.5, green: 0.5, blue: 0.5, alpha: 1))
            ctx.fill(CGRect(x: 0, y: 0, width: 40, height: 30))
        }
        let result = ReOCRPreprocessor.apply(.plain, to: image)
        #expect(result === image)
    }

    // MARK: - invert

    @Test func invertReversesReverseVideoSample() {
        // Reverse-video thermal sample: light "text" square on a dark
        // background. After inversion the background must be light and the
        // text region dark (dark-on-light, what Vision expects).
        let image = ReOCRTestImages.makeImage(width: 100, height: 100) { ctx in
            ctx.setFillColor(CGColor(red: 30.0 / 255, green: 30.0 / 255, blue: 30.0 / 255, alpha: 1))
            ctx.fill(CGRect(x: 0, y: 0, width: 100, height: 100))
            ctx.setFillColor(CGColor(red: 240.0 / 255, green: 240.0 / 255, blue: 240.0 / 255, alpha: 1))
            ctx.fill(CGRect(x: 40, y: 40, width: 20, height: 20))
        }
        let inverted = ReOCRPreprocessor.apply(.invert, to: image)
        #expect(inverted.width == 100)
        #expect(inverted.height == 100)

        let background = ReOCRTestImages.pixel(in: inverted, x: 5, y: 5)
        let text = ReOCRTestImages.pixel(in: inverted, x: 50, y: 50)
        // 30 -> ~225, 240 -> ~15 (small tolerance for colorspace rounding).
        #expect(background.r > 200 && background.g > 200 && background.b > 200)
        #expect(text.r < 60 && text.g < 60 && text.b < 60)
    }

    // MARK: - upscale2x

    @Test func upscale2xDoublesDimensions() {
        let image = ReOCRTestImages.makeImage(width: 100, height: 60) { ctx in
            ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
            ctx.fill(CGRect(x: 0, y: 0, width: 100, height: 60))
        }
        let upscaled = ReOCRPreprocessor.apply(.upscale2x, to: image)
        #expect(upscaled.width == 200)
        #expect(upscaled.height == 120)
    }

    // MARK: - deskew

    @Test func deskewReducesTiltBelowTwoDegrees() throws {
        let upright = ReOCRTestImages.makeTextImage()
        // Sanity: the detector must see the synthetic text at all.
        let uprightAngle = try #require(ReOCRPreprocessor.detectDominantTextAngleDegrees(upright))
        #expect(abs(uprightAngle) < 2.0)

        // Tilt by 8 degrees and confirm the detector reports roughly that.
        let tilted = ReOCRPreprocessor.rotate(upright, byDegrees: 8.0)
        let tiltedAngle = try #require(ReOCRPreprocessor.detectDominantTextAngleDegrees(tilted))
        #expect(abs(tiltedAngle - 8.0) < 3.0)

        // Deskew must bring the residual dominant angle below 2 degrees.
        let deskewed = ReOCRPreprocessor.apply(.deskew, to: tilted)
        let residual = try #require(ReOCRPreprocessor.detectDominantTextAngleDegrees(deskewed))
        #expect(abs(residual) < 2.0)
    }

    @Test func deskewLeavesUprightTextUntouched() {
        // Below the minimum-angle threshold the image is returned as-is
        // (no pointless resample).
        let upright = ReOCRTestImages.makeTextImage()
        let result = ReOCRPreprocessor.apply(.deskew, to: upright)
        #expect(result.width == upright.width)
        #expect(result.height == upright.height)
    }

    @Test func deskewWithoutTextIsPassthrough() {
        // A blank image has no text observations: deskew must return the
        // original rather than fail.
        let blank = ReOCRTestImages.makeImage(width: 64, height: 64) { ctx in
            ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
            ctx.fill(CGRect(x: 0, y: 0, width: 64, height: 64))
        }
        let result = ReOCRPreprocessor.apply(.deskew, to: blank)
        #expect(result === blank)
    }
}
#endif
