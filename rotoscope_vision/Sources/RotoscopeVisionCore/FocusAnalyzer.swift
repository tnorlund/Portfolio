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
}

/// Runs the Vision requests for one frame and turns them into the engine's
/// per-pixel focus tiers: the periocular ellipse around the detected eyes is
/// `face`, the rest of the subject mask is `body`, everything else is
/// `background`.
public final class FocusAnalyzer {
    public let width: Int
    public let height: Int
    public let mode: SubjectMode
    /// Periocular ellipse radii as multiples of the inter-eye distance. The
    /// homepage portrait ellipse (radii 0.085×960 by 0.055×720 for eyes 85 px
    /// apart) works out to roughly 0.95 × and 0.46 ×.
    public var eyeRadiusX: Double = 0.95
    public var eyeRadiusY: Double = 0.46
    public var maskThreshold: UInt8 = 127
    /// Static-camera background plate for `.held`; pixels whose largest channel
    /// difference from it exceeds `plateThreshold` are candidate prop pixels.
    public var plate: BackgroundPlate?
    public var plateThreshold: UInt8 = 48
    /// Misalignment tolerance of the plate difference, in half-resolution pixels.
    public var plateTolerance = 4
    /// Aligns each frame to the plate's reference frame before differencing.
    public var registrar: FrameRegistrar?
    /// Last frame's homography (frame → reference), for diagnostics.
    public private(set) var lastHomography = matrix_identity_float3x3
    public private(set) var lastRegistrationAccepted = true
    public private(set) var lastRefinement = (dx: 0, dy: 0)
    /// Diagnostics from the last `.held` frame: the plate warped into the frame
    /// and the plate difference, both frame-sized (RGBA / 1 byte).
    public private(set) var lastWarpedPlate: [UInt8]?
    public private(set) var lastDifference: [UInt8]?
    public private(set) var rejectedRegistrations = 0
    /// When set, `.held` dumps its intermediate masks for the next frame here
    /// (strong, weak, cleaned, reachable, props) as `<prefix>-<stage>.png`.
    public var debugDump: (directory: URL, prefix: String)?
    /// Previous frame's prop pixels (binary); blanked before registering so a
    /// moving barbell cannot pull the homography.
    private var previousProps: [UInt8]?
    private var previousAge: [UInt8]?
    /// Frames a carried prop may survive on weak differences alone.
    public var carryFrames = 90

    private let context = CIContext(options: [.cacheIntermediates: false])
    private var maskTarget: CVPixelBuffer?

    public init(width: Int, height: Int, mode: SubjectMode) {
        self.width = width
        self.height = height
        self.mode = mode
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

    public func analyze(_ pixelBuffer: CVPixelBuffer) throws -> FrameFocus {
        let count = width * height
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
            if mode == .held, let plate {
                // Everyone else in the shot: never a prop, even when the bar
                // sweeps across them.
                let everyone = VNGeneratePersonSegmentationRequest()
                everyone.qualityLevel = .accurate
                everyone.outputPixelFormat = kCVPixelFormatType_OneComponent8
                try handler.perform([everyone])
                var others: [UInt8]? = nil
                if let result = everyone.results?.first {
                    // Whole-frame person segmentation is blobby and happily
                    // labels the bar at the subject's arms as "person", so
                    // decide per connected blob: blobs touching the subject's
                    // own instance are the subject; the rest are other people.
                    let all = try maskBytes(from: result.pixelBuffer)
                    let people = all.map { $0 > 127 ? UInt8(1) : 0 }
                    let own = mask.map { $0 > 32 ? UInt8(1) : 0 }
                    let mine = Morphology.componentsTouching(people, anchor: own, width: width, height: height)
                    var binary = [UInt8](repeating: 0, count: count)
                    for index in 0..<count where people[index] != 0 && mine[index] == 0 { binary[index] = 1 }
                    for _ in 0..<3 { binary = Morphology.dilate(binary, width: width, height: height) }
                    others = binary
                }
                mask = try addHeldProps(
                    to: mask, rgba: rgbaBytes(from: pixelBuffer), plate: plate, others: others)
            }
        }

        let faceRequest = VNDetectFaceLandmarksRequest()
        try handler.perform([faceRequest])
        let face = bestFace(from: faceRequest.results ?? [], mask: mask)

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
        return FrameFocus(mask: mask, face: face, tiers: tiers, subjectPixels: subjectPixels)
    }

    /// Unions the person mask with background-plate difference blobs that touch
    /// it. The blob mask is opened (erode, dilate twice) so sensor noise and
    /// thin shadow fringes drop out before the connectivity test, and the
    /// person mask itself is part of the connectivity graph so a prop only has
    /// to touch the person somewhere.
    // swiftlint:disable:next function_body_length
    private func addHeldProps(
        to mask: [UInt8], rgba: [UInt8], plate: BackgroundPlate, others: [UInt8]?
    ) throws -> [UInt8] {
        let count = width * height
        let threshold = plateThreshold
        let difference: [UInt8]
        if let registrar {
            // Frame → reference homography; the plate (reference space) is
            // brought into this frame with the inverse. Everything that moves
            // — the person and last frame's props, both dilated — is blanked
            // first so only the static background drives the estimate.
            var blank = mask.map { $0 > 96 ? UInt8(1) : 0 }
            if let previousProps {
                for index in 0..<count where previousProps[index] != 0 { blank[index] = 1 }
            }
            for _ in 0..<5 { blank = Morphology.dilate(blank, width: width, height: height) }
            // Fill the hole with the plate aligned by an unbiased coarse
            // estimate (top-strip translation), then let the homography refine
            // on the full frame. Filling from the previous homography instead
            // would lock the estimate to yesterday's answer and drift.
            let plateRGBA = plate.rgba
            let strip = try registrar.coarseTranslation(rgba: rgba, blank: blank)
            let coarse = strip ?? lastHomography
            let guess = try registrar.warp(rgba: plateRGBA, by: coarse.inverse)
            let (homography, accepted) = try registrar.homography(rgba: rgba, blank: blank, fill: guess)
            // A rejected refinement falls back to the unbiased coarse estimate.
            lastHomography = accepted ? homography : coarse
            lastRegistrationAccepted = accepted
            if !accepted { rejectedRegistrations += 1 }
            var warped = accepted ? try registrar.warp(rgba: plateRGBA, by: homography.inverse) : guess
            // Direct photometric refinement over the visible background,
            // which corrects whatever residual the feature-based estimate
            // (or the strip fallback) left.
            let shift = Morphology.refineTranslation(
                rgba: rgba, warpedPlate: warped, exclude: blank, width: width, height: height, range: 10)
            lastRefinement = shift
            if shift.dx != 0 || shift.dy != 0 {
                warped = Morphology.shifted(warped, width: width, height: height, dx: shift.dx, dy: shift.dy)
            }
            difference = BackgroundPlate.tolerantDifference(
                rgba: rgba, warpedPlate: warped, width: width, height: height, radius: plateTolerance)
            lastWarpedPlate = warped
            lastDifference = difference
        } else {
            difference = plate.difference(rgba: rgba)
        }
        var blob = [UInt8](repeating: 0, count: count)
        var weak = [UInt8](repeating: 0, count: count)
        var person = [UInt8](repeating: 0, count: count)
        var top = height
        var bottom = -1
        // Entry is strict, staying is lenient: new props need a strong
        // difference, carried props survive on half that.
        let entryThreshold = UInt8(min(255, Int(threshold) + Int(threshold) / 6))
        let weakThreshold = threshold / 2
        for index in 0..<count {
            if mask[index] > maskThreshold {
                person[index] = 1
                let y = index / width
                if y < top { top = y }
                if y > bottom { bottom = y }
            }
            var isShadow = false
            if let plate = lastWarpedPlate, plate[index * 4 + 3] != 0 {
                // A cast shadow darkens the background without changing its
                // chroma: the frame is a uniformly scaled-down copy of the
                // plate. Never let one enter or sustain the props.
                let fr = Int(rgba[index * 4]), fg = Int(rgba[index * 4 + 1]), fb = Int(rgba[index * 4 + 2])
                let pr = Int(plate[index * 4]), pg = Int(plate[index * 4 + 1]), pb = Int(plate[index * 4 + 2])
                let fSum = fr + fg + fb
                let pSum = pr + pg + pb
                if fSum < pSum && pSum > 30 && fSum * 2 >= pSum {
                    let sr = fr * pSum, sg = fg * pSum, sb = fb * pSum
                    let tr = pr * fSum, tg = pg * fSum, tb = pb * fSum
                    let tolerance = 26 * pSum
                    isShadow = abs(sr - tr) < tolerance && abs(sg - tg) < tolerance && abs(sb - tb) < tolerance
                }
            }
            if !isShadow {
                if difference[index] > entryThreshold { blob[index] = 1 }
                if difference[index] > weakThreshold { weak[index] = 1 }
            }
        }
        // Held props live within the person's own vertical extent, below the
        // head: nothing is carried above the shoulders, and a ceiling fixture
        // at head height otherwise sneaks in through the hair. A little slack
        // below the feet keeps the band.
        if bottom >= top {
            let span = bottom - top
            let minY = max(0, top + span / 8)
            let maxY = min(height - 1, bottom + span / 12)
            for y in 0..<height where y < minY || y > maxY {
                for x in 0..<width {
                    blob[y * width + x] = 0
                    weak[y * width + x] = 0
                }
            }
        }
        if let others {
            for index in 0..<count where others[index] != 0 {
                blob[index] = 0
                weak[index] = 0
            }
        }
        // Open the strong blobs (one erosion: the tolerant difference already
        // absorbs misalignment, and a chrome bar's shaded underside in front
        // of a bright bench is only a few pixels wide), then grow them back.
        var cleaned = Morphology.erode(blob, width: width, height: height)
        cleaned = Morphology.dilate(cleaned, width: width, height: height)
        cleaned = Morphology.dilate(cleaned, width: width, height: height)
        // New prop pixels must be strongly different and reach the person
        // through strong pixels alone.
        var graph = cleaned
        for index in 0..<count where person[index] != 0 { graph[index] = 1 }
        let reachable = Morphology.componentsTouching(graph, anchor: person, width: width, height: height)
        var props = [UInt8](repeating: 0, count: count)
        for index in 0..<count where reachable[index] != 0 && person[index] == 0 { props[index] = 1 }
        // Temporal carry: what was a prop last frame stays a prop while it is
        // still at least weakly different nearby (a chrome bar in front of a
        // white bench matches in places but never everywhere), allowing a few
        // pixels of motion. Carried pixels never bridge new pixels in.
        var weakNear = weak
        for _ in 0..<2 { weakNear = Morphology.dilate(weakNear, width: width, height: height) }
        if let previousProps {
            var carried = previousProps
            for _ in 0..<4 { carried = Morphology.dilate(carried, width: width, height: height) }
            for index in 0..<count where carried[index] != 0 && weakNear[index] != 0 && person[index] == 0 {
                props[index] = 1
            }
        }
        // Drop islands: everything must touch the person, directly or through
        // other props.
        var bridge = props
        for index in 0..<count where person[index] != 0 { bridge[index] = 1 }
        let connected = Morphology.componentsTouching(bridge, anchor: person, width: width, height: height)
        for index in 0..<count where connected[index] == 0 { props[index] = 0 }
        // Expiry: a prop pixel's age is frames since it (or a neighbor) was
        // strongly different. A component whose youngest pixel is older than
        // `carryFrames` has been riding on weak differences alone for too
        // long — a leak — and is dropped.
        var age = [UInt8](repeating: 255, count: count)
        if let previousAge {
            // Min-filter the previous ages over the motion allowance.
            var spread = previousAge
            for _ in 0..<6 { spread = Morphology.minFilter(spread, width: width, height: height) }
            for index in 0..<count { age[index] = spread[index] == 255 ? 255 : spread[index] &+ 1 }
        }
        for index in 0..<count where blob[index] != 0 { age[index] = 0 }
        let expired = Morphology.componentsTooOld(props, age: age, maxAge: UInt8(clamping: carryFrames), width: width, height: height)
        for index in 0..<count where expired[index] != 0 { props[index] = 0 }
        previousAge = age
        if let debugDump {
            let stages: [(String, [UInt8])] = [
                ("strong", blob), ("weak", weak), ("cleaned", cleaned), ("reachable", reachable),
                ("props", props), ("person", person), ("others", others ?? []),
            ]
            for (name, stage) in stages where !stage.isEmpty {
                var rgba = [UInt8](repeating: 255, count: count * 4)
                for index in 0..<count where stage[index] == 0 {
                    rgba[index * 4] = 0
                    rgba[index * 4 + 1] = 0
                    rgba[index * 4 + 2] = 0
                }
                try? writePNG(rgba: rgba, width: width, height: height,
                              to: debugDump.directory.appendingPathComponent("\(debugDump.prefix)-\(name).png"))
            }
            self.debugDump = nil
        }
        props = Morphology.fillHoles(props, keepOpen: person, width: width, height: height)
        previousProps = props
        var out = mask
        for index in 0..<count where props[index] != 0 && person[index] == 0 {
            // Soft edge from the difference strength so the prop alpha is not a
            // hard cut; filled holes carry no difference and come in opaque.
            let strength = Int(difference[index])
            let alpha = cleaned[index] != 0 ? min(255, max(0, (strength - Int(threshold)) * 8 + 160)) : 255
            out[index] = max(out[index], UInt8(alpha))
        }
        return out
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
        // Resample through Core Image into an 8-bit frame-sized buffer.
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
            // Vision image points are bottom-left origin; flip to y-down.
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
