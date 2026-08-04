import Foundation
#if os(macOS)
import CoreGraphics
import CoreImage
import Vision

/// Preprocessing transforms applied to a cropped REGIONAL_REOCR region
/// before it is handed to Vision OCR. Strategies (contract with the
/// Python side, see `ReOCRStrategy`):
///
/// - `plain`: passthrough.
/// - `invert`: color inversion — recovers reverse-video thermal print
///   (light-on-dark) into the dark-on-light polarity Vision expects.
/// - `deskew`: detect the dominant text angle with a quick Vision
///   text-recognition pass and rotate the crop upright.
/// - `upscale2x`: 2x Lanczos upscale for tiny print.
///
/// Pure CoreGraphics/CoreImage/Vision (all already dependencies of this
/// target) — no new package dependencies. Every transform is best-effort:
/// on any internal failure the original image is returned unchanged so a
/// bad preprocess can never lose the re-OCR entirely.
public enum ReOCRPreprocessor {

    /// Deskew only fires when the detected dominant angle exceeds this
    /// (degrees); below it a rotation would just resample and blur.
    public static let deskewMinAngleDegrees = 0.5
    /// Angles beyond this are treated as detection noise (vertical text,
    /// stray observations) and excluded from the dominant-angle estimate.
    public static let deskewMaxAngleDegrees = 45.0

    /// Color management is disabled: with the default (linear) working
    /// space CIColorInvert inverts linearized values, turning gamma-240
    /// "text" into ~101 mid-gray instead of ~15 — low-contrast output that
    /// defeats the point of the invert strategy. Unmanaged math gives the
    /// photometric 255−v flip reverse-video recovery needs.
    private static let ciContext = CIContext(options: [
        .useSoftwareRenderer: false,
        .workingColorSpace: NSNull(),
        .outputColorSpace: NSNull(),
    ])

    // MARK: - Dispatch

    /// Apply `strategy` to `image`, returning the transformed image (or
    /// the original for `plain` / on failure).
    public static func apply(_ strategy: ReOCRStrategy, to image: CGImage) -> CGImage {
        switch strategy {
        case .plain:
            return image
        case .invert:
            return invert(image)
        case .deskew:
            return deskew(image)
        case .upscale2x:
            return upscale2x(image)
        }
    }

    // MARK: - Transforms

    /// Color inversion (CIColorInvert). Reverse-video thermal print
    /// becomes dark-on-light.
    public static func invert(_ image: CGImage) -> CGImage {
        let ci = CIImage(cgImage: image)
        guard let filter = CIFilter(name: "CIColorInvert") else { return image }
        filter.setValue(ci, forKey: kCIInputImageKey)
        guard let output = filter.outputImage,
              let result = ciContext.createCGImage(output, from: output.extent)
        else { return image }
        return result
    }

    /// 2x Lanczos upscale (CILanczosScaleTransform).
    public static func upscale2x(_ image: CGImage) -> CGImage {
        let ci = CIImage(cgImage: image)
        guard let filter = CIFilter(name: "CILanczosScaleTransform") else { return image }
        filter.setValue(ci, forKey: kCIInputImageKey)
        filter.setValue(2.0, forKey: kCIInputScaleKey)
        filter.setValue(1.0, forKey: kCIInputAspectRatioKey)
        guard let output = filter.outputImage,
              let result = ciContext.createCGImage(output, from: output.extent)
        else { return image }
        return result
    }

    /// Detect the dominant text angle and rotate the crop upright.
    /// If no text is detected, or the angle is already below
    /// `deskewMinAngleDegrees`, the image is returned unchanged.
    public static func deskew(_ image: CGImage) -> CGImage {
        guard let angle = detectDominantTextAngleDegrees(image),
              abs(angle) >= deskewMinAngleDegrees
        else { return image }
        return rotate(image, byDegrees: -angle)
    }

    /// Dominant text angle in degrees, estimated from the text-line quads
    /// of an `.accurate` Vision recognition pass. Positive = text rises
    /// left-to-right (counter-clockwise in standard math orientation).
    /// Returns nil when no usable text observations are found.
    public static func detectDominantTextAngleDegrees(_ image: CGImage) -> Double? {
        let request = VNRecognizeTextRequest()
        // .accurate, not .fast: the fast path reports axis-aligned quads,
        // so every baseline measures ~0° and deskew would silently never
        // fire. Only .accurate returns the true rotated text quads.
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        guard (try? handler.perform([request])) != nil,
              let observations = request.results, !observations.isEmpty
        else { return nil }

        let width = Double(image.width)
        let height = Double(image.height)
        // Per-observation baseline angle from the bottom edge of the text
        // quad, converted from normalized to pixel space so non-square
        // images do not distort the angle. Weighted by baseline length so
        // long lines dominate over stray short fragments.
        var samples: [(angle: Double, weight: Double)] = []
        for obs in observations {
            let dx = (Double(obs.bottomRight.x) - Double(obs.bottomLeft.x)) * width
            let dy = (Double(obs.bottomRight.y) - Double(obs.bottomLeft.y)) * height
            let length = (dx * dx + dy * dy).squareRoot()
            guard length > 0 else { continue }
            let degrees = atan2(dy, dx) * 180.0 / .pi
            guard abs(degrees) <= deskewMaxAngleDegrees else { continue }
            samples.append((degrees, length))
        }
        guard !samples.isEmpty else { return nil }
        // Weighted median: robust to a minority of misdetected quads.
        let sorted = samples.sorted { $0.angle < $1.angle }
        let total = sorted.reduce(0.0) { $0 + $1.weight }
        var cumulative = 0.0
        for sample in sorted {
            cumulative += sample.weight
            if cumulative >= total / 2.0 { return sample.angle }
        }
        return sorted.last?.angle
    }

    /// Rotate by `degrees` (positive = counter-clockwise), compositing
    /// over a white background so the corners uncovered by the rotation
    /// stay receipt-paper white instead of black/transparent.
    public static func rotate(_ image: CGImage, byDegrees degrees: Double) -> CGImage {
        let radians = degrees * .pi / 180.0
        let ci = CIImage(cgImage: image)
        // Rotate about the image center.
        let center = CGPoint(x: ci.extent.midX, y: ci.extent.midY)
        let transform = CGAffineTransform(translationX: center.x, y: center.y)
            .rotated(by: CGFloat(radians))
            .translatedBy(x: -center.x, y: -center.y)
        let rotated = ci.transformed(by: transform)
        let extent = rotated.extent.integral
        let background = CIImage(color: CIColor.white).cropped(to: extent)
        let composited = rotated.composited(over: background)
        guard let result = ciContext.createCGImage(composited, from: extent) else { return image }
        return result
    }
}
#endif
