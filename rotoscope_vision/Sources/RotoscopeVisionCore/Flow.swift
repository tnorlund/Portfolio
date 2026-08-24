import CoreVideo
import Foundation
import Vision

/// Dense optical flow between consecutive frames via Vision, exposed as a
/// backward map: for every pixel of the current frame, where it was in the
/// previous frame. Used to warp last frame's probabilities, masks, and marker
/// positions into the current frame, and to measure temporal stability.
///
/// Vision's flow is reported in its own (bottom-left, per-revision) sign
/// convention; rather than trust a doc, the first frame pair calibrates the x
/// and y signs by picking the combination that best warps the previous
/// person mask onto the current one.
public final class OpticalFlow {
    public let width: Int
    public let height: Int
    public var accuracy: VNGenerateOpticalFlowRequest.ComputationAccuracy
    private var previous: CVPixelBuffer?
    private var signX: Float = 1
    private var signY: Float = 1
    private var calibrated = false
    /// Backward flow for the current frame: dx, dy per pixel (frame → previous).
    public private(set) var dx: [Float] = []
    public private(set) var dy: [Float] = []
    public private(set) var available = false
    public private(set) var lastCalibrationIoU: Double = 0

    public init(width: Int, height: Int, accuracy: String) {
        self.width = width
        self.height = height
        switch accuracy {
        case "low": self.accuracy = .low
        case "high": self.accuracy = .high
        case "veryHigh": self.accuracy = .veryHigh
        default: self.accuracy = .medium
        }
    }

    /// Computes flow from the previous frame to `current`; the first call only
    /// stores the frame. `previousMask`/`currentMask` (0–255 person masks) are
    /// used once to calibrate the sign convention.
    public func update(current: CVPixelBuffer, previousMask: [UInt8]?, currentMask: [UInt8]?) throws {
        defer { previous = current }
        guard let previous else {
            available = false
            return
        }
        // Reference = current, target = previous, so the field maps current
        // pixels back to where they came from.
        let request = VNGenerateOpticalFlowRequest(targetedCVPixelBuffer: previous, options: [:])
        request.computationAccuracy = accuracy
        request.outputPixelFormat = kCVPixelFormatType_TwoComponent32Float
        let handler = VNImageRequestHandler(cvPixelBuffer: current, orientation: .up, options: [:])
        try handler.perform([request])
        guard let observation = request.results?.first else {
            available = false
            return
        }
        read(observation.pixelBuffer)
        available = true
        if !calibrated, let previousMask, let currentMask {
            calibrate(previousMask: previousMask, currentMask: currentMask)
        }
    }

    private func read(_ buffer: CVPixelBuffer) {
        CVPixelBufferLockBaseAddress(buffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        let bw = CVPixelBufferGetWidth(buffer)
        let bh = CVPixelBufferGetHeight(buffer)
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { return }
        let count = width * height
        if dx.count != count {
            dx = [Float](repeating: 0, count: count)
            dy = [Float](repeating: 0, count: count)
        }
        let scaleX = Float(width) / Float(bw)
        let scaleY = Float(height) / Float(bh)
        for y in 0..<height {
            let sy = min(bh - 1, Int(Float(y) / scaleY))
            let row = base.advanced(by: sy * stride).assumingMemoryBound(to: Float.self)
            for x in 0..<width {
                let sx = min(bw - 1, Int(Float(x) / scaleX))
                dx[y * width + x] = row[sx * 2] * scaleX
                dy[y * width + x] = row[sx * 2 + 1] * scaleY
            }
        }
    }

    private func calibrate(previousMask: [UInt8], currentMask: [UInt8]) {
        var best = -1.0
        var bestSigns: (Float, Float) = (1, 1)
        for sx in [Float(1), -1] {
            for sy in [Float(1), -1] {
                signX = sx
                signY = sy
                let warped = warp(previousMask, fill: 0)
                let iou = OpticalFlow.iou(warped, currentMask)
                if iou > best {
                    best = iou
                    bestSigns = (sx, sy)
                }
            }
        }
        signX = bestSigns.0
        signY = bestSigns.1
        lastCalibrationIoU = best
        calibrated = true
    }

    /// Warps an 8-bit field from the previous frame into the current one
    /// (nearest neighbor; `fill` where the source falls outside the frame).
    public func warp(_ field: [UInt8], fill: UInt8) -> [UInt8] {
        guard available else { return field }
        var out = [UInt8](repeating: fill, count: width * height)
        for y in 0..<height {
            for x in 0..<width {
                let index = y * width + x
                let sx = Int((Float(x) + signX * dx[index]).rounded())
                let sy = Int((Float(y) + signY * dy[index]).rounded())
                if sx >= 0 && sx < width && sy >= 0 && sy < height {
                    out[index] = field[sy * width + sx]
                }
            }
        }
        return out
    }

    /// Float-field variant (probabilities).
    public func warp(_ field: [Float], fill: Float) -> [Float] {
        guard available else { return field }
        var out = [Float](repeating: fill, count: width * height)
        for y in 0..<height {
            for x in 0..<width {
                let index = y * width + x
                let sx = Int((Float(x) + signX * dx[index]).rounded())
                let sy = Int((Float(y) + signY * dy[index]).rounded())
                if sx >= 0 && sx < width && sy >= 0 && sy < height {
                    out[index] = field[sy * width + sx]
                }
            }
        }
        return out
    }

    /// Moves previous-frame pixel indices forward into the current frame by
    /// inverting the backward map locally (flow at the destination is used as
    /// an estimate, which is accurate for small motion).
    public func advance(indices: [Int]) -> [Int] {
        guard available else { return indices }
        var out: [Int] = []
        out.reserveCapacity(indices.count)
        for index in indices {
            let y = index / width
            let x = index - y * width
            // Start from the previous position, apply the negated backward flow
            // sampled there, then re-sample at the guess to refine once.
            var gx = Float(x) - signX * dx[index]
            var gy = Float(y) - signY * dy[index]
            let rx = Int(gx.rounded()), ry = Int(gy.rounded())
            if rx >= 0 && rx < width && ry >= 0 && ry < height {
                let at = ry * width + rx
                gx = Float(x) - signX * dx[at]
                gy = Float(y) - signY * dy[at]
            }
            let fx = Int(gx.rounded()), fy = Int(gy.rounded())
            if fx >= 0 && fx < width && fy >= 0 && fy < height {
                out.append(fy * width + fx)
            }
        }
        return out
    }

    /// Backward flow vector at a sub-pixel position (bilinear, sign applied).
    public func backwardVector(atX x: Float, y: Float) -> SIMD2<Float> {
        guard available else { return .zero }
        let cx = max(0, min(Float(width - 1), x)), cy = max(0, min(Float(height - 1), y))
        let x0 = Int(cx.rounded(.down)), y0 = Int(cy.rounded(.down))
        let x1 = min(width - 1, x0 + 1), y1 = min(height - 1, y0 + 1)
        let fx = cx - Float(x0), fy = cy - Float(y0)
        func at(_ xx: Int, _ yy: Int) -> SIMD2<Float> {
            let i = yy * width + xx
            return SIMD2<Float>(signX * dx[i], signY * dy[i])
        }
        let top = at(x0, y0) * (1 - fx) + at(x1, y0) * fx
        let bottom = at(x0, y1) * (1 - fx) + at(x1, y1) * fx
        return top * (1 - fy) + bottom * fy
    }

    /// Maps a previous-frame point to this frame by locally inverting the
    /// backward field (two fixed-point iterations; exact for small motion).
    public func forward(_ p: SIMD2<Float>) -> SIMD2<Float> {
        guard available else { return p }
        var q = p - backwardVector(atX: p.x, y: p.y)
        q = p - backwardVector(atX: q.x, y: q.y)
        return q
    }

    /// Mean flow magnitude inside a mask (motion proxy for metrics).
    public func meanMagnitude(in mask: [UInt8]) -> Double {
        guard available else { return 0 }
        var sum = 0.0
        var n = 0
        for index in 0..<(width * height) where mask[index] != 0 {
            sum += Double((dx[index] * dx[index] + dy[index] * dy[index]).squareRoot())
            n += 1
        }
        return n > 0 ? sum / Double(n) : 0
    }

    public static func iou(_ a: [UInt8], _ b: [UInt8]) -> Double {
        var inter = 0
        var union = 0
        for index in 0..<a.count {
            let x = a[index] > 127
            let y = b[index] > 127
            if x && y { inter += 1 }
            if x || y { union += 1 }
        }
        return union > 0 ? Double(inter) / Double(union) : 1
    }
}
