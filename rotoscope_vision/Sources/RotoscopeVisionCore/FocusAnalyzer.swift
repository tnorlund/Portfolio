import CoreImage
import CoreVideo
import Foundation
import Vision
import simd

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
    /// Person instance mask plus anything that differs from a static-camera
    /// background plate *and* is connected to the person: held props such as a
    /// barbell or a band, which Vision's subject lift only keeps intermittently.
    case held
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
    /// Binary person (instance) and prop masks, for metrics and overlays.
    public var person: [UInt8]
    public var props: [UInt8]
    public var others: [UInt8]?
    public var difference: [UInt8]?
    public var warpedPlate: [UInt8]?
    public var pose: BodyPose?
    public var evidence: PropEvidenceResult?
    public var msVision: Double
    public var msEvidence: Double
}

/// Runs the Vision requests for one frame and turns them into the engine's
/// per-pixel focus tiers: the periocular ellipse around the detected eyes is
/// `face`, the rest of the subject mask is `body`, everything else is
/// `background`.
public final class FocusAnalyzer {
    public let width: Int
    public let height: Int
    public let mode: SubjectMode
    public var params: Params
    /// Periocular ellipse radii as multiples of the inter-eye distance. The
    /// homepage portrait ellipse (radii 0.085×960 by 0.055×720 for eyes 85 px
    /// apart) works out to roughly 0.95 × and 0.46 ×.
    public var eyeRadiusX: Double = 0.95
    public var eyeRadiusY: Double = 0.46
    public var maskThreshold: UInt8 = 127
    /// Static-camera background plate for `.held`.
    public var plate: BackgroundPlate?
    /// Aligns each frame to the plate's reference frame before differencing.
    public var registrar: FrameRegistrar?
    /// Optical flow between consecutive frames (nil = no temporal model).
    public var flow: OpticalFlow?
    public private(set) var poseDetector: PoseDetector
    public private(set) var soft: SoftEvidence
    /// Last frame's homography (frame → reference), for diagnostics.
    public private(set) var lastHomography = matrix_identity_float3x3
    public private(set) var previousHomography = matrix_identity_float3x3
    public private(set) var lastRegistrationAccepted = true
    public private(set) var lastRefinement = (dx: 0, dy: 0)
    public private(set) var lastWarpedPlate: [UInt8]?
    public private(set) var lastDifference: [UInt8]?
    public private(set) var rejectedRegistrations = 0
    /// When set, `.held` dumps its intermediate masks for the next frame here.
    public var debugDump: (directory: URL, prefix: String)?
    private var previousProps: [UInt8]?
    private var previousAge: [UInt8]?
    private var previousPerson: [UInt8]?

    private let context = CIContext(options: [.cacheIntermediates: false])
    private var maskTarget: CVPixelBuffer?

    public init(width: Int, height: Int, mode: SubjectMode, params: Params = Params()) {
        self.width = width
        self.height = height
        self.mode = mode
        self.params = params
        self.poseDetector = PoseDetector(width: width, height: height)
        self.soft = SoftEvidence(width: width, height: height, params: params)
    }

    /// Largest-person soft mask only (no props, no tiers); used to blank the
    /// subject while building the aligned background plate.
    public func personMask(_ pixelBuffer: CVPixelBuffer) throws -> [UInt8] {
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: .up, options: [:])
        let request = VNGeneratePersonInstanceMaskRequest()
        try handler.perform([request])
        if let observation = request.results?.first, let best = try largestInstance(in: observation) {
            let scaled = try observation.generateScaledMaskForImage(
                forInstances: IndexSet(integer: best), from: handler)
            return try maskBytes(from: scaled)
        }
        return [UInt8](repeating: 0, count: width * height)
    }

    // swiftlint:disable:next function_body_length
    public func analyze(_ pixelBuffer: CVPixelBuffer) throws -> FrameFocus {
        let count = width * height
        let visionStart = Date()
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
        case .person, .foreground, .held:
            let request: VNImageBasedRequest =
                mode == .foreground
                ? VNGenerateForegroundInstanceMaskRequest()
                : VNGeneratePersonInstanceMaskRequest()
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
        let person = mask.map { $0 > maskThreshold ? UInt8(1) : 0 }

        // Temporal model: flow from the previous frame, calibrated on the
        // person masks the first time.
        if let flow {
            try flow.update(current: pixelBuffer, previousMask: previousPerson, currentMask: mask)
        }

        var others: [UInt8]? = nil
        var pose: BodyPose? = nil
        var props = [UInt8](repeating: 0, count: count)
        var difference: [UInt8]? = nil
        var evidence: PropEvidenceResult? = nil
        var msEvidence = 0.0
        if mode == .held, let plate {
            // Everyone else in the shot: never a prop, even when the bar
            // sweeps across them. Whole-frame person segmentation is blobby
            // and labels the bar at the subject's arms as "person", so decide
            // per connected blob: blobs touching the subject's own instance
            // are the subject; the rest are other people.
            let everyone = VNGeneratePersonSegmentationRequest()
            everyone.qualityLevel = .accurate
            everyone.outputPixelFormat = kCVPixelFormatType_OneComponent8
            try handler.perform([everyone])
            if let result = everyone.results?.first {
                let all = try maskBytes(from: result.pixelBuffer)
                let people = all.map { $0 > 127 ? UInt8(1) : 0 }
                let own = mask.map { $0 > 32 ? UInt8(1) : 0 }
                let mine = Morphology.componentsTouching(people, anchor: own, width: width, height: height)
                var binary = [UInt8](repeating: 0, count: count)
                for index in 0..<count where people[index] != 0 && mine[index] == 0 { binary[index] = 1 }
                for _ in 0..<3 { binary = Morphology.dilate(binary, width: width, height: height) }
                others = binary
            }
            pose = try poseDetector.detect(pixelBuffer, subjectMask: mask)
            let rgba = rgbaBytes(from: pixelBuffer)
            let visionMs = Date().timeIntervalSince(visionStart) * 1000
            let evidenceStart = Date()
            let (diff, warped) = try plateDifference(rgba: rgba, mask: mask, plate: plate)
            difference = diff
            let band = verticalBand(person: person)
            if params.evidence == "soft" {
                soft.params = params
                let result = soft.compute(
                    rgba: rgba, difference: diff, warpedPlate: warped, person: person, others: others,
                    pose: pose, flow: flow, minY: band.minY, maxY: band.maxY)
                props = result.props
                evidence = result
            } else {
                props = try legacyProps(
                    rgba: rgba, difference: diff, warpedPlate: warped, person: person, others: others,
                    minY: band.minY, maxY: band.maxY)
            }
            previousProps = props
            msEvidence = Date().timeIntervalSince(evidenceStart) * 1000
            // Prop alpha: soft edge from the difference strength; asserted
            // interiors (disc, filled hole) come in opaque.
            for index in 0..<count where props[index] != 0 && person[index] == 0 {
                let strength = Int(diff[index])
                let alpha = min(255, max(160, (strength - Int(params.plateThreshold)) * 8 + 160))
                mask[index] = max(mask[index], UInt8(alpha))
            }
            _ = visionMs
        }
        previousPerson = mask

        let faceRequest = VNDetectFaceLandmarksRequest()
        try handler.perform([faceRequest])
        let face = bestFace(from: faceRequest.results ?? [], mask: mask)
        let msVision = Date().timeIntervalSince(visionStart) * 1000 - msEvidence

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
        return FrameFocus(
            mask: mask, face: face, tiers: tiers, subjectPixels: subjectPixels, person: person, props: props,
            others: others, difference: difference, warpedPlate: lastWarpedPlate, pose: pose, evidence: evidence,
            msVision: msVision, msEvidence: msEvidence)
    }

    /// Rows a held prop may occupy: below the head (nothing is carried above
    /// the shoulders) down to a little under the feet (the band).
    private func verticalBand(person: [UInt8]) -> (minY: Int, maxY: Int) {
        var top = height, bottom = -1
        for y in 0..<height {
            let row = y * width
            var any = false
            for x in 0..<width where person[row + x] != 0 { any = true; break }
            if any {
                if y < top { top = y }
                bottom = y
            }
        }
        guard bottom >= top else { return (0, height - 1) }
        let span = Double(bottom - top)
        return (max(0, top + Int(span * params.headExclusion)), min(height - 1, bottom + Int(span * params.footSlack)))
    }

    /// Registers the frame to the plate and returns the tolerant difference
    /// plus the plate warped into this frame.
    private func plateDifference(rgba: [UInt8], mask: [UInt8], plate: BackgroundPlate) throws -> ([UInt8], [UInt8]?) {
        let count = width * height
        guard let registrar else {
            let diff = plate.difference(rgba: rgba)
            lastDifference = diff
            lastWarpedPlate = nil
            return (diff, nil)
        }
        // Everything that moves — the person and last frame's props, both
        // dilated — is blanked so only the static background drives the estimate.
        var blank = mask.map { $0 > 96 ? UInt8(1) : 0 }
        if let previousProps {
            for index in 0..<count where previousProps[index] != 0 { blank[index] = 1 }
        }
        for _ in 0..<5 { blank = Morphology.dilate(blank, width: width, height: height) }
        let plateRGBA = plate.rgba
        let strip = try registrar.coarseTranslation(rgba: rgba, blank: blank)
        let coarse = strip ?? lastHomography
        let guess = try registrar.warp(rgba: plateRGBA, by: coarse.inverse)
        let (homography, accepted) = try registrar.homography(rgba: rgba, blank: blank, fill: guess)
        previousHomography = lastHomography
        lastHomography = accepted ? homography : coarse
        lastRegistrationAccepted = accepted
        if !accepted { rejectedRegistrations += 1 }
        var warped = accepted ? try registrar.warp(rgba: plateRGBA, by: homography.inverse) : guess
        let shift = Morphology.refineTranslation(
            rgba: rgba, warpedPlate: warped, exclude: blank, width: width, height: height, range: params.refineRange)
        lastRefinement = shift
        if shift.dx != 0 || shift.dy != 0 {
            warped = Morphology.shifted(warped, width: width, height: height, dx: shift.dx, dy: shift.dy)
        }
        let diff = BackgroundPlate.tolerantDifference(
            rgba: rgba, warpedPlate: warped, width: width, height: height, radius: params.plateTolerance)
        lastWarpedPlate = warped
        lastDifference = diff
        return (diff, warped)
    }

    /// The shipped threshold-and-morphology pipeline, kept as the baseline.
    // swiftlint:disable:next function_body_length
    private func legacyProps(
        rgba: [UInt8], difference: [UInt8], warpedPlate: [UInt8]?, person: [UInt8], others: [UInt8]?,
        minY: Int, maxY: Int
    ) throws -> [UInt8] {
        let count = width * height
        let threshold = UInt8(clamping: Int(params.plateThreshold))
        var blob = [UInt8](repeating: 0, count: count)
        var weak = [UInt8](repeating: 0, count: count)
        let entryThreshold = UInt8(clamping: Int(Double(threshold) * (1 + params.entryMargin)))
        let weakThreshold = UInt8(clamping: Int(Double(threshold) * params.weakRatio))
        for index in 0..<count {
            var isShadow = false
            if let plate = warpedPlate, plate[index * 4 + 3] != 0 {
                let fr = Int(rgba[index * 4]), fg = Int(rgba[index * 4 + 1]), fb = Int(rgba[index * 4 + 2])
                let pr = Int(plate[index * 4]), pg = Int(plate[index * 4 + 1]), pb = Int(plate[index * 4 + 2])
                let fSum = fr + fg + fb
                let pSum = pr + pg + pb
                if fSum < pSum && pSum > 30 && Double(fSum) >= Double(pSum) * params.shadowMinRatio
                    && Double(fSum) <= Double(pSum) * params.shadowMaxRatio
                {
                    let sr = fr * pSum, sg = fg * pSum, sb = fb * pSum
                    let tr = pr * fSum, tg = pg * fSum, tb = pb * fSum
                    let tolerance = Int(params.shadowChromaTolerance) * pSum
                    isShadow = abs(sr - tr) < tolerance && abs(sg - tg) < tolerance && abs(sb - tb) < tolerance
                }
            }
            let y = index / width
            if isShadow || y < minY || y > maxY { continue }
            if let others, others[index] != 0 { continue }
            if difference[index] > entryThreshold { blob[index] = 1 }
            if difference[index] > weakThreshold { weak[index] = 1 }
        }
        var cleaned = Morphology.erode(blob, width: width, height: height)
        cleaned = Morphology.dilate(cleaned, width: width, height: height)
        cleaned = Morphology.dilate(cleaned, width: width, height: height)
        var graph = cleaned
        for index in 0..<count where person[index] != 0 { graph[index] = 1 }
        let reachable = Morphology.componentsTouching(graph, anchor: person, width: width, height: height)
        var props = [UInt8](repeating: 0, count: count)
        for index in 0..<count where reachable[index] != 0 && person[index] == 0 { props[index] = 1 }
        var weakNear = weak
        for _ in 0..<2 { weakNear = Morphology.dilate(weakNear, width: width, height: height) }
        if let previousProps {
            var carried = previousProps
            for _ in 0..<max(0, params.carryDilate) { carried = Morphology.dilate(carried, width: width, height: height) }
            for index in 0..<count where carried[index] != 0 && weakNear[index] != 0 && person[index] == 0 {
                props[index] = 1
            }
        }
        var bridge = props
        for index in 0..<count where person[index] != 0 { bridge[index] = 1 }
        let connected = Morphology.componentsTouching(bridge, anchor: person, width: width, height: height)
        for index in 0..<count where connected[index] == 0 { props[index] = 0 }
        var age = [UInt8](repeating: 255, count: count)
        if let previousAge {
            var spread = previousAge
            for _ in 0..<6 { spread = Morphology.minFilter(spread, width: width, height: height) }
            for index in 0..<count { age[index] = spread[index] == 255 ? 255 : spread[index] &+ 1 }
        }
        for index in 0..<count where blob[index] != 0 { age[index] = 0 }
        let expired = Morphology.componentsTooOld(
            props, age: age, maxAge: UInt8(clamping: params.carryFrames), width: width, height: height)
        for index in 0..<count where expired[index] != 0 { props[index] = 0 }
        previousAge = age
        if let debugDump {
            let stages: [(String, [UInt8])] = [
                ("strong", blob), ("weak", weak), ("cleaned", cleaned), ("reachable", reachable), ("props", props),
            ]
            for (name, stage) in stages {
                try? writePNG(rgba: ContactSheet.grayTile(stage.map { $0 != 0 ? 255 : 0 }), width: width, height: height,
                              to: debugDump.directory.appendingPathComponent("\(debugDump.prefix)-\(name).png"))
            }
            self.debugDump = nil
        }
        return Morphology.fillHoles(props, keepOpen: person, width: width, height: height)
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
