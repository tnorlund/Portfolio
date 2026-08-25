import Foundation
import simd

public struct TrackEvidenceResult {
    public var trackerStats: TrackStepStats
    public var classifierStats: ClassifierStats
    public var clusterStats: ClusterStats
    public var overlay: [UInt8]
    public var msTracker: Double
    public var msObjects: Double
    /// Person + one alpha per attached object; nil while no object renders.
    public var labeled: LabeledMask?
    public var reports: [ObjectReport]
}

/// Orchestrates the feature tracker, the classifier, the object clusterer,
/// and the object renderers, and assembles the labeled mask.
public final class TrackEvidence {
    public let width: Int
    public let height: Int
    public var params: Params
    public let tracker: FeatureTracker
    public let classifier: TrackClassifier
    public let clusters: ObjectClusters
    public private(set) var frame = -1
    private var labelForObject: [Int: UInt8] = [:]
    private var nextLabel: UInt8 = 2
    private var previousAlphas: [Int: [UInt8]] = [:]

    public init(width: Int, height: Int, params: Params) {
        self.width = width
        self.height = height
        self.params = params
        self.tracker = FeatureTracker(width: width, height: height, params: params)
        self.classifier = TrackClassifier(width: width, height: height, params: params)
        self.clusters = ObjectClusters(width: width, height: height, params: params)
    }

    /// 1 within `hullRadius` of any member of an attached/occluded object,
    /// else 0; nil when no object is attached (nothing to supply).
    private func supplyMask(hullRadius: Int) -> [UInt8]? {
        let active = clusters.objects.filter { $0.status == .attached || $0.status == .occluded }
        if active.isEmpty { return nil }
        var positions: [SIMD2<Float>] = []
        for object in active {
            for id in object.trackIDs {
                if let t = tracker.tracks.first(where: { $0.id == id }), t.status == .live {
                    positions.append(t.current)
                }
            }
        }
        if positions.isEmpty { return nil }
        let r = max(1, hullRadius)
        let r2 = r * r
        var mask = [UInt8](repeating: 0, count: width * height)
        for p in positions {
            let cx = Int(p.x.rounded()), cy = Int(p.y.rounded())
            for dy in -r...r {
                let y = cy + dy
                if y < 0 || y >= height { continue }
                let row = y * width
                for dx in -r...r {
                    if dx * dx + dy * dy > r2 { continue }
                    let x = cx + dx
                    if x >= 0 && x < width { mask[row + x] = 1 }
                }
            }
        }
        return mask
    }

    // swiftlint:disable:next function_body_length
    public func compute(
        rgba: [UInt8], gray: [UInt8], gradient: Engine.Gradient, difference: [UInt8]?, warpedPlate: [UInt8]?,
        person: [UInt8], others: [UInt8]?, flow: OpticalFlow?
    ) -> TrackEvidenceResult {
        frame += 1
        tracker.params = params
        classifier.params = params
        clusters.params = params
        let count = width * height
        let trackStart = Date()
        let forward: (SIMD2<Float>) -> SIMD2<Float> = { p in flow?.forward(p) ?? p }
        let clusters = self.clusters
        // Supply mask: cells within hullRadius of the members of last frame's
        // attached/occluded objects. A second, lower-threshold detection pass
        // there feeds low-contrast objects (a dark plate on a dark rack) the
        // corners the global Shi–Tomasi floor misses. Built from the previous
        // frame's objects, which is why nothing is supplied until an object
        // has first attached on its own evidence.
        let supplyMask = supplyMask(hullRadius: Int(params.hullRadius.rounded()))
        tracker.step(
            gray: gray, gradient: gradient, rgba: rgba, forward: forward,
            predict: { clusters.predict($0) }, detectMask: nil, supplyMask: supplyMask)
        classifier.classify(
            tracker: tracker, frame: frame, rgba: rgba, difference: difference, warpedPlate: warpedPlate,
            person: person, others: others, registrarPrediction: nil)
        let msTracker = Date().timeIntervalSince(trackStart) * 1000

        let objectStart = Date()
        let distance = TrackClassifier.distanceTransform(person, width: width, height: height)
        clusters.update(tracker: tracker, frame: frame, person: person, distance: distance)

        // --- render every attached (or briefly occluded) object ---
        var labeled = LabeledMask(width: width, height: height)
        var byID: [Int: Track] = [:]
        for t in tracker.tracks where t.status == .live { byID[t.id] = t }
        var rendered = false
        for object in clusters.objects where object.status == .attached || (object.status == .occluded && object.occludedFrames <= 5) {
            // Member points that agree with the object's transform.
            var points: [SIMD2<Float>] = []
            for id in object.trackIDs {
                guard let t = byID[id] else { continue }
                if let c = object.canonical[id] {
                    if simd_distance(object.transform.apply(c), t.current) > Float(params.rigidResidual) * 2 { continue }
                }
                points.append(t.current)
            }
            var alpha: [UInt8]
            var mask: [UInt8]
            if object.kind == .rigid, let template = object.template, let captureTransform = object.templateTransform {
                let result = ObjectRender.render(
                    template: template, captureTransform: captureTransform, transform: object.transform, rgba: rgba,
                    person: person, width: width, height: height)
                alpha = result.alpha
                object.photoResidual = result.photoResidual.map(Float.init)
                mask = alpha.map { $0 > 127 ? 1 : 0 }
                // The template must keep explaining the pixels under it; when
                // it stops, re-grow from the tracks and re-capture when the
                // support is back (or clearly exceeds the stored one).
                let stale = (result.photoResidual ?? 999) > params.photoTolerance
                if object.status == .attached, points.count >= 6 {
                    let grown = ObjectRender.grow(
                        points: points, rgba: rgba, difference: difference, person: person, others: others,
                        width: width, height: height, params: params)
                    let support = ObjectRender.support(mask: grown, difference: difference, points: points)
                    if stale {
                        mask = grown
                        alpha = grown.map { $0 != 0 ? 255 : 0 }
                        object.templateSupport *= 0.9
                    }
                    if support > object.templateSupport * Float(params.templateRecapture) {
                        if let fresh = ObjectRender.capture(mask: grown, rgba: rgba, width: width, height: height, frame: frame) {
                            object.template = fresh
                            object.templateTransform = object.transform
                            object.templateSupport = support
                            mask = grown
                            alpha = grown.map { $0 != 0 ? 255 : 0 }
                        }
                    }
                }
            } else {
                if object.status == .occluded, let previous = previousAlphas[object.id] {
                    alpha = object.occludedFrames <= 5 ? (flow?.warp(previous, fill: 0) ?? previous) : [UInt8](repeating: 0, count: count)
                } else {
                    let grown = ObjectRender.grow(
                        points: points, rgba: rgba, difference: difference, person: person, others: others,
                        width: width, height: height, params: params)
                    alpha = grown.map { $0 != 0 ? 255 : 0 }
                    if object.kind == .rigid, points.count >= 6, frame - object.born >= params.templateDelay {
                        let support = ObjectRender.support(mask: grown, difference: difference, points: points)
                        if support > object.templateSupport, let fresh = ObjectRender.capture(mask: grown, rgba: rgba, width: width, height: height, frame: frame) {
                            object.template = fresh
                            object.templateTransform = object.transform
                            object.templateSupport = support
                        }
                    }
                }
                mask = alpha.map { $0 > 127 ? 1 : 0 }
            }
            let area = mask.reduce(0) { $0 + Int($1) }
            if area == 0 { continue }
            rendered = true
            // Colour signature: reference EMA, drift vs this frame.
            var current = ChromaHistogram()
            current.add(rgba: rgba, mask: mask)
            if object.histogram.total == 0 { object.histogram = current } else {
                object.colorDrift = object.histogram.distance(to: current)
                object.histogram.blend(current, alpha: 0.05)
            }
            if let previous = previousAlphas[object.id], let flow {
                let warped = flow.warp(previous, fill: 0)
                let previousArea = warped.reduce(0) { $0 + ($1 > 127 ? 1 : 0) }
                object.areaDelta = previousArea > 0 ? Float(abs(area - previousArea)) / Float(previousArea) : nil
            }
            object.lastArea = area
            previousAlphas[object.id] = alpha
            let label: UInt8
            if let l = labelForObject[object.id] { label = l } else {
                label = nextLabel
                labelForObject[object.id] = nextLabel
                nextLabel = nextLabel == 255 ? 2 : nextLabel + 1
            }
            labeled.alphas[object.id] = alpha
            labeled.objectLabels[object.id] = label
            for i in 0..<count where mask[i] != 0 && person[i] == 0 && labeled.labels[i] == 0 { labeled.labels[i] = label }
        }
        for i in 0..<count where person[i] != 0 { labeled.labels[i] = 1 }
        let live = Set(clusters.objects.map { $0.id })
        previousAlphas = previousAlphas.filter { live.contains($0.key) }
        let msObjects = Date().timeIntervalSince(objectStart) * 1000
        let overlay = TrackTile.render(
            rgba: rgba, width: width, height: height, person: person, tracks: tracker.tracks, objectOf: { $0.objectID })
        return TrackEvidenceResult(
            trackerStats: tracker.lastStats, classifierStats: classifier.lastStats, clusterStats: clusters.lastStats,
            overlay: overlay, msTracker: msTracker, msObjects: msObjects, labeled: rendered ? labeled : nil,
            reports: clusters.reports(frame: frame))
    }
}
