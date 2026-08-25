import Foundation
import simd

public enum TrackLabel: UInt8, Codable {
    case unknown = 0
    case background
    case subject
    case other
    case attached
    case shadowLike
    /// Moving, not subject, not yet assigned to an attached object.
    case moving
}

public enum TrackStatus: Equatable {
    case live
    case lost
    case retired
}

/// rg-chromaticity plus luma; distance ignores luma so it is shadow-invariant.
public struct ChromaSignature: Equatable {
    public var r: Float
    public var g: Float
    public var luma: Float

    public init(r: Float, g: Float, luma: Float) {
        self.r = r
        self.g = g
        self.luma = luma
    }

    public static func of(rgba: [UInt8], width: Int, height: Int, at p: SIMD2<Float>, radius: Int = 2) -> ChromaSignature {
        var r = 0, g = 0, b = 0, n = 0
        let cx = Int(p.x.rounded()), cy = Int(p.y.rounded())
        for dy in -radius...radius {
            let y = cy + dy
            if y < 0 || y >= height { continue }
            for dx in -radius...radius {
                let x = cx + dx
                if x < 0 || x >= width { continue }
                let o = (y * width + x) * 4
                r += Int(rgba[o]); g += Int(rgba[o + 1]); b += Int(rgba[o + 2]); n += 1
            }
        }
        let sum = max(1, r + g + b)
        return ChromaSignature(r: Float(r) / Float(sum), g: Float(g) / Float(sum), luma: Float(sum) / Float(3 * max(1, n)))
    }

    public mutating func update(_ s: ChromaSignature, alpha: Float = 0.1) {
        r += alpha * (s.r - r)
        g += alpha * (s.g - g)
        luma += alpha * (s.luma - luma)
    }

    public func distance(to other: ChromaSignature) -> Float {
        max(abs(r - other.r), abs(g - other.g))
    }
}

public struct Track {
    public let id: Int
    public let birth: Int
    /// positions[k] is the position at frame birth + k (predicted while lost).
    public var positions: [SIMD2<Float>]
    public var labels: [TrackLabel]
    public var status: TrackStatus = .live
    public var lostCount = 0
    public var template: [Float]
    public var score: Float
    public var fbError: Float = 0
    public var ssd: Float = 0
    public var refined = true
    public var chroma: ChromaSignature
    public var staticScore: Float = 0.5
    public var plateAgreement: Float = 0.5
    public var objectID: Int?
    /// Consecutive frames this track disagreed with its object's transform.
    public var outlierStreak = 0
    public var canonical: SIMD2<Float>?
    /// Frames the current label has been held (for hysteresis).
    public var labelAge = 0
    public var pendingLabel: TrackLabel = .unknown
    public var pendingAge = 0

    public var current: SIMD2<Float> { positions[positions.count - 1] }
    public var age: Int { positions.count }

    public func position(at frame: Int) -> SIMD2<Float>? {
        let k = frame - birth
        return k >= 0 && k < positions.count ? positions[k] : nil
    }

    public func label(at frame: Int) -> TrackLabel? {
        let k = frame - birth
        return k >= 0 && k < labels.count ? labels[k] : nil
    }

    public var label: TrackLabel { labels[labels.count - 1] }
}

public struct TrackStepStats {
    public var live = 0
    /// Tracks currently in the lost state (awaiting revival or retirement).
    public var lost = 0
    /// Tracks that failed tracking this frame.
    public var newlyLost = 0
    public var new = 0
    public var revived = 0
    public var retired = 0
    public var medianFB: Float = 0
    public var msDetect: Double = 0
    public var msTrack: Double = 0
}

/// Sparse feature tracker: Shi–Tomasi detection on the source gray, dense
/// flow as the motion seed, Lucas–Kanade refinement, forward–backward
/// validation, revival of occluded tracks from predicted positions, and
/// re-detection only where density dropped so ids stay stable.
public final class FeatureTracker {
    public let width: Int
    public let height: Int
    public var params: Params
    public private(set) var tracks: [Track] = []
    public private(set) var frame = -1
    public private(set) var lastStats = TrackStepStats()
    private var nextID = 1
    private var previousGray: [UInt8]?
    private var previousGradient: Engine.Gradient?

    public init(width: Int, height: Int, params: Params) {
        self.width = width
        self.height = height
        self.params = params
    }

    public var liveTracks: [Track] { tracks.filter { $0.status == .live } }

    /// Mutates a track in place by id.
    public func update(id: Int, _ body: (inout Track) -> Void) {
        if let i = tracks.firstIndex(where: { $0.id == id }) { body(&tracks[i]) }
    }

    public func updateAll(_ body: (inout Track) -> Void) {
        for i in tracks.indices { body(&tracks[i]) }
    }

    /// One frame. `forward` maps a previous-frame point to this frame (dense
    /// flow); `predict` optionally gives an object-model prediction for a
    /// track; `detectMask` (1 = allowed) restricts where new features spawn.
    public func step(
        gray: [UInt8], gradient: Engine.Gradient, rgba: [UInt8],
        forward: (SIMD2<Float>) -> SIMD2<Float>, predict: (Track) -> SIMD2<Float>?, detectMask: [UInt8]?,
        supplyMask: [UInt8]? = nil
    ) {
        frame += 1
        var stats = TrackStepStats()
        let trackStart = Date()
        let radius = max(2, params.lkRadius)
        let side = 2 * radius + 1
        if let previousGray, let previousGradient {
            var fbErrors: [Float] = []
            for i in tracks.indices {
                if tracks[i].status == .retired { continue }
                if tracks[i].status == .lost {
                    // Revival from the predicted position.
                    let guess = predict(tracks[i]) ?? forward(tracks[i].current)
                    let result = LucasKanade.refine(
                        template: tracks[i].template, radius: radius, current: gray, gradient: gradient,
                        width: width, height: height, start: guess, iterations: params.lkIterations,
                        minEigen: Float(params.lkMinEigen))
                    if result.converged && result.ssd < Float(params.trackSSDTolerance) {
                        tracks[i].status = .live
                        tracks[i].lostCount = 0
                        tracks[i].positions.append(result.position)
                        tracks[i].labels.append(tracks[i].label)
                        tracks[i].ssd = result.ssd
                        stats.revived += 1
                        stats.live += 1
                    } else {
                        tracks[i].lostCount += 1
                        tracks[i].positions.append(guess)
                        tracks[i].labels.append(tracks[i].label)
                        if tracks[i].lostCount > params.occlusionGrace || !inside(guess) {
                            tracks[i].status = .retired
                            stats.retired += 1
                        } else {
                            stats.lost += 1
                        }
                    }
                    continue
                }
                let previous = tracks[i].current
                // Seeds in order: dense flow, object prediction, zero motion.
                var seeds: [SIMD2<Float>] = [forward(previous)]
                if let p = predict(tracks[i]) { seeds.append(p) }
                seeds.append(previous)
                var best: LKResult?
                var bestFB: Float = .greatestFiniteMagnitude
                for seed in seeds {
                    let result = LucasKanade.refine(
                        template: tracks[i].template, radius: radius, current: gray, gradient: gradient,
                        width: width, height: height, start: seed, iterations: params.lkIterations,
                        minEigen: Float(params.lkMinEigen))
                    if !result.converged && result.minEigen >= Float(params.lkMinEigen) { continue }
                    // Forward–backward: track the found point back to the previous frame.
                    var fb: Float = .greatestFiniteMagnitude
                    if result.converged, let back = LucasKanade.patch(gray, width: width, height: height, at: result.position, radius: radius) {
                        let backward = LucasKanade.refine(
                            template: back, radius: radius, current: previousGray, gradient: previousGradient,
                            width: width, height: height, start: previous + (previous - result.position) * 0,
                            iterations: params.lkIterations, minEigen: Float(params.lkMinEigen))
                        if backward.converged { fb = simd_distance(backward.position, previous) }
                    } else if !result.converged {
                        // Flat patch: keep the seed, no FB claim possible.
                        fb = 0
                    }
                    let passes = fb <= Float(params.trackFBTolerance) && result.ssd <= Float(params.trackSSDTolerance)
                    if passes && (best == nil || result.ssd < best!.ssd) {
                        best = result
                        bestFB = fb
                    }
                    if passes && result.converged { break }
                }
                if let best {
                    tracks[i].positions.append(best.position)
                    tracks[i].labels.append(tracks[i].label)
                    tracks[i].fbError = bestFB
                    tracks[i].ssd = best.ssd
                    tracks[i].refined = best.converged
                    // Refresh the template slowly so appearance drift is followed.
                    if best.converged, let fresh = LucasKanade.patch(gray, width: width, height: height, at: best.position, radius: radius) {
                        for k in 0..<(side * side) { tracks[i].template[k] += 0.15 * (fresh[k] - tracks[i].template[k]) }
                    }
                    tracks[i].chroma.update(ChromaSignature.of(rgba: rgba, width: width, height: height, at: best.position))
                    fbErrors.append(bestFB)
                    stats.live += 1
                } else {
                    tracks[i].status = .lost
                    tracks[i].lostCount = 1
                    tracks[i].positions.append(predict(tracks[i]) ?? forward(previous))
                    tracks[i].labels.append(tracks[i].label)
                    stats.lost += 1
                    stats.newlyLost += 1
                }
            }
            fbErrors.sort()
            stats.medianFB = fbErrors.isEmpty ? 0 : fbErrors[fbErrors.count / 2]
        }
        stats.msTrack = Date().timeIntervalSince(trackStart) * 1000

        // Re-detection where density dropped.
        let detectStart = Date()
        let liveCount = tracks.reduce(0) { $0 + ($1.status == .live ? 1 : 0) }
        let needMain = liveCount < params.trackBudget
        if needMain || supplyMask != nil {
            let scores = Engine.shiTomasiScores(difference: gray, width: width, height: height)
            var frameMax: Float = 0
            for s in scores where s > frameMax { frameMax = s }
            if needMain {
                let added = detect(
                    scores: scores, frameMax: frameMax, gray: gray, rgba: rgba, radius: radius,
                    room: params.trackBudget - liveCount, minScore: Float(params.trackMinScore),
                    useQualityGate: true, allowMask: detectMask)
                stats.new += added
                stats.live += added
            }
            // Second pass: a lower Shi–Tomasi floor confined to attached
            // objects' hulls, so a low-contrast object (a dark plate on a dark
            // rack) picks up enough corners to be tracked and clustered where
            // the global threshold finds none. Runs after the main pass so it
            // only fills cells the main pass left empty, and inherits the same
            // trackBudget cap.
            if let supplyMask {
                let live = tracks.reduce(0) { $0 + ($1.status == .live ? 1 : 0) }
                if live < params.trackBudget {
                    let added = detect(
                        scores: scores, frameMax: frameMax, gray: gray, rgba: rgba, radius: radius,
                        room: params.trackBudget - live, minScore: Float(params.trackSupplyScore),
                        useQualityGate: false, allowMask: supplyMask)
                    stats.new += added
                    stats.live += added
                }
            }
        }
        stats.msDetect = Date().timeIntervalSince(detectStart) * 1000
        tracks.removeAll { $0.status == .retired }
        lastStats = stats
        previousGray = gray
        previousGradient = gradient
    }

    private func inside(_ p: SIMD2<Float>) -> Bool {
        p.x >= 0 && p.y >= 0 && p.x < Float(width) && p.y < Float(height)
    }

    /// Shi–Tomasi maxima in cells that hold no live track. `useQualityGate`
    /// applies the relative `trackQuality * frameMax` floor on top of
    /// `minScore`; the supply pass drops it so low-contrast corners inside an
    /// object hull are eligible. `allowMask` (1 = allowed) confines detection.
    private func detect(
        scores: [Float], frameMax: Float, gray: [UInt8], rgba: [UInt8], radius: Int, room: Int,
        minScore: Float, useQualityGate: Bool, allowMask: [UInt8]?
    ) -> Int {
        let spacing = max(4, params.trackSpacing)
        let cellsX = (width + spacing - 1) / spacing
        let cellsY = (height + spacing - 1) / spacing
        var occupied = [UInt8](repeating: 0, count: cellsX * cellsY)
        for track in tracks where track.status == .live {
            let cx = Int(track.current.x) / spacing, cy = Int(track.current.y) / spacing
            for dy in -1...1 {
                let y = cy + dy
                if y < 0 || y >= cellsY { continue }
                for dx in -1...1 {
                    let x = cx + dx
                    if x >= 0 && x < cellsX { occupied[y * cellsX + x] = 1 }
                }
            }
        }
        let threshold = useQualityGate ? max(minScore, Float(params.trackQuality) * frameMax) : minScore
        var candidates: [(index: Int, score: Float)] = []
        scores.withUnsafeBufferPointer { s in
            for y in 3..<(height - 3) {
                for x in 3..<(width - 3) {
                    let index = y * width + x
                    if s[index] < threshold { continue }
                    if occupied[(y / spacing) * cellsX + x / spacing] != 0 { continue }
                    if let allowMask, allowMask[index] == 0 { continue }
                    if Engine.isLocalMaximum(s, index, width) { candidates.append((index, s[index])) }
                }
            }
        }
        candidates.sort { $0.score > $1.score || ($0.score == $1.score && $0.index < $1.index) }
        var blocked = [UInt8](repeating: 0, count: width * height)
        for track in tracks where track.status == .live {
            let index = Int(track.current.y.rounded()) * width + Int(track.current.x.rounded())
            if index >= 0 && index < blocked.count {
                Engine.blockDiamond(&blocked, index: index, width: width, height: height, radius: spacing)
            }
        }
        var added = 0
        let limit = min(room, params.trackNewPerFrame)
        for candidate in candidates {
            if added >= limit { break }
            if blocked[candidate.index] != 0 { continue }
            let y = candidate.index / width
            let x = candidate.index - y * width
            let p = SIMD2<Float>(Float(x), Float(y))
            guard let template = LucasKanade.patch(gray, width: width, height: height, at: p, radius: radius) else { continue }
            tracks.append(Track(
                id: nextID, birth: frame, positions: [p], labels: [.unknown], template: template,
                score: candidate.score, chroma: ChromaSignature.of(rgba: rgba, width: width, height: height, at: p)))
            nextID += 1
            added += 1
            Engine.blockDiamond(&blocked, index: candidate.index, width: width, height: height, radius: spacing)
        }
        return added
    }
}
