import Foundation
import simd

/// 2D similarity transform (uniform scale, rotation, translation).
public struct Similarity: Equatable, Codable {
    public var scale: Float
    public var angle: Float
    public var tx: Float
    public var ty: Float

    public init(scale: Float = 1, angle: Float = 0, tx: Float = 0, ty: Float = 0) {
        self.scale = scale
        self.angle = angle
        self.tx = tx
        self.ty = ty
    }

    public static let identity = Similarity()

    @inline(__always)
    public func apply(_ p: SIMD2<Float>) -> SIMD2<Float> {
        let c = cos(angle) * scale, s = sin(angle) * scale
        return SIMD2<Float>(c * p.x - s * p.y + tx, s * p.x + c * p.y + ty)
    }

    public var inverse: Similarity {
        let inv = 1 / max(1e-6, scale)
        let c = cos(-angle) * inv, s = sin(-angle) * inv
        return Similarity(scale: inv, angle: -angle, tx: -(c * tx - s * ty), ty: -(s * tx + c * ty))
    }

    /// `a * b` applies `b` first, then `a`.
    public static func * (a: Similarity, b: Similarity) -> Similarity {
        let t = a.apply(SIMD2<Float>(b.tx, b.ty))
        return Similarity(scale: a.scale * b.scale, angle: a.angle + b.angle, tx: t.x, ty: t.y)
    }

    public var translationMagnitude: Float { (tx * tx + ty * ty).squareRoot() }

    /// Weighted least-squares similarity `to ≈ S(from)` (closed form via the
    /// complex-number formulation). Needs ≥ 2 points.
    public static func fit(from: [SIMD2<Float>], to: [SIMD2<Float>], weights: [Float]? = nil) -> Similarity? {
        let n = min(from.count, to.count)
        guard n >= 2 else { return nil }
        var wsum: Float = 0
        var mf = SIMD2<Float>(0, 0), mt = SIMD2<Float>(0, 0)
        for i in 0..<n {
            let w = weights?[i] ?? 1
            wsum += w
            mf += from[i] * w
            mt += to[i] * w
        }
        guard wsum > 0 else { return nil }
        mf /= wsum
        mt /= wsum
        var re: Float = 0, im: Float = 0, den: Float = 0
        for i in 0..<n {
            let w = weights?[i] ?? 1
            let f = from[i] - mf, t = to[i] - mt
            // (t)(conj f)
            re += w * (t.x * f.x + t.y * f.y)
            im += w * (t.y * f.x - t.x * f.y)
            den += w * (f.x * f.x + f.y * f.y)
        }
        guard den > 1e-6 else { return nil }
        let a = re / den, b = im / den
        let scale = (a * a + b * b).squareRoot()
        let angle = atan2(b, a)
        let c = cos(angle) * scale, s = sin(angle) * scale
        let tx = mt.x - (c * mf.x - s * mf.y)
        let ty = mt.y - (s * mf.x + c * mf.y)
        return Similarity(scale: scale, angle: angle, tx: tx, ty: ty)
    }

    /// Deterministic RANSAC (2-point hypotheses over strided pairs) followed by
    /// two rounds of Tukey-weighted least squares. Returns the transform, the
    /// inlier flags, and the inlier RMS residual.
    public static func fitRobust(
        from: [SIMD2<Float>], to: [SIMD2<Float>], threshold: Float
    ) -> (transform: Similarity, inliers: [Bool], rms: Float)? {
        let n = min(from.count, to.count)
        guard n >= 2 else { return nil }
        var bestCount = -1
        var best: Similarity?
        let hypotheses = min(40, max(1, n))
        let step = max(1, n / hypotheses)
        var i = 0
        while i < n {
            let j = (i + n / 2) % n
            if j != i, let candidate = fit(from: [from[i], from[j]], to: [to[i], to[j]]) {
                var count = 0
                for k in 0..<n where simd_distance(candidate.apply(from[k]), to[k]) < threshold { count += 1 }
                if count > bestCount {
                    bestCount = count
                    best = candidate
                }
            }
            i += step
        }
        guard var current = best ?? fit(from: from, to: to) else { return nil }
        var inliers = [Bool](repeating: false, count: n)
        var rms: Float = 0
        for _ in 0..<2 {
            var weights = [Float](repeating: 0, count: n)
            let c = 1.5 * threshold
            for k in 0..<n {
                let r = simd_distance(current.apply(from[k]), to[k])
                let u = r / c
                weights[k] = u < 1 ? (1 - u * u) * (1 - u * u) : 0
            }
            guard let refined = fit(from: from, to: to, weights: weights) else { break }
            current = refined
        }
        var sum: Float = 0
        var count = 0
        for k in 0..<n {
            let r = simd_distance(current.apply(from[k]), to[k])
            inliers[k] = r < threshold
            if inliers[k] { sum += r * r; count += 1 }
        }
        rms = count > 0 ? (sum / Float(count)).squareRoot() : 0
        return (current, inliers, rms)
    }
}

/// 16×16 rg-chromaticity histogram; an object's learned colour signature.
public struct ChromaHistogram: Equatable {
    public static let bins = 16
    public var counts: [Float] = [Float](repeating: 0, count: bins * bins)
    public var total: Float = 0

    public init() {}

    @inline(__always)
    public static func bin(r: Int, g: Int, b: Int) -> Int {
        let sum = max(1, r + g + b)
        let rb = min(bins - 1, r * bins / sum)
        let gb = min(bins - 1, g * bins / sum)
        return gb * bins + rb
    }

    public mutating func add(rgba: [UInt8], indices: [Int]) {
        for index in indices {
            let o = index * 4
            counts[ChromaHistogram.bin(r: Int(rgba[o]), g: Int(rgba[o + 1]), b: Int(rgba[o + 2]))] += 1
            total += 1
        }
    }

    public mutating func add(rgba: [UInt8], mask: [UInt8]) {
        for index in 0..<mask.count where mask[index] != 0 {
            let o = index * 4
            counts[ChromaHistogram.bin(r: Int(rgba[o]), g: Int(rgba[o + 1]), b: Int(rgba[o + 2]))] += 1
            total += 1
        }
    }

    /// Exponential blend toward `other`.
    public mutating func blend(_ other: ChromaHistogram, alpha: Float) {
        guard other.total > 0 else { return }
        for i in counts.indices { counts[i] += alpha * (other.counts[i] / other.total * max(1, total) - counts[i]) }
        if total == 0 { total = other.total }
    }

    public var normalized: [Float] {
        guard total > 0 else { return counts }
        let m = counts.max() ?? 1
        return counts.map { $0 / max(1e-6, m) }
    }

    /// Bhattacharyya distance in [0, 1].
    public func distance(to other: ChromaHistogram) -> Float {
        guard total > 0, other.total > 0 else { return 1 }
        var bc: Float = 0
        for i in counts.indices { bc += ((counts[i] / total) * (other.counts[i] / other.total)).squareRoot() }
        return max(0, 1 - bc).squareRoot()
    }
}

/// Per-object, per-frame residual report.
public struct ObjectReport: Codable {
    public var id: Int
    public var kind: String
    public var status: String
    public var liveTracks: Int
    public var lostTracks: Int
    public var geomResidual: Double?
    public var rigidity: Double?
    public var inlierFrac: Double?
    public var photoResidual: Double?
    public var colorDrift: Double?
    public var labelFlips: Int
    public var area: Int
    public var areaDelta: Double?
    public var visible: Int
    public var attachScore: Double
    public var attachPath: String
    public var contactFrac: Double
    public var comotion: Double
    public var scale: Double
    public var angle: Double
    public var tx: Double
    public var ty: Double
}
