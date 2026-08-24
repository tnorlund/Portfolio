import Foundation
import simd

public struct TrackEvidenceResult {
    public var trackerStats: TrackStepStats
    public var classifierStats: ClassifierStats
    public var overlay: [UInt8]
    public var msTracker: Double
    public var msObjects: Double
    /// Set once objects exist (M2+); nil keeps the caller's mask untouched.
    public var labeled: LabeledMask?
    public var reports: [ObjectReport]
}

/// Orchestrates the feature tracker and the classifier (objects follow in
/// later milestones). The mask is not touched yet: this is the instrumented
/// substrate the object models are built on.
public final class TrackEvidence {
    public let width: Int
    public let height: Int
    public var params: Params
    public let tracker: FeatureTracker
    public let classifier: TrackClassifier
    public private(set) var frame = -1

    public init(width: Int, height: Int, params: Params) {
        self.width = width
        self.height = height
        self.params = params
        self.tracker = FeatureTracker(width: width, height: height, params: params)
        self.classifier = TrackClassifier(width: width, height: height, params: params)
    }

    public func compute(
        rgba: [UInt8], gray: [UInt8], gradient: Engine.Gradient, difference: [UInt8]?, warpedPlate: [UInt8]?,
        person: [UInt8], others: [UInt8]?, flow: OpticalFlow?
    ) -> TrackEvidenceResult {
        frame += 1
        tracker.params = params
        classifier.params = params
        let trackStart = Date()
        let forward: (SIMD2<Float>) -> SIMD2<Float> = { p in flow?.forward(p) ?? p }
        tracker.step(gray: gray, gradient: gradient, rgba: rgba, forward: forward, predict: { _ in nil }, detectMask: nil)
        classifier.classify(
            tracker: tracker, frame: frame, rgba: rgba, difference: difference, warpedPlate: warpedPlate,
            person: person, others: others, registrarPrediction: nil)
        let msTracker = Date().timeIntervalSince(trackStart) * 1000
        let overlay = TrackTile.render(
            rgba: rgba, width: width, height: height, person: person, tracks: tracker.tracks, objectOf: { $0.objectID })
        return TrackEvidenceResult(
            trackerStats: tracker.lastStats, classifierStats: classifier.lastStats, overlay: overlay,
            msTracker: msTracker, msObjects: 0, labeled: nil, reports: [])
    }
}
