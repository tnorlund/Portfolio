import Foundation
import simd

public struct BackgroundFit {
    public var transform: Similarity
    public var residual: Float      // inlier RMS, px
    public var inlierFrac: Float
    public var tracksUsed: Int
}

public struct ClassifierStats {
    public var staticCount = 0
    public var subjectCount = 0
    public var otherCount = 0
    public var movingCount = 0
    public var attachedCount = 0
    public var shadowCount = 0
    public var labelFlips = 0
    public var staticPlateAgreement: Float = 0
    public var fit: BackgroundFit?
}

/// Labels tracks each frame from motion relative to the background consensus,
/// the plate difference, the person mask, and chromaticity. Nothing here knows
/// what any object is; it only knows what moves, what touches the subject,
/// and what merely got darker.
public final class TrackClassifier {
    public let width: Int
    public let height: Int
    public var params: Params
    /// Background consensus similarity per frame (frame index → transform
    /// mapping the previous frame's background to this frame's).
    public private(set) var backgroundHistory: [Similarity] = []
    public private(set) var lastStats = ClassifierStats()

    public init(width: Int, height: Int, params: Params) {
        self.width = width
        self.height = height
        self.params = params
    }

    /// Chamfer (3-4) distance to the nearest set pixel, in ~px (value / 3).
    public static func distanceTransform(_ mask: [UInt8], width: Int, height: Int) -> [UInt16] {
        let inf: UInt16 = 60000
        var d = [UInt16](repeating: inf, count: width * height)
        for i in 0..<d.count where mask[i] != 0 { d[i] = 0 }
        for y in 0..<height {
            for x in 0..<width {
                let i = y * width + x
                var best = d[i]
                if x > 0 { best = min(best, d[i - 1] &+ 3) }
                if y > 0 {
                    best = min(best, d[i - width] &+ 3)
                    if x > 0 { best = min(best, d[i - width - 1] &+ 4) }
                    if x < width - 1 { best = min(best, d[i - width + 1] &+ 4) }
                }
                d[i] = best
            }
        }
        for y in stride(from: height - 1, through: 0, by: -1) {
            for x in stride(from: width - 1, through: 0, by: -1) {
                let i = y * width + x
                var best = d[i]
                if x < width - 1 { best = min(best, d[i + 1] &+ 3) }
                if y < height - 1 {
                    best = min(best, d[i + width] &+ 3)
                    if x < width - 1 { best = min(best, d[i + width + 1] &+ 4) }
                    if x > 0 { best = min(best, d[i + width - 1] &+ 4) }
                }
                d[i] = best
            }
        }
        return d
    }

    // swiftlint:disable:next function_body_length
    public func classify(
        tracker: FeatureTracker, frame: Int, rgba: [UInt8], difference: [UInt8]?, warpedPlate: [UInt8]?,
        person: [UInt8], others: [UInt8]?, registrarPrediction: ((SIMD2<Float>) -> SIMD2<Float>)?
    ) {
        var stats = ClassifierStats()
        let p = params
        let count = width * height
        // Eroded person mask: boundary jitter must not flip labels.
        var personCore = person
        for _ in 0..<2 { personCore = Morphology.erode(personCore, width: width, height: height) }
        var personWide = person
        for _ in 0..<4 { personWide = Morphology.dilate(personWide, width: width, height: height) }
        let distance = TrackClassifier.distanceTransform(person, width: width, height: height)

        // --- background consensus similarity from non-subject tracks ---
        var from: [SIMD2<Float>] = [], to: [SIMD2<Float>] = []
        for t in tracker.tracks where t.status == .live && t.positions.count >= 2 {
            let cur = t.current
            let idx = Int(cur.y.rounded()) * width + Int(cur.x.rounded())
            if idx < 0 || idx >= count { continue }
            if personWide[idx] != 0 { continue }
            if let others, others[idx] != 0 { continue }
            from.append(t.positions[t.positions.count - 2])
            to.append(cur)
        }
        var fit: BackgroundFit?
        if from.count >= 8, let robust = Similarity.fitRobust(from: from, to: to, threshold: 3) {
            let inliers = robust.inliers.reduce(0) { $0 + ($1 ? 1 : 0) }
            fit = BackgroundFit(
                transform: robust.transform, residual: robust.rms, inlierFrac: Float(inliers) / Float(from.count),
                tracksUsed: from.count)
        }
        let background = fit?.transform ?? (backgroundHistory.last ?? .identity)
        backgroundHistory.append(background)
        stats.fit = fit

        // Composite background motion over the window (frame-n → frame).
        let window = max(2, p.motionWindow)
        var composite = Similarity.identity
        for k in 0..<min(window, backgroundHistory.count) {
            composite = backgroundHistory[backgroundHistory.count - 1 - k] * composite
        }

        var staticAgree: Float = 0
        var staticAgreeCount = 0
        tracker.updateAll { t in
            guard t.status == .live else { return }
            let cur = t.current
            let idx = Int(cur.y.rounded()) * width + Int(cur.x.rounded())
            guard idx >= 0 && idx < count else { return }
            // Per-frame residual motion vs the background.
            if t.positions.count >= 2 {
                let predicted = background.apply(t.positions[t.positions.count - 2])
                let m = simd_distance(predicted, cur)
                t.staticScore += 0.1 * ((m < Float(p.staticTolerance) ? 1 : 0) - t.staticScore)
            }
            // Windowed displacement relative to the background.
            var windowed: Float = 0
            let n = min(window, t.positions.count - 1)
            if n >= 2 {
                var comp = Similarity.identity
                for k in 0..<n { comp = backgroundHistory[backgroundHistory.count - 1 - k] * comp }
                windowed = simd_distance(comp.apply(t.positions[t.positions.count - 1 - n]), cur)
            }
            // Plate agreement: does the tolerant difference say "background" here?
            if let difference {
                var sum = 0, m = 0
                let cx = Int(cur.x.rounded()), cy = Int(cur.y.rounded())
                for dy in -2...2 {
                    let y = cy + dy
                    if y < 0 || y >= height { continue }
                    for dx in -2...2 {
                        let x = cx + dx
                        if x < 0 || x >= width { continue }
                        sum += Int(difference[y * width + x]); m += 1
                    }
                }
                let agrees: Float = m > 0 && Double(sum) / Double(m) < p.plateThreshold ? 1 : 0
                t.plateAgreement += 0.2 * (agrees - t.plateAgreement)
            }
            // Shadow-likeness: darker than the plate with the plate's chroma.
            var shadowLike = false
            if let plate = warpedPlate, plate[idx * 4 + 3] != 0 {
                let fr = Float(rgba[idx * 4]), fg = Float(rgba[idx * 4 + 1]), fb = Float(rgba[idx * 4 + 2])
                let pr = Float(plate[idx * 4]), pg = Float(plate[idx * 4 + 1]), pb = Float(plate[idx * 4 + 2])
                let fSum = fr + fg + fb, pSum = pr + pg + pb
                if pSum > 30 && fSum < pSum {
                    let ratio = fSum / pSum
                    if ratio >= Float(p.shadowMinRatio) && ratio <= Float(p.shadowMaxRatio) {
                        let frame = ChromaSignature(r: fr / max(1, fSum), g: fg / max(1, fSum), luma: 0)
                        let plateSig = ChromaSignature(r: pr / max(1, pSum), g: pg / max(1, pSum), luma: 0)
                        shadowLike = frame.distance(to: plateSig) < Float(p.chromaTolerance)
                    }
                }
            }
            // Candidate label from the cues.
            var candidate: TrackLabel
            let wasMoving = t.label == .moving || t.label == .attached
            if personCore[idx] != 0 {
                candidate = .subject
            } else if let others, others[idx] != 0 {
                candidate = .other
            } else if windowed > Float(p.moveTolerance) {
                // Moving is decided by displacement alone. Provisional
                // attachment (track level): recently in contact with the
                // subject; object-level attachment refines this.
                let d = Float(distance[idx]) / 3
                candidate = shadowLike ? .shadowLike : (d < Float(p.contactRadius) ? .attached : .moving)
            } else if wasMoving && t.plateAgreement < 0.3 {
                // Paused prop: still differs from the plate, keep its label.
                candidate = t.label
            } else if t.staticScore > 0.8 || t.plateAgreement > 0.6 {
                candidate = .background
            } else {
                candidate = t.label == .unknown ? .background : t.label
            }
            // Hysteresis: promotions are quick, demotions from attached/moving
            // to background need the hold.
            let current = t.label
            if candidate != current {
                if t.pendingLabel == candidate { t.pendingAge += 1 } else { t.pendingLabel = candidate; t.pendingAge = 1 }
                let hold: Int
                switch (current, candidate) {
                case (.attached, .background), (.moving, .background), (.attached, .moving): hold = p.labelHold
                case (.unknown, _): hold = 1
                default: hold = 3
                }
                if t.pendingAge >= hold {
                    t.labels[t.labels.count - 1] = candidate
                    t.labelAge = 0
                    t.pendingAge = 0
                    if current != .unknown { stats.labelFlips += 1 }
                } else {
                    t.labels[t.labels.count - 1] = current
                    t.labelAge += 1
                }
            } else {
                t.labelAge += 1
                t.pendingAge = 0
            }
            switch t.label {
            case .background:
                stats.staticCount += 1
                staticAgree += t.plateAgreement
                staticAgreeCount += 1
            case .subject: stats.subjectCount += 1
            case .other: stats.otherCount += 1
            case .moving: stats.movingCount += 1
            case .attached: stats.attachedCount += 1
            case .shadowLike: stats.shadowCount += 1
            case .unknown: break
            }
        }
        stats.staticPlateAgreement = staticAgreeCount > 0 ? staticAgree / Float(staticAgreeCount) : 0
        lastStats = stats
    }
}
