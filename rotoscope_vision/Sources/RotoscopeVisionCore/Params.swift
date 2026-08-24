import Foundation

/// Every tunable of the pipeline in one Codable value, so a run is fully
/// described by one JSON file and an optimizer can sweep it. Defaults are the
/// shipped behavior; `documentation` gives each key's meaning and sane range.
public struct Params: Codable, Equatable {
    // Evidence model: "legacy" (threshold + carry + expiry) or "soft"
    // (probabilistic evidence, flow prior, pose priors, one decision).
    public var evidence: String = "soft"

    // Plate difference
    public var plateThreshold: Double = 48
    public var plateTolerance: Int = 4
    public var plateSamples: Int = 48
    public var entryMargin: Double = 1.0 / 6.0
    public var weakRatio: Double = 0.5
    public var carryFrames: Int = 90
    public var carryDilate: Int = 4

    // Soft evidence
    public var diffCenter: Double = 40
    public var diffWidth: Double = 10
    public var priorWeight: Double = 0.6
    public var priorDecay: Double = 0.92
    public var structWeight: Double = 0.75
    public var barHalfWidth: Double = 9
    public var shadowStrength: Double = 0.85
    public var shadowMinRatio: Double = 0.45
    public var shadowMaxRatio: Double = 0.97
    public var shadowChromaTolerance: Double = 26
    public var smoothRadius: Int = 2
    public var decisionThreshold: Double = 0.5
    public var trackDiscs: Bool = true
    public var discSmoothing: Double = 0.6
    public var headExclusion: Double = 0.125
    public var footSlack: Double = 1.0 / 12.0

    // Registration
    public var stripHeight: Int = 120
    public var refineRange: Int = 10
    public var maxJump: Double = 40

    // Optical flow
    public var flowAccuracy: String = "medium"

    // Feature tracks (evidence == "tracks")
    public var trackBudget: Int = 1500
    public var trackSpacing: Int = 10
    public var trackQuality: Double = 0.01
    public var trackMinScore: Double = 400
    public var trackNewPerFrame: Int = 300
    public var lkRadius: Int = 5
    public var lkIterations: Int = 8
    public var lkMinEigen: Double = 50
    public var trackFBTolerance: Double = 1.0
    public var trackSSDTolerance: Double = 30
    public var staticTolerance: Double = 0.75
    public var moveTolerance: Double = 4.0
    public var motionWindow: Int = 15
    public var labelHold: Int = 30
    public var contactRadius: Double = 40
    public var attachEnter: Double = 0.6
    public var attachExit: Double = 0.4
    public var rigidDrift: Double = 1.0
    public var clusterLink: Double = 120
    public var deformLink: Double = 40
    public var chromaTolerance: Double = 0.06
    public var minClusterTracks: Int = 6
    public var rigidResidual: Double = 1.5
    public var hullRadius: Double = 24
    public var capsuleRadius: Double = 10
    public var colorGate: Double = 0.3
    public var templateDelay: Int = 10
    public var templateRecapture: Double = 1.3
    public var photoTolerance: Double = 40
    public var outlierExpel: Int = 5
    public var occlusionGrace: Int = 30

    // Engine (paint)
    public var markerBudget: Int = 1200
    public var blurRadius: Int = 3
    public var faceQuota: Double = 0.3
    public var spacingFace: Double = 2
    public var spacingBody: Double = 4
    public var spacingBackground: Double = 8
    public var colorEdges: Bool = true
    public var propagateMarkers: Bool = true

    public init() {}

    public static func load(_ url: URL) throws -> Params {
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(Params.self, from: data)
    }

    public func json() throws -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return String(decoding: try encoder.encode(self), as: UTF8.self)
    }

    /// Meaning and range of every key, for humans and for the sweep script.
    public static let documentation: [String: String] = [
        "evidence": "\"legacy\" (thresholds + carry + expiry), \"soft\" (probabilistic fusion) or \"tracks\" (feature-track objects)",
        "trackBudget": "tracks: maximum live feature tracks, 600–3000",
        "trackSpacing": "tracks: detection cell / suppression radius, px, 6–16",
        "trackQuality": "tracks: Shi–Tomasi threshold as a fraction of the frame maximum, 0.005–0.05",
        "trackMinScore": "tracks: absolute Shi–Tomasi floor, 200–5000",
        "trackNewPerFrame": "tracks: new features per frame, 50–600",
        "lkRadius": "tracks: Lucas–Kanade window radius, 3–7",
        "lkIterations": "tracks: Lucas–Kanade iterations, 4–16",
        "lkMinEigen": "tracks: minimum texture eigenvalue for a refine, 10–200",
        "trackFBTolerance": "tracks: forward–backward error to keep a track, px, 0.5–2",
        "trackSSDTolerance": "tracks: mean template residual to keep a track, gray levels, 15–50",
        "staticTolerance": "tracks: per-frame background-relative motion counted as static, px, 0.4–1.5",
        "moveTolerance": "tracks: windowed displacement counted as moving, px, 2–6",
        "motionWindow": "tracks: frames for the windowed displacement, 8–30",
        "labelHold": "tracks: frames a label must hold before demotion, 10–60",
        "contactRadius": "tracks: distance to the person mask that counts as contact, px, 20–80",
        "attachEnter": "tracks: attach score to become attached, 0.4–0.8",
        "attachExit": "tracks: attach score to be released, 0.2–0.6",
        "rigidDrift": "tracks: pairwise distance std over the window for a rigid link, px, 0.3–1.5",
        "clusterLink": "tracks: max distance for a rigid link, px, 60–300",
        "deformLink": "tracks: max distance for a deformable link, px, 20–80",
        "chromaTolerance": "tracks: rg-chromaticity distance for a deformable link, 0.03–0.12",
        "minClusterTracks": "tracks: tracks needed to form an object, 3–8",
        "rigidResidual": "tracks: inlier residual of the similarity fit, px, 1–3",
        "hullRadius": "tracks: support disc radius around each track for template capture, px, 12–40",
        "capsuleRadius": "tracks: support radius along rigid edges, px, 6–16",
        "colorGate": "tracks: back-projection threshold for the colour gate, 0.15–0.5",
        "templateDelay": "tracks: frames after attachment before the template is captured, 5–20",
        "templateRecapture": "tracks: support gain that triggers re-capture, 1.1–2",
        "outlierExpel": "tracks: consecutive outlier frames before a track leaves its object, 3–15",
        "photoTolerance": "tracks: max template photometric residual before the object is re-grown, 20–80",
        "occlusionGrace": "tracks: frames a lost track / occluded object survives, 10–60",
        "plateThreshold": "tolerant difference that counts as a prop, 24–80",
        "plateTolerance": "misalignment tolerance in half-res pixels, 2–8",
        "plateSamples": "frames sampled for the background median, 16–96",
        "entryMargin": "legacy: entry threshold = plateThreshold·(1+margin), 0–0.5",
        "weakRatio": "legacy: carry survives at plateThreshold·ratio, 0.3–0.8",
        "carryFrames": "legacy: frames a component may live without strong support, 15–200",
        "carryDilate": "legacy: motion allowance for the carry, px, 2–10",
        "diffCenter": "soft: logistic center on the tolerant difference, 24–64",
        "diffWidth": "soft: logistic width, 4–20",
        "priorWeight": "soft: weight of the flow-warped previous posterior, 0–0.9",
        "priorDecay": "soft: per-frame decay of an unsupported prior, 0.7–0.98",
        "structWeight": "soft: weight of the pose bar-line / disc evidence, 0–1",
        "barHalfWidth": "soft: half-width of the bar prior around the elbow line, px, 5–16",
        "shadowStrength": "soft: how much a shadow-like pixel is discounted, 0–1",
        "shadowMinRatio": "shadow brightness ratio lower bound, 0.3–0.6",
        "shadowMaxRatio": "shadow brightness ratio upper bound, 0.9–0.99",
        "shadowChromaTolerance": "per-channel chroma tolerance for shadow, 12–40",
        "smoothRadius": "soft: box radius applied to the posterior before the decision, 0–5",
        "decisionThreshold": "soft: posterior threshold, 0.3–0.7",
        "trackDiscs": "soft: fit and track plate discs at the bar ends",
        "discSmoothing": "soft: exponential smoothing of disc center/radius, 0–0.9",
        "headExclusion": "no props within this fraction of the person's height below the head top, 0–0.25",
        "footSlack": "props allowed this fraction below the feet, 0–0.2",
        "stripHeight": "rows of the never-occluded top strip for coarse registration, 60–200",
        "refineRange": "photometric refinement search radius in quarter-res px, 3–16",
        "maxJump": "largest accepted frame-to-frame translation change, px, 10–80",
        "flowAccuracy": "Vision optical flow accuracy: low, medium, high, veryHigh",
        "markerBudget": "watershed markers per frame, 300–3000",
        "blurRadius": "box blur radius of the stand-in background, 1–9",
        "faceQuota": "share of markers inside the eye ellipse, 0–0.5",
        "spacingFace": "marker suppression radius in the face tier, 1–6",
        "spacingBody": "marker suppression radius in the body tier, 2–10",
        "spacingBackground": "marker suppression radius in the background tier, 4–16",
        "colorEdges": "watershed on per-channel Sobel max (true) or gray only (false)",
        "propagateMarkers": "carry last frame's markers through optical flow as seeds",
    ]
}
