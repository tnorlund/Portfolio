import CoreImage
import CoreVideo
import Foundation
import Vision

/// How the subject is lifted off the background.
public enum SubjectMode: String, CaseIterable {
    /// `VNGeneratePersonInstanceMaskRequest`, keeping the largest person. Best
    /// when other people are in frame.
    case person
    /// `VNGenerateForegroundInstanceMaskRequest` (subject lift), keeping the
    /// largest instance. Can include props the subject is holding.
    case foreground
    /// `VNGeneratePersonSegmentationRequest`: every person in frame.
    case people
    /// No segmentation; everything is body tier and nothing is removed.
    case none
}

public struct FaceEllipse {
    /// Normalized to the frame (0–1), y down.
    public var centerX: Double
    public var centerY: Double
    public var radiusX: Double
    public var radiusY: Double
}

public struct FrameFocus {
    /// Soft subject mask, 0–255, one byte per pixel.
    public var mask: [UInt8]
    public var face: FaceEllipse?
    /// Per-pixel `FocusTier` raw value.
    public var tiers: [UInt8]
    public var subjectPixels: Int
}

/// Runs the Vision requests for one frame and turns them into the engine's
/// per-pixel focus tiers: the periocular ellipse around the detected eyes is
/// `face`, the rest of the subject mask is `body`, everything else is
/// `background`.
public final class FocusAnalyzer {
    public let width: Int
    public let height: Int
    public let mode: SubjectMode
    /// Periocular ellipse radii as multiples of the inter-eye distance. The
    /// homepage portrait ellipse (radii 0.085×960 by 0.055×720 for eyes 85 px
    /// apart) works out to roughly 0.95 × and 0.46 ×.
    public var eyeRadiusX: Double = 0.95
    public var eyeRadiusY: Double = 0.46
    public var maskThreshold: UInt8 = 127

    private let context = CIContext(options: [.cacheIntermediates: false])
    private var maskTarget: CVPixelBuffer?

    public init(width: Int, height: Int, mode: SubjectMode) {
        self.width = width
        self.height = height
        self.mode = mode
    }

    public func analyze(_ pixelBuffer: CVPixelBuffer) throws -> FrameFocus {
        let count = width * height
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: .up, options: [:])

        var mask: [UInt8]
        switch mode {
        case .none:
            mask = [UInt8](repeating: 255, count: count)
        case .people:
            let request = VNGeneratePersonSegmentationRequest()
            request.qualityLevel = .accurate
            request.outputPixelFormat = kCVPixelFormatType_OneComponent8
            try handler.perform([request])
            if let result = request.results?.first {
                mask = try maskBytes(from: result.pixelBuffer)
            } else {
                mask = [UInt8](repeating: 0, count: count)
            }
        case .person, .foreground:
            let request: VNImageBasedRequest =
                mode == .person
                ? VNGeneratePersonInstanceMaskRequest()
                : VNGenerateForegroundInstanceMaskRequest()
            try handler.perform([request])
            if let observation = request.results?.first as? VNInstanceMaskObservation,
                let best = try largestInstance(in: observation)
            {
                let scaled = try observation.generateScaledMaskForImage(
                    forInstances: IndexSet(integer: best), from: handler)
                mask = try maskBytes(from: scaled)
            } else {
                mask = [UInt8](repeating: 0, count: count)
            }
        }

        let faceRequest = VNDetectFaceLandmarksRequest()
        try handler.perform([faceRequest])
        let face = bestFace(from: faceRequest.results ?? [], mask: mask)

        var tiers = [UInt8](repeating: FocusTier.background.rawValue, count: count)
        var subjectPixels = 0
        let threshold = maskThreshold
        for y in 0..<height {
            let normalizedY = (Double(y) + 0.5) / Double(height)
            for x in 0..<width {
                let index = y * width + x
                if mask[index] <= threshold { continue }
                subjectPixels += 1
                var tier = FocusTier.body
                if let face {
                    let normalizedX = (Double(x) + 0.5) / Double(width)
                    let fx = (normalizedX - face.centerX) / max(0.0001, face.radiusX)
                    let fy = (normalizedY - face.centerY) / max(0.0001, face.radiusY)
                    if fx * fx + fy * fy <= 1 { tier = .face }
                }
                tiers[index] = tier.rawValue
            }
        }
        return FrameFocus(mask: mask, face: face, tiers: tiers, subjectPixels: subjectPixels)
    }

    private func largestInstance(in observation: VNInstanceMaskObservation) throws -> Int? {
        var best: Int?
        var bestArea = -1
        for instance in observation.allInstances {
            let buffer = try observation.generateMask(forInstances: IndexSet(integer: instance))
            let area = coverage(of: buffer)
            if area > bestArea {
                bestArea = area
                best = instance
            }
        }
        return best
    }

    /// Number of mask samples above half intensity, used only to rank instances.
    private func coverage(of buffer: CVPixelBuffer) -> Int {
        CVPixelBufferLockBaseAddress(buffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { return 0 }
        let w = CVPixelBufferGetWidth(buffer)
        let h = CVPixelBufferGetHeight(buffer)
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        let format = CVPixelBufferGetPixelFormatType(buffer)
        var total = 0
        for y in 0..<h {
            let row = base.advanced(by: y * stride)
            if format == kCVPixelFormatType_OneComponent32Float {
                let floats = row.assumingMemoryBound(to: Float.self)
                for x in 0..<w where floats[x] > 0.5 { total += 1 }
            } else {
                let bytes = row.assumingMemoryBound(to: UInt8.self)
                for x in 0..<w where bytes[x] > 127 { total += 1 }
            }
        }
        return total
    }

    /// Reads a Vision mask at any size/format as frame-sized 8-bit bytes.
    private func maskBytes(from buffer: CVPixelBuffer) throws -> [UInt8] {
        let bw = CVPixelBufferGetWidth(buffer)
        let bh = CVPixelBufferGetHeight(buffer)
        let format = CVPixelBufferGetPixelFormatType(buffer)
        if bw == width && bh == height
            && (format == kCVPixelFormatType_OneComponent8 || format == kCVPixelFormatType_OneComponent32Float)
        {
            return readGray(buffer)
        }
        // Resample through Core Image into an 8-bit frame-sized buffer.
        if maskTarget == nil {
            var target: CVPixelBuffer?
            let status = CVPixelBufferCreate(
                kCFAllocatorDefault, width, height, kCVPixelFormatType_OneComponent8,
                [kCVPixelBufferIOSurfacePropertiesKey: [:]] as CFDictionary, &target)
            guard status == kCVReturnSuccess, let target else {
                throw RotoscopeVisionError.pixelBuffer("could not allocate mask buffer")
            }
            maskTarget = target
        }
        let target = maskTarget!
        let image = CIImage(cvPixelBuffer: buffer).transformed(
            by: CGAffineTransform(scaleX: CGFloat(width) / CGFloat(bw), y: CGFloat(height) / CGFloat(bh)))
        context.render(
            image, to: target, bounds: CGRect(x: 0, y: 0, width: width, height: height), colorSpace: nil)
        return readGray(target)
    }

    private func readGray(_ buffer: CVPixelBuffer) -> [UInt8] {
        CVPixelBufferLockBaseAddress(buffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        var output = [UInt8](repeating: 0, count: width * height)
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { return output }
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        let format = CVPixelBufferGetPixelFormatType(buffer)
        for y in 0..<height {
            let row = base.advanced(by: y * stride)
            if format == kCVPixelFormatType_OneComponent32Float {
                let floats = row.assumingMemoryBound(to: Float.self)
                for x in 0..<width {
                    output[y * width + x] = UInt8(max(0, min(255, (floats[x] * 255).rounded())))
                }
            } else {
                let bytes = row.assumingMemoryBound(to: UInt8.self)
                for x in 0..<width { output[y * width + x] = bytes[x] }
            }
        }
        return output
    }

    /// Picks the face that sits on the subject (largest such face; otherwise the
    /// largest face) and turns its eyes into a periocular ellipse.
    private func bestFace(from faces: [VNFaceObservation], mask: [UInt8]) -> FaceEllipse? {
        let size = CGSize(width: width, height: height)
        var chosen: (face: VNFaceObservation, onSubject: Bool, area: CGFloat)?
        for face in faces {
            let box = face.boundingBox
            let cx = Int(box.midX * CGFloat(width))
            let cy = Int((1 - box.midY) * CGFloat(height))
            let index = max(0, min(height - 1, cy)) * width + max(0, min(width - 1, cx))
            let onSubject = mask[index] > maskThreshold
            let area = box.width * box.height
            if let current = chosen {
                if (onSubject && !current.onSubject) || (onSubject == current.onSubject && area > current.area) {
                    chosen = (face, onSubject, area)
                }
            } else {
                chosen = (face, onSubject, area)
            }
        }
        guard let chosen, let landmarks = chosen.face.landmarks,
            let left = landmarks.leftEye, let right = landmarks.rightEye
        else { return nil }
        func center(_ region: VNFaceLandmarkRegion2D) -> CGPoint {
            let points = region.pointsInImage(imageSize: size)
            guard !points.isEmpty else { return .zero }
            let sum = points.reduce(CGPoint.zero) { CGPoint(x: $0.x + $1.x, y: $0.y + $1.y) }
            // Vision image points are bottom-left origin; flip to y-down.
            return CGPoint(x: sum.x / CGFloat(points.count), y: CGFloat(height) - sum.y / CGFloat(points.count))
        }
        let l = center(left)
        let r = center(right)
        let distance = max(4, hypot(r.x - l.x, r.y - l.y))
        return FaceEllipse(
            centerX: Double((l.x + r.x) / 2) / Double(width),
            centerY: Double((l.y + r.y) / 2) / Double(height),
            radiusX: eyeRadiusX * Double(distance) / Double(width),
            radiusY: eyeRadiusY * Double(distance) / Double(height))
    }
}

public enum RotoscopeVisionError: Error, CustomStringConvertible {
    case pixelBuffer(String)
    case video(String)

    public var description: String {
        switch self {
        case .pixelBuffer(let message), .video(let message): return message
        }
    }
}
