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
    public var foreignCount = 0
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
            // Plate agreement: does the background plate explain the pixels
            // under this feature? Tracks sit on real corners, so the test is
            // strict — the frame's 3×3 patch against the warped plate within
            // ±2 px of alignment slack (the pixel-mask tolerance of ±16 px
            // would let a 12-px bar "match" the floor beside it).
            if let plate = warpedPlate {
                let cx = Int(cur.x.rounded()), cy = Int(cur.y.rounded())
                if cx >= 3 && cy >= 3 && cx < width - 3 && cy < height - 3 {
                    var best = Int.max
                    var valid = true
                    for oy in -2...2 {
                        for ox in -2...2 {
                            var sum = 0
                            for dy in -1...1 {
                                for dx in -1...1 {
                                    let f = ((cy + dy) * width + cx + dx) * 4
                                    let g = ((cy + dy + oy) * width + cx + dx + ox) * 4
                                    if plate[g + 3] == 0 { valid = false; continue }
                                    let dr = abs(Int(rgba[f]) - Int(plate[g]))
                                    let dg = abs(Int(rgba[f + 1]) - Int(plate[g + 1]))
                                    let db = abs(Int(rgba[f + 2]) - Int(plate[g + 2]))
                                    sum += max(dr, max(dg, db))
                                }
                            }
                            best = min(best, sum)
                        }
                    }
                    if valid {
                        let agrees: Float = Double(best) / 9 < p.plateThreshold ? 1 : 0
                        t.plateAgreement += 0.2 * (agrees - t.plateAgreement)
                    }
                }
            } else if let difference {
                let idx2 = Int(cur.y.rounded()) * width + Int(cur.x.rounded())
                let agrees: Float = Double(difference[idx2]) < p.plateThreshold ? 1 : 0
                t.plateAgreement += 0.2 * (agrees - t.plateAgreement)
            }
            // Foreign streak: how long this track's plate agreement has stayed
            // below the foreign threshold. Background pixels are in the plate
            // median and agree by construction, so this only climbs on things
            // absent from it — the props.
            if t.plateAgreement < Float(p.foreignAgreement) { t.foreignStreak += 1 } else { t.foreignStreak = 0 }
            let foreignish = t.foreignStreak >= p.foreignHold && t.plateAgreement < Float(p.foreignAgreement)
            // Shadow-likeness: a shadow has no texture of its own — the
            // feature under it is the floor's, seen darker — so the frame
            // patch correlates with the plate patch at the same spot. A prop
            // replaces the background texture and does not. Run it for a foreign
            // candidate too (a static shadow would otherwise read as foreign).
            var shadowLike = false
            if (windowed > Float(p.moveTolerance) || foreignish), let plate = warpedPlate, plate[idx * 4 + 3] != 0 {
                let cx = Int(cur.x.rounded()), cy = Int(cur.y.rounded())
                if cx >= 5 && cy >= 5 && cx < width - 5 && cy < height - 5 {
                    var fs: [Float] = [], ps: [Float] = []
                    fs.reserveCapacity(121); ps.reserveCapacity(121)
                    var darker = 0
                    var valid = true
                    for dy in -5...5 {
                        for dx in -5...5 {
                            let o = ((cy + dy) * width + cx + dx) * 4
                            if plate[o + 3] == 0 { valid = false; break }
                            let f = Float(Engine.rec601Gray(Int(rgba[o]), Int(rgba[o + 1]), Int(rgba[o + 2])))
                            let g = Float(Engine.rec601Gray(Int(plate[o]), Int(plate[o + 1]), Int(plate[o + 2])))
                            fs.append(f); ps.append(g)
                            if f < g { darker += 1 }
                        }
                        if !valid { break }
                    }
                    if valid {
                        let n = Float(fs.count)
                        let mf = fs.reduce(0, +) / n, mp = ps.reduce(0, +) / n
                        var cov: Float = 0, vf: Float = 0, vp: Float = 0
                        for k in 0..<fs.count {
                            let a = fs[k] - mf, b = ps[k] - mp
                            cov += a * b; vf += a * a; vp += b * b
                        }
                        let ncc = cov / max(1e-3, (vf * vp).squareRoot())
                        // Correlated with the background and mostly darker than it.
                        shadowLike = ncc > 0.75 && darker * 3 >= fs.count * 2 && vp > 30 * n
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
            } else if windowed > Float(p.moveTolerance) && t.plateAgreement < 0.6 {
                // Moving needs both cues: displacement against the background
                // consensus, and the plate not explaining the pixel (a static
                // corner drifting against a similarity-only camera model does
                // not qualify). Provisional attachment (track level): recently
                // in contact with the subject; object-level attachment refines.
                let d = Float(distance[idx]) / 3
                candidate = shadowLike ? .shadowLike : (d < Float(p.contactRadius) ? .attached : .moving)
            } else if wasMoving && t.plateAgreement < 0.3 {
                // Paused prop: still differs from the plate, keep its label.
                candidate = t.label
            } else if foreignish && !shadowLike && !wasMoving {
                // Foreign: static, but plainly not the background and not a
                // shadow. Lets an object form from a held-still prop without any
                // motion cue; the background never reaches here (it agrees with
                // the plate it was built from). Only a promotion from
                // background/unknown — a moving or attached track (a prop
                // disagrees with the plate, so its foreign streak climbs too) is
                // never demoted to foreign, which would pull it out of its
                // object when it pauses.
                candidate = .foreign
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
            case .foreign: stats.foreignCount += 1
            case .unknown: break
            }
        }
        stats.staticPlateAgreement = staticAgreeCount > 0 ? staticAgree / Float(staticAgreeCount) : 0
        lastStats = stats
    }
}
