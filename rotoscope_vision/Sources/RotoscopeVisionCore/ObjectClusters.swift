import Foundation
import simd

public enum ObjectKind: String {
    case undecided
    case rigid
    case deformable
}

public enum ObjectStatus: String {
    case candidate
    case attached
    case occluded
    case retired
}

/// A persistent thing defined by its track ids: a similarity transform from
/// its own frame to the current one, a rigidity verdict learned from its
/// residuals, an attachment score to the subject, and (later) a template or
/// a colour signature that renders its mask.
public final class TrackedObject {
    public let id: Int
    public var kind: ObjectKind = .undecided
    public var trackIDs: Set<Int> = []
    public let born: Int
    public var lastSeen: Int
    public var status: ObjectStatus = .candidate
    public var transform = Similarity.identity
    public var previousTransform = Similarity.identity
    public var canonical: [Int: SIMD2<Float>] = [:]
    public var residualHistory: [Float] = []
    public var inlierHistory: [Float] = []
    public var contactHistory: [Float] = []
    public var attachScore: Float = 0
    public var contactFrac: Float = 0
    public var comotion: Float = 0
    public var belowExitFrames = 0
    public var occludedFrames = 0
    public var histogram = ChromaHistogram()
    public var liveTracks = 0
    public var lostTracks = 0
    public var labelFlips = 0
    public var lastGeomResidual: Float?
    public var lastInlierFrac: Float?
    public var kindStreak = 0
    public var template: RigidTemplate?
    public var templateSupport: Float = 0
    /// Object transform at the frame the template was captured.
    public var templateTransform: Similarity?
    public var areaDelta: Float?
    public var mask: [UInt8]?
    public var lastArea = 0
    public var photoResidual: Float?
    public var colorDrift: Float?

    public init(id: Int, born: Int) {
        self.id = id
        self.born = born
        self.lastSeen = born
    }

    /// Where a canonical point is expected next frame (constant velocity).
    public var predictedTransform: Similarity {
        let delta = transform * previousTransform.inverse
        // Damp the velocity so a wild last step cannot fling a prediction.
        let damped = Similarity(scale: 1 + (delta.scale - 1) * 0.5, angle: delta.angle * 0.5, tx: delta.tx * 0.8, ty: delta.ty * 0.8)
        return damped * transform
    }
}

/// A rigid object's captured appearance in its own frame.
public struct RigidTemplate {
    public var originX: Int
    public var originY: Int
    public var width: Int
    public var height: Int
    public var mask: [UInt8]
    public var rgb: [UInt8]
    public var capturedFrame: Int
}

public struct ClusterStats {
    public var objects = 0
    public var attached = 0
    public var occluded = 0
    public var newIDs = 0
    public var merges = 0
    public var splits = 0
    public var ms: Double = 0
}

/// Groups moving, non-subject tracks into persistent objects by pairwise
/// distance drift (rigid) or proximity + colour (deformable), fits each
/// object's similarity transform, scores attachment to the subject, and
/// carries objects through occlusion by prediction.
public final class ObjectClusters {
    public let width: Int
    public let height: Int
    public var params: Params
    public private(set) var objects: [TrackedObject] = []
    public private(set) var lastStats = ClusterStats()
    private var nextID = 1

    public init(width: Int, height: Int, params: Params) {
        self.width = width
        self.height = height
        self.params = params
    }

    public func object(id: Int) -> TrackedObject? { objects.first { $0.id == id } }

    /// Object-model prediction for a track (used by the tracker's seeds/revival).
    public func predict(_ track: Track) -> SIMD2<Float>? {
        guard let oid = track.objectID, let object = object(id: oid), let c = object.canonical[track.id] else { return nil }
        return object.predictedTransform.apply(c)
    }

    private struct UnionFind {
        var parent: [Int]
        init(_ n: Int) { parent = Array(0..<n) }
        mutating func find(_ x: Int) -> Int {
            var r = x
            while parent[r] != r { r = parent[r] }
            var c = x
            while parent[c] != r { let n = parent[c]; parent[c] = r; c = n }
            return r
        }
        mutating func union(_ a: Int, _ b: Int) {
            let ra = find(a), rb = find(b)
            if ra != rb { parent[max(ra, rb)] = min(ra, rb) }
        }
    }

    // swiftlint:disable:next function_body_length
    public func update(tracker: FeatureTracker, frame: Int, person: [UInt8], distance: [UInt16]) {
        let start = Date()
        var stats = ClusterStats()
        let p = params
        let window = max(5, min(p.motionWindow, 10))

        // --- candidates: moving/attached, alive long enough, and genuinely
        // not explained by the plate. Nothing clusters before the motion
        // window has history: with no history every track looks "moving" and
        // the background would cluster into one giant rigid object.
        var candidates: [Track] = []
        if frame >= p.motionWindow {
            for t in tracker.tracks where t.status == .live && (t.label == .moving || t.label == .attached)
                && t.positions.count >= 5 && t.plateAgreement < 0.6 && t.staticScore < 0.8
            {
                candidates.append(t)
            }
        }
        var indexByID: [Int: Int] = [:]
        for (i, t) in candidates.enumerated() { indexByID[t.id] = i }

        // --- rigid affinity: pairwise distance drift over the window ---
        var uf = UnionFind(candidates.count)
        var hasRigidPartner = [Bool](repeating: false, count: candidates.count)
        let link = Float(p.clusterLink)
        // Rigidity is only evidence when things move: two tracks that both sat
        // still over the window are trivially "rigid" with each other, and at
        // the bottom of a squat that would chain the bar to anything static
        // nearby. Require real displacement over the window from both.
        var moved = [Bool](repeating: false, count: candidates.count)
        for i in 0..<candidates.count {
            let t = candidates[i]
            let n = min(window, t.positions.count - 1)
            moved[i] = n >= 4 && simd_distance(t.current, t.positions[t.positions.count - 1 - n]) >= 2
        }
        for i in 0..<candidates.count where moved[i] {
            let a = candidates[i]
            for j in (i + 1)..<candidates.count where moved[j] {
                let b = candidates[j]
                if simd_distance(a.current, b.current) > link { continue }
                let n = min(window, a.positions.count, b.positions.count)
                if n < 5 { continue }
                var sum: Float = 0, sq: Float = 0
                for k in 0..<n {
                    let d = simd_distance(a.positions[a.positions.count - 1 - k], b.positions[b.positions.count - 1 - k])
                    sum += d; sq += d * d
                }
                let mean = sum / Float(n)
                let variance = max(0, sq / Float(n) - mean * mean)
                if variance.squareRoot() < Float(p.rigidDrift) {
                    uf.union(i, j)
                    hasRigidPartner[i] = true
                    hasRigidPartner[j] = true
                }
            }
        }
        // --- deformable affinity for tracks without a rigid partner ---
        for i in 0..<candidates.count where !hasRigidPartner[i] {
            let a = candidates[i]
            let da = a.current - a.positions[max(0, a.positions.count - 6)]
            for j in 0..<candidates.count where j != i && !hasRigidPartner[j] {
                let b = candidates[j]
                if simd_distance(a.current, b.current) > Float(p.deformLink) { continue }
                if a.chroma.distance(to: b.chroma) > Float(p.chromaTolerance) { continue }
                let db = b.current - b.positions[max(0, b.positions.count - 6)]
                if simd_distance(da, db) > 2.5 { continue }
                uf.union(i, j)
            }
        }
        var clusters: [Int: [Int]] = [:]
        for i in 0..<candidates.count { clusters[uf.find(i), default: []].append(i) }
        let minRigid = p.minClusterTracks
        let clusterList = clusters.values.filter { members in
            let rigid = members.contains { hasRigidPartner[$0] }
            return members.count >= (rigid ? minRigid : max(3, minRigid - 1))
        }.sorted { $0.count > $1.count }

        // --- identity: match clusters to objects by shared track ids ---
        var assigned = Set<Int>()
        for object in objects { object.liveTracks = 0; object.lostTracks = 0 }
        for members in clusterList {
            let ids = Set(members.map { candidates[$0].id })
            var best: TrackedObject?
            var bestOverlap = 0
            for object in objects where object.status != .retired && !assigned.contains(object.id) {
                let overlap = object.trackIDs.intersection(ids).count
                if overlap > bestOverlap { bestOverlap = overlap; best = object }
            }
            let object: TrackedObject
            if let best, bestOverlap * 2 >= min(ids.count, max(1, best.trackIDs.count)) {
                object = best
            } else {
                object = TrackedObject(id: nextID, born: frame)
                nextID += 1
                objects.append(object)
                stats.newIDs += 1
            }
            assigned.insert(object.id)
            object.lastSeen = frame
            // Tracks leaving another object count as a split signal.
            for id in ids {
                for other in objects where other !== object && other.trackIDs.contains(id) {
                    other.trackIDs.remove(id)
                    other.canonical[id] = nil
                }
            }
            object.trackIDs = ids
            object.liveTracks = ids.count
            // Canonical coordinates: the object frame is the frame of creation.
            var from: [SIMD2<Float>] = [], to: [SIMD2<Float>] = [], fromIDs: [Int] = []
            for i in members {
                let t = candidates[i]
                if let c = object.canonical[t.id] {
                    from.append(c); to.append(t.current); fromIDs.append(t.id)
                }
            }
            if from.count >= 3, let fit = Similarity.fitRobust(from: from, to: to, threshold: Float(p.rigidResidual)) {
                object.previousTransform = object.transform
                object.transform = fit.transform
                let inliers = fit.inliers.reduce(0) { $0 + ($1 ? 1 : 0) }
                object.residualHistory.append(fit.rms)
                object.inlierHistory.append(Float(inliers) / Float(from.count))
                object.lastGeomResidual = fit.rms
                object.lastInlierFrac = Float(inliers) / Float(from.count)
                // Outliers: a rigid object expels tracks that keep disagreeing
                // with its transform (they belong to something else that
                // merely paused beside it); a deformable object tolerates
                // three times the residual before doing the same. Expelled
                // tracks re-anchor so a later re-link starts clean.
                let expelBound = Float(p.rigidResidual) * (object.kind == .rigid ? 1 : 3)
                var expelled: [Int] = []
                for (k, id) in fromIDs.enumerated() {
                    let off = simd_distance(object.transform.apply(from[k]), to[k])
                    if fit.inliers[k] && off <= expelBound {
                        tracker.update(id: id) { $0.outlierStreak = 0 }
                        continue
                    }
                    var streak = 0
                    tracker.update(id: id) { $0.outlierStreak += 1; streak = $0.outlierStreak }
                    object.canonical[id] = object.transform.inverse.apply(to[k])
                    if streak >= p.outlierExpel { expelled.append(id) }
                }
                for id in expelled {
                    object.trackIDs.remove(id)
                    object.canonical[id] = nil
                    tracker.update(id: id) { $0.objectID = nil; $0.outlierStreak = 0 }
                    stats.splits += 1
                }
                object.liveTracks = object.trackIDs.count
            } else if from.count < 3 {
                object.previousTransform = object.transform
            }
            for i in members where object.trackIDs.contains(candidates[i].id) {
                let t = candidates[i]
                if object.canonical[t.id] == nil {
                    object.canonical[t.id] = object.transform.inverse.apply(t.current)
                }
                tracker.update(id: t.id) { $0.objectID = object.id }
            }
            if object.residualHistory.count > 40 { object.residualHistory.removeFirst() }
            if object.inlierHistory.count > 40 { object.inlierHistory.removeFirst() }
            // Rigidity verdict with hysteresis over the recent history.
            if object.residualHistory.count >= 8 {
                let recent = Array(object.residualHistory.suffix(20)).sorted()
                let medianResidual = recent[recent.count / 2]
                let inl = Array(object.inlierHistory.suffix(20)).sorted()
                let medianInlier = inl[inl.count / 2]
                let rigidNow = medianResidual < Float(p.rigidResidual) && medianInlier > 0.8
                let verdict: ObjectKind = rigidNow ? .rigid : .deformable
                if object.kind == .undecided {
                    object.kind = verdict
                } else if verdict != object.kind {
                    object.kindStreak += 1
                    if object.kindStreak >= 10 { object.kind = verdict; object.kindStreak = 0 }
                } else {
                    object.kindStreak = 0
                }
            }
            // Attachment: contact over the window, co-motion with nearby subject tracks.
            var inContact = 0
            var objectMotion = SIMD2<Float>(0, 0)
            var motionCount = 0
            // Positions of the members that actually touch the subject; the
            // co-motion comparison uses the subject tracks nearest these, not
            // the object's centroid.
            var contactPoints: [SIMD2<Float>] = []
            for i in members {
                let t = candidates[i]
                let idx = Int(t.current.y.rounded()) * width + Int(t.current.x.rounded())
                if idx >= 0 && idx < distance.count && Float(distance[idx]) / 3 < Float(p.contactRadius) {
                    inContact += 1
                    contactPoints.append(t.current)
                }
                if t.positions.count >= 11 {
                    objectMotion += t.current - t.positions[t.positions.count - 11]
                    motionCount += 1
                }
            }
            // Contact is a property of the whole object: a barbell touches the
            // subject only at the hands, so "any member in contact" is the
            // indicator, not the fraction of members.
            object.contactHistory.append(inContact >= min(2, members.count) ? 1 : 0)
            if object.contactHistory.count > window { object.contactHistory.removeFirst() }
            object.contactFrac = object.contactHistory.reduce(0, +) / Float(object.contactHistory.count)
            // Co-motion holds its last value while both are still: stillness
            // is not evidence either way, and "both still = 1" would attach
            // any bystander that paused beside the subject.
            var comotion: Float = object.comotion * 0.95
            if motionCount > 0 {
                objectMotion /= Float(motionCount)
                // Reference points for "which part of the subject is this
                // moving with": the members in contact when there are any,
                // else the centroid. A barbell touches the subject at the
                // hands, so its contact members' nearest subject tracks are
                // the wrists — which track the descent — not the shoulders and
                // head nearest the object's centroid.
                var references = contactPoints
                if references.isEmpty {
                    var centroid = SIMD2<Float>(0, 0)
                    for i in members { centroid += candidates[i].current }
                    centroid /= Float(members.count)
                    references = [centroid]
                }
                var subjectMotion = SIMD2<Float>(0, 0)
                var subjectCount = 0
                var nearest: [(Float, SIMD2<Float>)] = []
                for t in tracker.tracks where t.status == .live && t.label == .subject && t.positions.count >= 11 {
                    var d = Float.greatestFiniteMagnitude
                    for r in references { d = min(d, simd_distance(t.current, r)) }
                    nearest.append((d, t.current - t.positions[t.positions.count - 11]))
                }
                nearest.sort { $0.0 < $1.0 }
                for (_, v) in nearest.prefix(10) { subjectMotion += v; subjectCount += 1 }
                if subjectCount > 0 {
                    subjectMotion /= Float(subjectCount)
                    let no = simd_length(objectMotion), ns = simd_length(subjectMotion)
                    if no > 0.5 && ns > 0.5 {
                        comotion = simd_dot(objectMotion, subjectMotion) / (no * ns) * min(1, no / ns, ns / no)
                    } else if no > 0.5 || ns > 0.5 {
                        comotion = 0  // one moves, the other does not
                    }
                }
            }
            object.comotion = comotion
            object.attachScore = 0.35 * object.contactFrac + 0.65 * max(0, comotion)
            switch object.status {
            case .candidate, .occluded:
                if object.attachScore > Float(p.attachEnter) { object.status = .attached; object.belowExitFrames = 0 }
                else if object.status == .occluded { object.status = .attached }  // reacquired
            case .attached:
                if object.attachScore < Float(p.attachExit) {
                    object.belowExitFrames += 1
                    if object.belowExitFrames >= p.labelHold { object.status = .candidate }
                } else {
                    object.belowExitFrames = 0
                }
            case .retired: break
            }
            object.occludedFrames = 0
        }

        // --- objects not seen this frame: occlusion / retirement ---
        for object in objects where !assigned.contains(object.id) && object.status != .retired {
            object.lostTracks = object.trackIDs.count
            if object.status == .attached || object.status == .occluded {
                object.status = .occluded
                object.occludedFrames += 1
                // Hold the transform: extrapolating velocity for many frames
                // flings the model far from where the object reappears.
                object.previousTransform = object.transform
                if object.occludedFrames > p.occlusionGrace { object.status = .retired }
            } else {
                object.status = .retired
            }
        }
        objects.removeAll { $0.status == .retired }

        // Dissolve objects the classifier now considers static: an object is
        // only real while its tracks keep disagreeing with the plate.
        for object in objects where object.status != .retired {
            var agreeing = 0, total = 0
            for id in object.trackIDs {
                if let t = tracker.tracks.first(where: { $0.id == id }), t.status == .live {
                    total += 1
                    if t.plateAgreement > 0.7 && t.staticScore > 0.8 { agreeing += 1 }
                }
            }
            if total >= 3 && agreeing * 2 > total { object.status = .retired }
        }
        objects.removeAll { $0.status == .retired }
        // Relabel tracks by their object's status, but only tracks the
        // classifier still calls moving: an object never overrides a static,
        // subject, or other verdict.
        for object in objects {
            for id in object.trackIDs {
                tracker.update(id: id) { t in
                    guard t.status == .live, t.label == .moving || t.label == .attached else {
                        if t.status == .live { t.objectID = nil }
                        return
                    }
                    let label: TrackLabel = object.status == .attached || object.status == .occluded ? .attached : .moving
                    if t.labels[t.labels.count - 1] != label { object.labelFlips += 1 }
                    t.labels[t.labels.count - 1] = label
                }
            }
            object.trackIDs = object.trackIDs.filter { id in
                tracker.tracks.first(where: { $0.id == id })?.objectID == object.id
            }
        }
        stats.objects = objects.count
        stats.attached = objects.filter { $0.status == .attached }.count
        stats.occluded = objects.filter { $0.status == .occluded }.count
        stats.ms = Date().timeIntervalSince(start) * 1000
        lastStats = stats
    }

    public func reports(frame: Int) -> [ObjectReport] {
        objects.map { o in
            ObjectReport(
                id: o.id, kind: o.kind.rawValue, status: o.status.rawValue, liveTracks: o.liveTracks, lostTracks: o.lostTracks,
                geomResidual: o.lastGeomResidual.map(Double.init), rigidity: o.residualHistory.last.map(Double.init),
                inlierFrac: o.lastInlierFrac.map(Double.init), photoResidual: o.photoResidual.map(Double.init),
                colorDrift: o.colorDrift.map(Double.init), labelFlips: o.labelFlips, area: o.lastArea,
                areaDelta: nil, visible: o.status == .occluded ? 0 : 1, attachScore: Double(o.attachScore),
                contactFrac: Double(o.contactFrac), comotion: Double(o.comotion), scale: Double(o.transform.scale),
                angle: Double(o.transform.angle), tx: Double(o.transform.tx), ty: Double(o.transform.ty))
        }
    }
}
