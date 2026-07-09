import Foundation

#if os(macOS)
import CoreGraphics

/// Image types that can be detected by the classifier
public enum ImageType: String, Codable, CaseIterable {
    case native = "NATIVE"   // Single receipt fills the image (margins < 1%)
    case photo = "PHOTO"     // Phone camera photo (~4032x3024)
    case scan = "SCAN"       // Scanner document (~3508x2480)
}

/// Reference dimensions for known image formats
public struct ReferenceFormat {
    /// Standard scanner dimensions (300 DPI, A4: 11.7" x 8.27")
    public static let scanner = (width: 3508, height: 2480)

    /// Typical phone camera dimensions
    public static let phone = (width: 4032, height: 3024)
}

/// Margins around text content in an image
public struct ImageMargins: Codable {
    public let left: CGFloat
    public let right: CGFloat
    public let top: CGFloat
    public let bottom: CGFloat

    /// Check if all margins are below a threshold (indicates NATIVE type)
    public func allBelow(_ threshold: CGFloat) -> Bool {
        return left < threshold && right < threshold && top < threshold && bottom < threshold
    }
}

/// Classification result containing image type and metadata
public struct ClassificationResult: Codable {
    public let imageType: ImageType
    public let margins: ImageMargins
    public let scanDistance: CGFloat
    public let photoDistance: CGFloat
    public let imageWidth: Int
    public let imageHeight: Int

    private enum CodingKeys: String, CodingKey {
        case imageType = "image_type"
        case margins
        case scanDistance = "scan_distance"
        case photoDistance = "photo_distance"
        case imageWidth = "image_width"
        case imageHeight = "image_height"
    }
}

/// Classifies images as NATIVE, PHOTO, or SCAN based on OCR line positions and image dimensions
public struct ImageClassifier {

    /// Threshold for margin detection (1% = 0.01)
    private let marginThreshold: CGFloat

    public init(marginThreshold: CGFloat = 0.01) {
        self.marginThreshold = marginThreshold
    }

    /// Calculate Euclidean distance between given dimensions and a reference format.
    /// Handles both normal and rotated (90°) orientations.
    ///
    /// - Parameters:
    ///   - width: Image width to check
    ///   - height: Image height to check
    ///   - referenceWidth: Reference format width
    ///   - referenceHeight: Reference format height
    /// - Returns: The smaller of the normalized distances between normal and rotated orientations
    private func dimensionDistance(
        width: Int,
        height: Int,
        referenceWidth: Int,
        referenceHeight: Int
    ) -> CGFloat {
        let w = CGFloat(width)
        let h = CGFloat(height)
        let refW = CGFloat(referenceWidth)
        let refH = CGFloat(referenceHeight)

        // Calculate distance for normal orientation
        let dx1 = (w - refW) / refW
        let dy1 = (h - refH) / refH
        let normalDistance = sqrt(dx1 * dx1 + dy1 * dy1)

        // Calculate distance for rotated orientation (swap width/height)
        let dx2 = (w - refH) / refH
        let dy2 = (h - refW) / refW
        let rotatedDistance = sqrt(dx2 * dx2 + dy2 * dy2)

        // Return the smaller distance (better match)
        return min(normalDistance, rotatedDistance)
    }

    /// Find the margins between text boundaries and image edges.
    ///
    /// - Parameter lines: Array of OCR lines with normalized coordinates (0-1)
    /// - Returns: ImageMargins with distances from text to image edges
    public func findMargins(lines: [Line]) -> ImageMargins {
        guard !lines.isEmpty else {
            return ImageMargins(left: 1.0, right: 1.0, top: 1.0, bottom: 1.0)
        }

        var leftMargin: CGFloat = 1.0
        var rightMargin: CGFloat = 1.0
        var topMargin: CGFloat = 1.0
        var bottomMargin: CGFloat = 1.0

        for line in lines {
            // Left margin - distance from x=0 to leftmost text
            leftMargin = min(leftMargin, line.topLeft.x)

            // Right margin - distance from rightmost text to x=1
            rightMargin = min(rightMargin, 1.0 - line.topRight.x)

            // Bottom margin - distance from y=0 to bottommost text
            bottomMargin = min(bottomMargin, line.bottomLeft.y)

            // Top margin - distance from topmost text to y=1
            topMargin = min(topMargin, 1.0 - line.topLeft.y)
        }

        return ImageMargins(
            left: leftMargin,
            right: rightMargin,
            top: topMargin,
            bottom: bottomMargin
        )
    }

    /// Classify an image based on OCR lines and image dimensions.
    ///
    /// - Parameters:
    ///   - lines: Array of OCR lines with normalized coordinates
    ///   - imageWidth: Width of the image in pixels
    ///   - imageHeight: Height of the image in pixels
    /// - Returns: ClassificationResult with image type and metadata
    public func classify(
        lines: [Line],
        imageWidth: Int,
        imageHeight: Int,
        image: CGImage? = nil
    ) -> ClassificationResult {
        let margins = findMargins(lines: lines)

        // Calculate distances to known formats
        let scanDistance = dimensionDistance(
            width: imageWidth,
            height: imageHeight,
            referenceWidth: ReferenceFormat.scanner.width,
            referenceHeight: ReferenceFormat.scanner.height
        )

        let photoDistance = dimensionDistance(
            width: imageWidth,
            height: imageHeight,
            referenceWidth: ReferenceFormat.phone.width,
            referenceHeight: ReferenceFormat.phone.height
        )

        // Determine image type
        let imageType: ImageType
        if margins.allBelow(marginThreshold) {
            // Text fills the image - single receipt
            imageType = .native
        } else if let image = image, let feat = pixelFeatures(image: image) {
            // PRIMARY signal: a SCAN is a white document that fills the frame,
            // so its border is overwhelmingly near-white with ~0 saturation.
            // A PHOTO has a table/scene background -> low border-white, higher
            // saturation. This is resolution-invariant, unlike comparing pixel
            // dimensions to fixed references (which mislabels downscaled photos
            // as scans). Calibrated margin is huge: real scans ~0.9-1.0
            // border-white, photos <0.01.
            if feat.borderWhite > 0.5 && feat.meanSat < 0.05 {
                imageType = .scan
            } else {
                imageType = .photo
            }
        } else if scanDistance < photoDistance {
            // Fallback (no pixels available): dimension heuristic.
            imageType = .scan
        } else {
            // When scanDistance == photoDistance, defaults to .photo
            imageType = .photo
        }

        return ClassificationResult(
            imageType: imageType,
            margins: margins,
            scanDistance: scanDistance,
            photoDistance: photoDistance,
            imageWidth: imageWidth,
            imageHeight: imageHeight
        )
    }

    /// Cheap pixel-histogram features for scan-vs-photo discrimination.
    ///
    /// Downscales the image to a 64x64 thumbnail (one draw, ~4k pixels) and
    /// measures how "document-like" the frame is:
    ///   - borderWhite: fraction of near-white, near-zero-saturation pixels in
    ///     the outer 12% border. A scanned document fills the frame in white
    ///     paper (~0.9-1.0); a photo shows a table/scene background (~0).
    ///   - meanSat: mean HSV saturation over the whole thumbnail. Scans are
    ///     grayscale paper+ink (~0); photos have color (>0.1).
    ///
    /// Returns nil if a bitmap context can't be created.
    private func pixelFeatures(
        image: CGImage
    ) -> (borderWhite: CGFloat, meanSat: CGFloat)? {
        let side = 64
        let bytesPerRow = side * 4
        var data = [UInt8](repeating: 0, count: side * side * 4)
        guard
            let ctx = CGContext(
                data: &data,
                width: side,
                height: side,
                bitsPerComponent: 8,
                bytesPerRow: bytesPerRow,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            )
        else { return nil }
        ctx.interpolationQuality = .low
        ctx.draw(
            image,
            in: CGRect(x: 0, y: 0, width: side, height: side)
        )

        let borderPx = max(1, Int(CGFloat(side) * 0.12))
        var whiteBorder = 0
        var borderCount = 0
        var satSum: CGFloat = 0

        for y in 0..<side {
            for x in 0..<side {
                let i = (y * side + x) * 4
                let r = CGFloat(data[i]) / 255.0
                let g = CGFloat(data[i + 1]) / 255.0
                let b = CGFloat(data[i + 2]) / 255.0
                let mx = max(r, max(g, b))
                let mn = min(r, min(g, b))
                let sat = mx > 0 ? (mx - mn) / mx : 0
                let lum = 0.299 * r + 0.587 * g + 0.114 * b
                satSum += sat

                let isBorder =
                    x < borderPx || x >= side - borderPx
                    || y < borderPx || y >= side - borderPx
                if isBorder {
                    borderCount += 1
                    if lum > 0.85 && sat < 0.12 {
                        whiteBorder += 1
                    }
                }
            }
        }

        let borderWhite =
            borderCount > 0
            ? CGFloat(whiteBorder) / CGFloat(borderCount) : 0
        let meanSat = satSum / CGFloat(side * side)
        return (borderWhite, meanSat)
    }
}
#endif
