import Foundation

#if os(macOS)
import CoreGraphics
import CoreImage
import AppKit

// MARK: - Image Transform Errors

/// Errors that can occur during image transformation
public enum ImageTransformError: Error, LocalizedError {
    case invalidImage(String)
    case transformFailed(String)
    case renderFailed(String)

    public var errorDescription: String? {
        switch self {
        case .invalidImage(let msg): return "Invalid image: \(msg)"
        case .transformFailed(let msg): return "Transform failed: \(msg)"
        case .renderFailed(let msg): return "Render failed: \(msg)"
        }
    }
}

// MARK: - Perspective Transform

/// Apply a perspective transform to extract a quadrilateral region from an image.
///
/// Uses CoreImage's CIPerspectiveCorrection filter to warp the source quadrilateral
/// to a rectangular output. This is used for PHOTO receipts that have keystone distortion.
///
/// - Parameters:
///   - image: Source CGImage
///   - srcCorners: 4 source corners in image coordinates [TL, TR, BR, BL]
///   - outputSize: Desired output size (width, height)
/// - Returns: Warped CGImage, or nil if transform fails
public func applyPerspectiveTransform(
    image: CGImage,
    srcCorners: [(CGFloat, CGFloat)],
    outputSize: CGSize
) -> CGImage? {
    guard srcCorners.count == 4 else { return nil }

    let ciImage = CIImage(cgImage: image)
    let context = CIContext(options: [.useSoftwareRenderer: false])

    // CIPerspectiveCorrection expects corners as CIVector
    // Note: CoreImage uses bottom-left origin, so we may need to flip Y
    let imageHeight = CGFloat(image.height)

    // Convert to CoreImage coordinate system (Y=0 at bottom)
    let topLeft = CIVector(x: srcCorners[0].0, y: imageHeight - srcCorners[0].1)
    let topRight = CIVector(x: srcCorners[1].0, y: imageHeight - srcCorners[1].1)
    let bottomRight = CIVector(x: srcCorners[2].0, y: imageHeight - srcCorners[2].1)
    let bottomLeft = CIVector(x: srcCorners[3].0, y: imageHeight - srcCorners[3].1)

    // Use CIPerspectiveCorrection to correct the perspective
    guard let filter = CIFilter(name: "CIPerspectiveCorrection") else {
        return nil
    }

    filter.setValue(ciImage, forKey: kCIInputImageKey)
    filter.setValue(topLeft, forKey: "inputTopLeft")
    filter.setValue(topRight, forKey: "inputTopRight")
    filter.setValue(bottomRight, forKey: "inputBottomRight")
    filter.setValue(bottomLeft, forKey: "inputBottomLeft")

    guard let outputImage = filter.outputImage else {
        return nil
    }

    // Scale to desired output size
    let scaleX = outputSize.width / outputImage.extent.width
    let scaleY = outputSize.height / outputImage.extent.height
    let scaledImage = outputImage.transformed(by: CGAffineTransform(scaleX: scaleX, y: scaleY))

    // Render to CGImage
    let outputRect = CGRect(origin: .zero, size: outputSize)
    return context.createCGImage(scaledImage, from: outputRect)
}

// MARK: - Affine Transform

/// Apply an affine transform to extract a rotated rectangular region from an image.
///
/// Uses CoreImage's affine transform capability to warp the image. This is used
/// for SCAN receipts where the distortion is simpler (rotation + translation only).
///
/// - Parameters:
///   - image: Source CGImage
///   - srcCorners: 4 source corners in image coordinates [TL, TR, BR, BL]
///   - outputSize: Desired output size (width, height)
/// - Returns: Warped CGImage, or nil if transform fails
public func applyAffineTransform(
    image: CGImage,
    srcCorners: [(CGFloat, CGFloat)],
    outputSize: CGSize
) -> CGImage? {
    guard srcCorners.count == 4 else { return nil }

    let ciImage = CIImage(cgImage: image)
    let context = CIContext(options: [.useSoftwareRenderer: false])

    // For affine transform, we compute the transform from source corners to destination rectangle
    let srcTL = srcCorners[0]
    let srcTR = srcCorners[1]
    let srcBL = srcCorners[3]

    let dstW = outputSize.width
    let dstH = outputSize.height

    // Compute affine coefficients (dst -> src mapping for PIL-style transform)
    // We need the inverse (src -> dst) for CoreImage
    let a_i: CGFloat, b_i: CGFloat, c_i: CGFloat
    let d_i: CGFloat, e_i: CGFloat, f_i: CGFloat

    if dstW > 1 {
        a_i = (srcTR.0 - srcTL.0) / (dstW - 1)
        d_i = (srcTR.1 - srcTL.1) / (dstW - 1)
    } else {
        a_i = 0
        d_i = 0
    }

    if dstH > 1 {
        b_i = (srcBL.0 - srcTL.0) / (dstH - 1)
        e_i = (srcBL.1 - srcTL.1) / (dstH - 1)
    } else {
        b_i = 0
        e_i = 0
    }

    c_i = srcTL.0
    f_i = srcTL.1

    // Invert the affine transform to get src -> dst mapping
    guard let (a_f, b_f, c_f, d_f, e_f, f_f) = try? invertAffine(a: a_i, b: b_i, c: c_i, d: d_i, e: e_i, f: f_i) else {
        return nil
    }

    // CoreImage uses bottom-left origin, need to adjust
    let imageHeight = CGFloat(image.height)

    // Create the affine transform
    // The transform maps source coordinates to destination coordinates
    var transform = CGAffineTransform.identity

    // First, flip Y to convert from top-left to bottom-left origin
    transform = transform.translatedBy(x: 0, y: imageHeight)
    transform = transform.scaledBy(x: 1, y: -1)

    // Apply the computed affine transform
    let affine = CGAffineTransform(a: a_f, b: d_f, c: b_f, d: e_f, tx: c_f, ty: f_f)
    transform = transform.concatenating(affine)

    // Apply transform to image
    let transformedImage = ciImage.transformed(by: transform)

    // Crop to output size
    let outputRect = CGRect(x: 0, y: 0, width: outputSize.width, height: outputSize.height)

    // Render to CGImage
    return context.createCGImage(transformedImage, from: outputRect)
}

// MARK: - Alternative: Direct Perspective Transform Using CIPerspectiveTransform

/// Apply perspective transform using CIPerspectiveTransform filter.
///
/// This is an alternative approach that uses the raw perspective matrix.
///
/// - Parameters:
///   - image: Source CGImage
///   - perspectiveCoeffs: 8 perspective coefficients [a, b, c, d, e, f, g, h]
///   - outputSize: Desired output size
/// - Returns: Transformed CGImage, or nil if transform fails
public func applyPerspectiveWithCoeffs(
    image: CGImage,
    perspectiveCoeffs: [CGFloat],
    outputSize: CGSize
) -> CGImage? {
    guard perspectiveCoeffs.count == 8 else { return nil }

    let ciImage = CIImage(cgImage: image)
    let context = CIContext(options: [.useSoftwareRenderer: false])

    // The coefficients define: x' = (ax + by + c) / (gx + hy + 1)
    //                          y' = (dx + ey + f) / (gx + hy + 1)
    // We use CIPerspectiveCorrection with corner points computed from inverse transform

    // This approach requires computing where (0,0), (w,0), (w,h), (0,h) map to
    // using the inverse of our coefficients
    guard let invCoeffs = try? invertPerspective(coeffs: perspectiveCoeffs) else {
        return nil
    }

    func transformPoint(_ x: CGFloat, _ y: CGFloat) -> (CGFloat, CGFloat) {
        let a = invCoeffs[0], b = invCoeffs[1], c = invCoeffs[2]
        let d = invCoeffs[3], e = invCoeffs[4], f = invCoeffs[5]
        let g = invCoeffs[6], h = invCoeffs[7]

        let denom = g * x + h * y + 1
        let xp = (a * x + b * y + c) / denom
        let yp = (d * x + e * y + f) / denom
        return (xp, yp)
    }

    let w = outputSize.width
    let hgt = outputSize.height

    // Transform destination corners back to source
    let srcTL = transformPoint(0, 0)
    let srcTR = transformPoint(w, 0)
    let srcBR = transformPoint(w, hgt)
    let srcBL = transformPoint(0, hgt)

    // Use CIPerspectiveCorrection with these source corners
    guard let correctionFilter = CIFilter(name: "CIPerspectiveCorrection") else {
        return nil
    }

    let imageHeight = CGFloat(image.height)

    correctionFilter.setValue(ciImage, forKey: kCIInputImageKey)
    correctionFilter.setValue(CIVector(x: srcTL.0, y: imageHeight - srcTL.1), forKey: "inputTopLeft")
    correctionFilter.setValue(CIVector(x: srcTR.0, y: imageHeight - srcTR.1), forKey: "inputTopRight")
    correctionFilter.setValue(CIVector(x: srcBR.0, y: imageHeight - srcBR.1), forKey: "inputBottomRight")
    correctionFilter.setValue(CIVector(x: srcBL.0, y: imageHeight - srcBL.1), forKey: "inputBottomLeft")

    guard let outputImage = correctionFilter.outputImage else {
        return nil
    }

    // Scale to desired output size
    let scaleX = outputSize.width / outputImage.extent.width
    let scaleY = outputSize.height / outputImage.extent.height
    let scaledImage = outputImage.transformed(by: CGAffineTransform(scaleX: scaleX, y: scaleY))

    let outputRect = CGRect(origin: .zero, size: outputSize)
    return context.createCGImage(scaledImage, from: outputRect)
}

// MARK: - Save Image to PNG

/// Save a CGImage to a PNG file.
///
/// - Parameters:
///   - image: The CGImage to save
///   - url: Destination file URL
/// - Throws: ImageTransformError if saving fails
public func saveImageToPNG(_ image: CGImage, to url: URL) throws {
    let bitmapRep = NSBitmapImageRep(cgImage: image)
    guard let pngData = bitmapRep.representation(using: .png, properties: [:]) else {
        throw ImageTransformError.renderFailed("Failed to create PNG data")
    }

    try pngData.write(to: url)
}

#endif
