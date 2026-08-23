import CoreImage
import CoreImage.CIFilterBuiltins
import CoreVideo
import Foundation
import simd
import Vision

/// Aligns frames of a handheld (nearly static) shot to a reference frame with
/// `VNHomographicImageRegistrationRequest`, so the background plate can be a
/// sharp median and the per-frame difference does not light up every static
/// edge. The subject is blanked to flat gray before registering so the person
/// moving in front of the camera does not pull the estimate.
public final class FrameRegistrar {
    public let width: Int
    public let height: Int
    private let context = CIContext(options: [.cacheIntermediates: false])
    private var referenceBuffer: CVPixelBuffer?
    private var lastHomography = matrix_identity_float3x3

    public init(width: Int, height: Int) {
        self.width = width
        self.height = height
    }

    public var hasReference: Bool { referenceBuffer != nil }

    /// Largest frame-to-frame jump (px) a handheld-but-still shot is allowed;
    /// anything bigger is treated as a failed registration.
    public var maxJump: Float = 40
    /// Largest scale deviation from 1 and translation (fraction of the frame)
    /// an accepted homography may have.
    public var maxScaleDeviation: Float = 0.06
    public var maxTranslationFraction: Float = 0.15

    /// The reference frame's pixels (subject flattened), used to fill blanked
    /// regions of later frames when no plate is available yet.
    public private(set) var referencePixels: [UInt8]?
    /// Height of the top strip used for the coarse, subject-free translation
    /// estimate (the ceiling in a full-length shot). 0 disables the stage.
    public var stripHeight = 120
    private var referenceStrip: CVPixelBuffer?

    /// Sets the reference frame. `blank` (binary) marks pixels to flatten.
    public func setReference(rgba: [UInt8], blank: [UInt8]?) throws {
        let pixels = blanked(rgba, blank: blank, fill: nil)
        referencePixels = pixels
        referenceBuffer = try makeBuffer(rgba: pixels)
        referenceStrip = stripHeight > 0 ? try makeStrip(rgba: pixels) : nil
        lastHomography = matrix_identity_float3x3
    }

    /// Coarse frame → reference translation from the top strip alone, which
    /// the subject never enters, so it carries no bias from blanking. Nil when
    /// the strip is disabled or Vision finds no alignment.
    public func coarseTranslation(rgba: [UInt8], blank: [UInt8]?) throws -> simd_float3x3? {
        guard let referenceStrip else { return nil }
        let strip = try makeStrip(rgba: blanked(rgba, blank: blank, fill: nil))
        let request = VNTranslationalImageRegistrationRequest(targetedCVPixelBuffer: strip, options: [:])
        let handler = VNImageRequestHandler(cvPixelBuffer: referenceStrip, options: [:])
        try handler.perform([request])
        guard let observation = request.results?.first as? VNImageTranslationAlignmentObservation else {
            return nil
        }
        let tx = Float(observation.alignmentTransform.tx)
        let ty = Float(observation.alignmentTransform.ty)
        guard tx.isFinite, ty.isFinite,
            abs(tx) < maxTranslationFraction * Float(width), abs(ty) < maxTranslationFraction * Float(height)
        else { return nil }
        var matrix = matrix_identity_float3x3
        matrix.columns.2 = SIMD3<Float>(tx, ty, 1)
        return matrix
    }

    /// Top `stripHeight` rows as a BGRA buffer. Vision's coordinate origin is
    /// the bottom-left, but a translation is origin-independent.
    private func makeStrip(rgba: [UInt8]) throws -> CVPixelBuffer {
        let rows = min(stripHeight, height)
        var buffer: CVPixelBuffer?
        let status = CVPixelBufferCreate(
            kCFAllocatorDefault, width, rows, kCVPixelFormatType_32BGRA,
            [kCVPixelBufferIOSurfacePropertiesKey: [:]] as CFDictionary, &buffer)
        guard status == kCVReturnSuccess, let buffer else {
            throw RotoscopeVisionError.pixelBuffer("could not allocate strip buffer")
        }
        CVPixelBufferLockBaseAddress(buffer, [])
        defer { CVPixelBufferUnlockBaseAddress(buffer, []) }
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { return buffer }
        for y in 0..<rows {
            let row = base.advanced(by: y * stride).assumingMemoryBound(to: UInt8.self)
            var src = y * width * 4
            var dst = 0
            for _ in 0..<width {
                row[dst] = rgba[src + 2]
                row[dst + 1] = rgba[src + 1]
                row[dst + 2] = rgba[src]
                row[dst + 3] = 255
                src += 4
                dst += 4
            }
        }
        return buffer
    }

    /// Homography mapping this frame's pixel coordinates into reference
    /// coordinates (Vision's "floating → reference" warp). `accepted` is false
    /// when Vision's estimate failed the plausibility gate, in which case the
    /// last accepted homography is returned instead.
    ///
    /// `fill` (RGBA, same size) replaces the blanked pixels instead of flat
    /// gray — pass the plate warped into this frame (or the reference frame)
    /// so the hole looks like static background rather than a moving edge.
    public func homography(
        rgba: [UInt8], blank: [UInt8]?, fill: [UInt8]? = nil
    ) throws -> (matrix: simd_float3x3, accepted: Bool) {
        guard let referenceBuffer else { return (matrix_identity_float3x3, true) }
        let floating = try makeBuffer(rgba: blanked(rgba, blank: blank, fill: fill))
        let request = VNHomographicImageRegistrationRequest(targetedCVPixelBuffer: floating, options: [:])
        let handler = VNImageRequestHandler(cvPixelBuffer: referenceBuffer, options: [:])
        try handler.perform([request])
        guard let observation = request.results?.first as? VNImageHomographicAlignmentObservation else {
            return (lastHomography, false)
        }
        var warp = observation.warpTransform
        let scale = warp.columns.2.z
        guard scale.isFinite, abs(scale) > 1e-6, warp.determinant.isFinite else { return (lastHomography, false) }
        warp = warp * (1 / scale)
        let sx = warp.columns.0.x
        let sy = warp.columns.1.y
        let shearX = warp.columns.1.x
        let shearY = warp.columns.0.y
        let tx = warp.columns.2.x
        let ty = warp.columns.2.y
        let px = warp.columns.0.z
        let py = warp.columns.1.z
        let plausible =
            abs(sx - 1) < maxScaleDeviation && abs(sy - 1) < maxScaleDeviation
            && abs(shearX) < maxScaleDeviation && abs(shearY) < maxScaleDeviation
            && abs(tx) < maxTranslationFraction * Float(width) && abs(ty) < maxTranslationFraction * Float(height)
            && abs(px) < 1e-4 && abs(py) < 1e-4
        // Continuity is judged against this frame's own coarse estimate when
        // there is one (the last accepted homography goes stale across a
        // rejection streak and would keep rejecting forever).
        let anchor = jumpAnchor ?? lastHomography
        let jump = max(abs(tx - anchor.columns.2.x), abs(ty - anchor.columns.2.y))
        guard plausible && jump <= maxJump else { return (lastHomography, false) }
        lastHomography = warp
        return (warp, true)
    }

    /// Optional per-frame anchor for the jump test (e.g. the coarse strip
    /// translation); cleared after each `homography` call.
    public var jumpAnchor: simd_float3x3? {
        didSet { if jumpAnchor != nil { anchorSet = true } }
    }
    private var anchorSet = false

    /// Forget the frame-to-frame continuity (e.g. when sampling sparsely).
    public func resetContinuity() {
        lastHomography = matrix_identity_float3x3
    }

    /// Warps RGBA pixels by a homography into a same-sized canvas; pixels the
    /// source does not cover come back with alpha 0.
    public func warp(rgba: [UInt8], by homography: simd_float3x3) throws -> [UInt8] {
        let source = try makeBuffer(rgba: rgba)
        let image = CIImage(cvPixelBuffer: source)
        let corners = [
            SIMD2<Float>(0, 0), SIMD2<Float>(Float(width), 0),
            SIMD2<Float>(Float(width), Float(height)), SIMD2<Float>(0, Float(height)),
        ].map { point -> CGPoint in
            let mapped = homography * SIMD3<Float>(point.x, point.y, 1)
            let w = abs(mapped.z) > 1e-9 ? mapped.z : 1e-9
            return CGPoint(x: CGFloat(mapped.x / w), y: CGFloat(mapped.y / w))
        }
        let filter = CIFilter.perspectiveTransform()
        filter.inputImage = image
        filter.bottomLeft = corners[0]
        filter.bottomRight = corners[1]
        filter.topRight = corners[2]
        filter.topLeft = corners[3]
        guard let output = filter.outputImage else {
            throw RotoscopeVisionError.pixelBuffer("perspective transform failed")
        }
        let target = try makeBuffer(rgba: nil)
        context.render(
            output, to: target, bounds: CGRect(x: 0, y: 0, width: width, height: height),
            colorSpace: CGColorSpace(name: CGColorSpace.sRGB))
        return rgbaBytes(from: target)
    }

    private func blanked(_ rgba: [UInt8], blank: [UInt8]?, fill: [UInt8]?) -> [UInt8] {
        guard let blank else { return rgba }
        var out = rgba
        for index in 0..<(width * height) where blank[index] != 0 {
            let offset = index * 4
            if let fill, fill[offset + 3] != 0 {
                out[offset] = fill[offset]
                out[offset + 1] = fill[offset + 1]
                out[offset + 2] = fill[offset + 2]
            } else {
                out[offset] = 128
                out[offset + 1] = 128
                out[offset + 2] = 128
            }
        }
        return out
    }

    /// BGRA pixel buffer from RGBA bytes (nil = cleared to transparent black).
    private func makeBuffer(rgba: [UInt8]?) throws -> CVPixelBuffer {
        var buffer: CVPixelBuffer?
        let status = CVPixelBufferCreate(
            kCFAllocatorDefault, width, height, kCVPixelFormatType_32BGRA,
            [kCVPixelBufferIOSurfacePropertiesKey: [:]] as CFDictionary, &buffer)
        guard status == kCVReturnSuccess, let buffer else {
            throw RotoscopeVisionError.pixelBuffer("could not allocate registration buffer")
        }
        CVPixelBufferLockBaseAddress(buffer, [])
        defer { CVPixelBufferUnlockBaseAddress(buffer, []) }
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { return buffer }
        if let rgba {
            for y in 0..<height {
                let row = base.advanced(by: y * stride).assumingMemoryBound(to: UInt8.self)
                var src = y * width * 4
                var dst = 0
                for _ in 0..<width {
                    row[dst] = rgba[src + 2]
                    row[dst + 1] = rgba[src + 1]
                    row[dst + 2] = rgba[src]
                    row[dst + 3] = rgba[src + 3]
                    src += 4
                    dst += 4
                }
            }
        } else {
            memset(base, 0, stride * height)
        }
        return buffer
    }
}
