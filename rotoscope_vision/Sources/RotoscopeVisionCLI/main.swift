import AVFoundation
import Foundation
import RotoscopeVisionCore
import simd

/// rotoscope-vision — run the best-feature rotoscope over a movie, with Apple
/// Vision supplying the focus tiers (person/subject mask + eye landmarks) and
/// lifting the subject off the background.
///
///   rotoscope-vision IMG_0974.mov [--out-dir DIR] [--params p.json] [--evidence legacy|soft]
///       [--subject person|held|foreground|people|none] [--keep-background]
///       [--metrics DIR] [--keyframes 0,30,60] [--baseline summary.json] [--objective o.json]
///       [--width W] [--matte white|black] [--max-frames N] [--stills DIR] [--stills-every N]
///       [--no-mov] [--no-audio] [--no-registration] [--no-flow] [--verbose] [--dump-params]
///
/// Writes `<name>-rotoscope.mov` (ProRes 4444 with alpha) and
/// `<name>-rotoscope-preview.mp4` (H.264 over the matte), both with the source
/// audio passed through. With `--metrics DIR` it also writes per-frame
/// `metrics.jsonl`, `summary.json`, a `contact.png` of the keyframes, and a
/// comparison against `--baseline`.

struct Arguments {
    var input: URL
    var outDir: URL
    var width: Int?
    var params = Params()
    var subject: SubjectMode = .person
    var keepBackground = false
    var matte: (red: UInt8, green: UInt8, blue: UInt8) = (255, 255, 255)
    var maxFrames: Int?
    var stillsDir: URL?
    var stillsEvery = 30
    var skipMov = false
    var skipAudio = false
    var noRegistration = false
    var noFlow = false
    var verbose = false
    var metricsDir: URL?
    var keyframes: [Int] = [0, 30, 60, 90, 120, 150, 180]
    var baseline: URL?
    var objective: URL?
    var dumpParams = false
    var twoPass = false
    var propagateOnly = false
    var scratchDir: URL?

    static func parse(_ argv: [String]) throws -> Arguments {
        var args = Array(argv.dropFirst())
        if args.first == "--dump-params" {
            var parsed = Arguments(input: URL(fileURLWithPath: "/dev/null"), outDir: URL(fileURLWithPath: "."))
            parsed.dumpParams = true
            return parsed
        }
        guard let first = args.first, !first.hasPrefix("--") else {
            throw UsageError("usage: rotoscope-vision <input.mov> [options]   (or --dump-params)")
        }
        args.removeFirst()
        let input = URL(fileURLWithPath: first)
        var parsed = Arguments(input: input, outDir: input.deletingLastPathComponent())
        func value(_ flag: String) throws -> String {
            guard !args.isEmpty else { throw UsageError("\(flag) needs a value") }
            return args.removeFirst()
        }
        while !args.isEmpty {
            let flag = args.removeFirst()
            switch flag {
            case "--out-dir": parsed.outDir = URL(fileURLWithPath: try value(flag), isDirectory: true)
            case "--params": parsed.params = try Params.load(URL(fileURLWithPath: try value(flag)))
            case "--evidence": parsed.params.evidence = try value(flag)
            case "--width": parsed.width = Int(try value(flag))
            case "--budget": parsed.params.markerBudget = Int(try value(flag)) ?? parsed.params.markerBudget
            case "--blur": parsed.params.blurRadius = Int(try value(flag)) ?? parsed.params.blurRadius
            case "--face-quota": parsed.params.faceQuota = Double(try value(flag)) ?? parsed.params.faceQuota
            case "--spacing":
                let parts = try value(flag).split(separator: ",").compactMap { Double($0) }
                if parts.count == 3 {
                    parsed.params.spacingFace = parts[0]
                    parsed.params.spacingBody = parts[1]
                    parsed.params.spacingBackground = parts[2]
                }
            case "--subject":
                let raw = try value(flag)
                guard let mode = SubjectMode(rawValue: raw) else { throw UsageError("unknown --subject \(raw)") }
                parsed.subject = mode
            case "--keep-background": parsed.keepBackground = true
            case "--matte": parsed.matte = try value(flag) == "black" ? (0, 0, 0) : (255, 255, 255)
            case "--max-frames": parsed.maxFrames = Int(try value(flag))
            case "--stills": parsed.stillsDir = URL(fileURLWithPath: try value(flag), isDirectory: true)
            case "--stills-every": parsed.stillsEvery = max(1, Int(try value(flag)) ?? 30)
            case "--no-mov": parsed.skipMov = true
            case "--no-audio": parsed.skipAudio = true
            case "--gray-edges": parsed.params.colorEdges = false
            case "--plate-threshold": parsed.params.plateThreshold = Double(try value(flag)) ?? parsed.params.plateThreshold
            case "--plate-samples": parsed.params.plateSamples = max(3, Int(try value(flag)) ?? 48)
            case "--no-registration": parsed.noRegistration = true
            case "--no-flow": parsed.noFlow = true
            case "--verbose": parsed.verbose = true
            case "--metrics": parsed.metricsDir = URL(fileURLWithPath: try value(flag), isDirectory: true)
            case "--keyframes": parsed.keyframes = try value(flag).split(separator: ",").compactMap { Int($0) }
            case "--baseline": parsed.baseline = URL(fileURLWithPath: try value(flag))
            case "--objective": parsed.objective = URL(fileURLWithPath: try value(flag))
            case "--two-pass": parsed.twoPass = true
            case "--propagate-only": parsed.twoPass = true; parsed.propagateOnly = true
            case "--scratch": parsed.scratchDir = URL(fileURLWithPath: try value(flag), isDirectory: true)
            case "--propagate-max-gap": parsed.params.propagateMaxGap = Int(try value(flag)) ?? parsed.params.propagateMaxGap
            default: throw UsageError("unknown option \(flag)")
            }
        }
        return parsed
    }

    var engineOptions: EngineOptions {
        var options = EngineOptions()
        options.markerBudget = params.markerBudget
        options.blurRadius = params.blurRadius
        options.quotas = TierValues(face: params.faceQuota, body: 1 - params.faceQuota, background: keepBackground ? 0.1 : 0)
        options.spacing = TierValues(face: params.spacingFace, body: params.spacingBody, background: params.spacingBackground)
        options.colorEdges = params.colorEdges
        return options
    }
}

struct UsageError: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }
}

func log(_ message: String) {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
}

do {
    let args = try Arguments.parse(CommandLine.arguments)
    if args.dumpParams {
        print(try Params().json())
        exit(0)
    }
    let started = Date()
    let reader = try await FrameReader(url: args.input, targetWidth: args.width)
    let width = reader.width
    let height = reader.height
    let count = width * height
    log("input \(args.input.lastPathComponent): \(width)×\(height) @ \(reader.frameRate) fps, \(String(format: "%.2f", reader.duration)) s (~\(reader.estimatedFrames) frames)")
    log("subject \(args.subject.rawValue), evidence \(args.params.evidence), budget \(args.params.markerBudget), background \(args.keepBackground ? "kept" : "removed")")

    let stem = args.input.deletingPathExtension().lastPathComponent
    try FileManager.default.createDirectory(at: args.outDir, withIntermediateDirectories: true)
    if let stillsDir = args.stillsDir {
        try FileManager.default.createDirectory(at: stillsDir, withIntermediateDirectories: true)
    }
    if let metricsDir = args.metricsDir {
        try FileManager.default.createDirectory(at: metricsDir, withIntermediateDirectories: true)
        try args.params.json().write(to: metricsDir.appendingPathComponent("params.json"), atomically: true, encoding: .utf8)
    }
    // Two-pass scratch dir (default under the metrics/out dir; git-ignored).
    let scratchDir: URL? = args.twoPass
        ? (args.scratchDir ?? (args.metricsDir ?? args.outDir).appendingPathComponent("scratch", isDirectory: true))
        : args.scratchDir
    if let scratchDir {
        try FileManager.default.createDirectory(at: scratchDir, withIntermediateDirectories: true)
    }
    // Propagate-only: reuse an existing scratch store (pass 1 is identical for
    // every maxGap) and just run pass 2/3.
    if args.propagateOnly, let scratchDir, let metricsDir = args.metricsDir {
        let jsons = (try? FileManager.default.contentsOfDirectory(atPath: scratchDir.path).filter { $0.hasSuffix(".json") }) ?? []
        let out = metricsDir.appendingPathComponent(String(format: "metrics_prop_%d.jsonl", args.params.propagateMaxGap))
        try Propagate.run(scratchDir: scratchDir, out: out, frames: jsons.count, params: args.params,
                          stillsDir: args.stillsDir, stillsEvery: args.stillsEvery, log: { log($0) })
        exit(0)
    }
    let hasAudio = reader.audioTrack != nil && !args.skipAudio
    let audioFormat = hasAudio ? reader.audioFormat : nil
    var writers: [FrameWriter] = []
    if !args.skipMov {
        writers.append(
            try FrameWriter(
                url: args.outDir.appendingPathComponent("\(stem)-rotoscope.mov"), width: width, height: height,
                codec: .proRes4444, audioFormat: audioFormat))
    }
    writers.append(
        try FrameWriter(
            url: args.outDir.appendingPathComponent("\(stem)-rotoscope-preview.mp4"), width: width,
            height: height, codec: .h264(matte: args.matte), audioFormat: audioFormat))

    let analyzer = FocusAnalyzer(width: width, height: height, mode: args.subject, params: args.params)
    if !args.noFlow {
        analyzer.flow = OpticalFlow(width: width, height: height, accuracy: args.params.flowAccuracy)
    }
    if args.subject == .held {
        // Background plate for a handheld-but-still shot: every sampled frame is
        // registered to the first frame (subject blanked), warped into that
        // reference space, and the per-pixel median of the aligned samples with
        // the person cut out is the clean plate.
        let plateStarted = Date()
        let sampler = try await FrameReader(url: args.input, targetWidth: args.width)
        try sampler.start()
        let stride = max(1, sampler.estimatedFrames / args.params.plateSamples)
        let registrar = args.noRegistration ? nil : FrameRegistrar(width: width, height: height)
        registrar?.stripHeight = args.params.stripHeight
        var samples: [[UInt8]] = []
        var sampled = 0
        var rejectedSamples = 0
        while let (buffer, _) = try sampler.next() {
            defer { sampled += 1 }
            if let max = args.maxFrames, sampled >= max * 4 { break }
            if sampled % stride != 0 { continue }
            var rgba = rgbaBytes(from: buffer)
            let person = try analyzer.personMask(buffer)
            var cut = person.map { $0 > 32 ? UInt8(1) : 0 }
            for _ in 0..<8 { cut = Morphology.dilate(cut, width: width, height: height) }
            for index in 0..<count where cut[index] != 0 { rgba[index * 4 + 3] = 0 }
            guard let registrar else {
                samples.append(rgba)
                continue
            }
            if !registrar.hasReference {
                try registrar.setReference(rgba: rgba, blank: cut)
                samples.append(rgba)
                continue
            }
            registrar.resetContinuity()
            registrar.maxJump = .greatestFiniteMagnitude
            var fill = registrar.referencePixels
            if let coarse = try registrar.coarseTranslation(rgba: rgba, blank: cut), let pixels = fill {
                fill = try registrar.warp(rgba: pixels, by: coarse.inverse)
            }
            let (homography, accepted) = try registrar.homography(rgba: rgba, blank: cut, fill: fill)
            if accepted { samples.append(try registrar.warp(rgba: rgba, by: homography)) } else { rejectedSamples += 1 }
        }
        registrar?.resetContinuity()
        registrar?.maxJump = Float(args.params.maxJump)
        analyzer.plate = BackgroundPlate.median(width: width, height: height, samples: samples)
        analyzer.registrar = registrar
        log(String(format: "background plate: median of %d %@frames (every %d, %d rejected) in %.1fs", samples.count,
                   registrar == nil ? "" : "registered ", stride, rejectedSamples, Date().timeIntervalSince(plateStarted)))
        if let stillsDir = args.stillsDir, let plate = analyzer.plate {
            try writePNG(rgba: plate.rgba, width: width, height: height, to: stillsDir.appendingPathComponent("plate.png"))
        }
    }

    // Audio is interleaved with video as we go: AVAssetWriter holds a track
    // input once it runs ahead of a sibling track, so feeding audio only at the
    // end would deadlock the video input.
    let audio = hasAudio ? try reader.audioTrack.map { try AudioReader(asset: reader.asset, track: $0) } : nil
    var pendingAudio = audio?.next()
    var audioSamples = 0
    let audioLead = CMTime(seconds: 0.5, preferredTimescale: 600)
    func pumpAudio(upTo time: CMTime?, wait: Bool) throws {
        while let sample = pendingAudio {
            if let time, CMSampleBufferGetPresentationTimeStamp(sample) > time + audioLead { return }
            var accepted = true
            for writer in writers where try !writer.appendAudio(sample, wait: wait) { accepted = false }
            if !accepted { return }
            audioSamples += 1
            pendingAudio = audio?.next()
        }
        for writer in writers { writer.finishAudio() }
    }
    if pendingAudio == nil { for writer in writers { writer.finishAudio() } }

    try reader.start()
    var frameIndex = 0
    var sessionStarted = false
    var facesSeen = 0
    var emptyMasks = 0
    var metrics: [FrameMetrics] = []
    var metricsHandle: FileHandle?
    if let metricsDir = args.metricsDir {
        let url = metricsDir.appendingPathComponent("metrics.jsonl")
        FileManager.default.createFile(atPath: url.path, contents: nil)
        metricsHandle = try FileHandle(forWritingTo: url)
    }
    let keyframeSet = Set(args.keyframes)
    var contactRows: [[[UInt8]]] = []
    let tileFactor = 4
    // State for temporal metrics.
    var previousMask: [UInt8]?
    var previousProps: [UInt8]?
    var previousPaint: [UInt8]?
    var previousMarkers: [Int] = []
    var previousArea = 0
    var previousDiscRadius: Double?
    var previousHomographyTx: Float = 0, previousHomographyTy: Float = 0
    let metricsEncoder = JSONEncoder()

    while let (buffer, time) = try reader.next() {
        if let max = args.maxFrames, frameIndex >= max { break }
        let frameStart = Date()
        if !sessionStarted {
            for writer in writers { try writer.start(at: time) }
            sessionStarted = true
        }
        let rgba = rgbaBytes(from: buffer)
        if let stillsDir = args.stillsDir, frameIndex % args.stillsEvery == 0 {
            analyzer.debugDump = (stillsDir, String(format: "%04d", frameIndex))
        }
        let focus = try analyzer.analyze(buffer)
        if focus.face != nil { facesSeen += 1 }
        if focus.subjectPixels == 0 { emptyMasks += 1 }
        let tiers = focus.subjectPixels == 0 && !args.keepBackground
            ? [UInt8](repeating: FocusTier.body.rawValue, count: count)
            : focus.tiers
        let alpha = focus.subjectPixels == 0 ? nil : focus.mask
        var options = args.engineOptions
        if focus.face == nil {
            options.quotas = TierValues(face: 0, body: options.quotas.face + options.quotas.body, background: options.quotas.background)
        }
        // Markers from last frame, moved by flow, seed this frame's basins.
        var seeds: [Int] = []
        if args.params.propagateMarkers, let flow = analyzer.flow, flow.available, !previousMarkers.isEmpty {
            seeds = flow.advance(indices: previousMarkers)
        }
        let paintStart = Date()
        let frame = Engine.process(
            rgba: rgba, width: width, height: height, tiers: tiers, alpha: alpha,
            removeBackground: !args.keepBackground, options: options, seeds: seeds)
        let msPaint = Date().timeIntervalSince(paintStart) * 1000
        do {
            for writer in writers { try writer.append(rgba: frame.rgba, at: time) }
        } catch {
            log("video append failed at frame \(frameIndex) t=\(CMTimeGetSeconds(time))")
            throw error
        }
        try pumpAudio(upTo: time, wait: false)

        // ---------------- metrics ----------------
        if args.metricsDir != nil {
            let mask = focus.mask.map { $0 > 127 ? UInt8(1) : 0 }
            let flow = analyzer.flow
            let flowReady = (flow?.available ?? false) && frameIndex > 0
            var m = FrameMetrics(frame: frameIndex, time: CMTimeGetSeconds(time))
            m.poseFound = focus.pose == nil ? 0 : 1
            m.paintRegionCount = frame.regionCount
            m.markerCount = frame.markers.indices.count
            m.msVision = focus.msVision
            m.msEvidence = focus.msEvidence
            m.msPaint = msPaint
            // Registration & plate
            if let diff = focus.difference {
                var include = [UInt8](repeating: 1, count: count)
                var dilated = mask
                for index in 0..<count where focus.props[index] != 0 { dilated[index] = 1 }
                for _ in 0..<6 { dilated = Morphology.dilate(dilated, width: width, height: height) }
                for index in 0..<count where dilated[index] != 0 || (focus.others?[index] ?? 0) != 0 { include[index] = 0 }
                let residual = MetricsMath.backgroundResidual(difference: diff, include: include, threshold: Int(args.params.plateThreshold))
                m.bgResidualMedian = residual.median
                m.bgFalseRate = residual.falseRate
                m.regAccepted = analyzer.lastRegistrationAccepted ? 1 : 0
                m.regRefinePx = Double(abs(analyzer.lastRefinement.dx) + abs(analyzer.lastRefinement.dy))
                let h = analyzer.lastHomography
                if frameIndex > 0 {
                    m.regJumpPx = Double(max(abs(h.columns.2.x - previousHomographyTx), abs(h.columns.2.y - previousHomographyTy)))
                }
                previousHomographyTx = h.columns.2.x
                previousHomographyTy = h.columns.2.y
            }
            // Mask
            let (_, ratio, area) = MetricsMath.boundaryRatio(mask, width: width, height: height)
            m.maskArea = area
            m.maskBoundaryRatio = ratio
            m.maskComponents = MetricsMath.components(mask, width: width, height: height)
            m.maskHoles = MetricsMath.holes(mask, width: width, height: height)
            var soft = 0
            for index in 0..<count where focus.mask[index] > 16 && focus.mask[index] < 240 { soft += 1 }
            m.maskSoftBand = area > 0 ? Double(soft) / Double(area) : 0
            if previousArea > 0 { m.maskAreaDelta = Double(area - previousArea) / Double(previousArea) }
            previousArea = area
            if flowReady, let flow, let previousMask {
                let warped = flow.warp(previousMask.map { $0 != 0 ? UInt8(255) : 0 }, fill: 0)
                m.maskTemporalIoU = OpticalFlow.iou(warped, mask.map { $0 != 0 ? UInt8(255) : 0 })
                m.flowMeanPx = flow.meanMagnitude(in: mask)
            }
            // Props
            let props = focus.props
            let (_, _, propArea) = MetricsMath.boundaryRatio(props, width: width, height: height)
            m.propArea = propArea
            m.propComponents = MetricsMath.components(props, width: width, height: height)
            if flowReady, let flow, let previousProps {
                let warped = flow.warp(previousProps, fill: 0)
                let flicker = MetricsMath.propFlicker(current: props, warpedPrevious: warped, width: width, height: height)
                m.propFlicker = flicker.events
                m.propTrackedArea = flicker.trackedAreas
            }
            if let evidence = focus.evidence {
                m.discCount = evidence.discs.count
                m.discRadii = evidence.discs.map { $0.radius }
                if let r = evidence.discs.map({ $0.radius }).max() {
                    if let previousDiscRadius { m.discRadiusDelta = abs(r - previousDiscRadius) }
                    previousDiscRadius = r
                }
            }
            if let segment = focus.pose?.barSegment,
                let fit = MetricsMath.barLine(props: props, segment: segment, width: width, height: height)
            {
                m.barLineResidual = fit.residual
                m.barLineLength = fit.length
                m.poseBarAgreement = fit.agreement
            }
            // Shadow
            if let warped = focus.warpedPlate {
                m.shadowLikeInProps = MetricsMath.shadowLike(props: props, rgba: rgba, warpedPlate: warped, params: args.params)
            }
            if let floorY = focus.pose?.floorY {
                m.floorContactLeak = MetricsMath.floorLeak(props: props, floorY: floorY, width: width, height: height)
            }
            // Presence of each held object (truth proxy; evaluation only)
            let presence = Presence.evaluate(
                rgba: rgba, difference: focus.difference, person: focus.person, mask: mask, pose: focus.pose,
                width: width, height: height, threshold: Int(args.params.plateThreshold))
            m.bandTruth = presence.bandTruth
            m.bandRecall = presence.bandRecall
            m.plateTruthLeft = presence.plateTruthLeft
            m.plateTruthRight = presence.plateTruthRight
            m.plateRecallLeft = presence.plateRecallLeft
            m.plateRecallRight = presence.plateRecallRight
            // Paint
            m.paintPSNR = MetricsMath.psnr(painted: frame.rgba, source: rgba, mask: mask)
            m.paintBoundaryRecall = MetricsMath.boundaryRecall(labels: frame.labels, gray: frame.gray, mask: mask, width: width, height: height, tau: 48)
            var sizes = [Int](repeating: 0, count: frame.regionCount + 1)
            for index in 0..<count { sizes[Int(frame.labels[index])] += 1 }
            let sorted = sizes.dropFirst().filter { $0 > 0 }.sorted()
            m.paintRegionP50 = sorted.isEmpty ? 0 : sorted[sorted.count / 2]
            if flowReady, let flow, let previousPaint {
                let warped = flow.warpRGBA(previousPaint)
                m.paintTemporalDelta = MetricsMath.temporalDelta(current: frame.rgba, warpedPrevious: warped, mask: mask)
                m.markerPersistence = MetricsMath.markerPersistence(advanced: seeds, current: frame.markers.indices, width: width, height: height)
            }
            if let tr = focus.trackResult {
                let ts = tr.trackerStats, cs = tr.classifierStats
                m.trackCount = ts.live
                m.trackNew = ts.new
                m.trackLost = ts.newlyLost
                m.trackRevived = ts.revived
                m.trackFBError = Double(ts.medianFB)
                m.trackLabelFlips = ts.live > 0 ? Double(cs.labelFlips) / Double(ts.live) : 0
                m.staticTrackCount = cs.staticCount
                m.subjectTrackCount = cs.subjectCount
                m.attachedTrackCount = cs.attachedCount
                m.foreignTrackCount = cs.foreignCount
                m.staticPlateAgreement = Double(cs.staticPlateAgreement)
                if let fit = cs.fit {
                    m.bgFitResidual = Double(fit.residual)
                    m.bgInlierFrac = Double(fit.inlierFrac)
                    // Disagreement between the track consensus and the registrar at the frame centre.
                    let c = SIMD2<Float>(Float(width) / 2, Float(height) / 2)
                    let h = analyzer.lastHomography, hp = analyzer.previousHomography
                    // registrar prediction: Ĥ_t⁻¹ · Ĥ_{t−1} · c  (translation-only approximation)
                    let reg = SIMD2<Float>(c.x + hp.columns.2.x - h.columns.2.x, c.y + hp.columns.2.y - h.columns.2.y)
                    m.regTrackDisagreePx = Double(simd_distance(fit.transform.apply(c), reg))
                }
                m.msTracker = tr.msTracker
                m.msObjects = tr.msObjects
                m.objectCount = tr.reports.count
                m.objectAttached = tr.reports.filter { $0.status == "attached" }.count
                m.objectOccluded = tr.reports.filter { $0.status == "occluded" }.count
                m.objectIdChurn = tr.clusterStats.newIDs
                m.objectMerges = tr.clusterStats.merges
                m.objectSplits = tr.clusterStats.splits
                m.objGeomResidual = tr.reports.compactMap { $0.geomResidual }
                m.objRigidity = tr.reports.compactMap { $0.rigidity }
                m.objInlierFrac = tr.reports.compactMap { $0.inlierFrac }
                m.objPhotoResidual = tr.reports.compactMap { $0.photoResidual }
                m.objColorDrift = tr.reports.compactMap { $0.colorDrift }
                m.objLabelFlips = tr.reports.map { Double($0.labelFlips) }
                m.objLiveTracks = tr.reports.map { Double($0.liveTracks) }
                m.objArea = tr.reports.map { Double($0.area) }
                m.objAreaDelta = tr.reports.compactMap { $0.areaDelta }
                m.objVisible = tr.reports.map { Double($0.visible) }
                let attached = tr.reports.filter { $0.status == "attached" || $0.status == "occluded" }
                if !attached.isEmpty {
                    m.objPersistence = Double(attached.filter { $0.visible == 1 }.count) / Double(attached.count)
                }
            }
            m.msTotal = Date().timeIntervalSince(frameStart) * 1000
            metrics.append(m)
            if let handle = metricsHandle {
                let data = try metricsEncoder.encode(m)
                handle.write(data)
                handle.write("\n".data(using: .utf8)!)
            }
            // Pass-1 scratch store for the two-pass pipeline (git-ignored). The
            // pass-1 output above is unchanged; this only records what passes
            // 2/3 need so they never re-run Vision. Disabled propagation
            // (propagateMaxGap 0) leaves the single-pass output as the result.
            if let scratchDir {
                let signed = analyzer.flow?.signedField() ?? ([], [], false)
                func fixed(_ a: [Float]) -> [Int16] { a.map { Int16(max(-32000, min(32000, ($0 * 16).rounded()))) } }
                let side = Scratch.Side(
                    frame: frameIndex, width: width, height: height,
                    signX: 1, signY: 1, flowAvailable: signed.2, subjectPixels: focus.subjectPixels,
                    faceCenterX: focus.face?.centerX, faceCenterY: focus.face?.centerY,
                    faceRadiusX: focus.face?.radiusX, faceRadiusY: focus.face?.radiusY,
                    poseFloorY: focus.pose?.floorY,
                    barX0: focus.pose?.barSegment?.x0, barY0: focus.pose?.barSegment?.y0,
                    barX1: focus.pose?.barSegment?.x1, barY1: focus.pose?.barSegment?.y1,
                    metrics: m)
                try Scratch.write(scratchDir, frame: frameIndex, data: Scratch.Frame(
                    mask: focus.mask, person: focus.person, props: focus.props.map { $0 != 0 ? 255 : 0 },
                    difference: focus.difference ?? [UInt8](repeating: 0, count: count),
                    others: focus.others ?? [UInt8](repeating: 0, count: count),
                    warpedPlate: focus.warpedPlate, rgba: rgba,
                    dx: signed.2 ? fixed(signed.0) : [Int16](repeating: 0, count: count),
                    dy: signed.2 ? fixed(signed.1) : [Int16](repeating: 0, count: count),
                    side: side))
            }
            previousMask = mask
            previousProps = props
            previousPaint = frame.rgba
            // Contact sheet row for keyframes: source | focus | evidence | paint.
            if keyframeSet.contains(frameIndex) {
                let evidenceTile: [UInt8]
                if let tr = focus.trackResult {
                    evidenceTile = tr.overlay
                } else if let e = focus.evidence {
                    evidenceTile = ContactSheet.heatTile(e.posterior)
                } else if let diff = focus.difference {
                    evidenceTile = ContactSheet.grayTile(diff)
                } else {
                    evidenceTile = ContactSheet.grayTile(focus.mask)
                }
                let overlay = debugOverlay(rgba: rgba, width: width, height: height, focus: focus, markers: frame.markers, tiers: tiers)
                let tiles = [rgba, overlay, evidenceTile, composite(rgba: frame.rgba, over: args.matte)].map {
                    ContactSheet.downscale($0, width: width, height: height, factor: tileFactor).rgba
                }
                contactRows.append(tiles)
            }
        }
        previousMarkers = frame.markers.indices

        if let stillsDir = args.stillsDir, frameIndex % args.stillsEvery == 0 {
            let tag = String(format: "%04d", frameIndex)
            try writePNG(rgba: composite(rgba: frame.rgba, over: args.matte), width: width, height: height,
                         to: stillsDir.appendingPathComponent("\(tag)-rotoscope.png"))
            try writePNG(
                rgba: debugOverlay(rgba: rgba, width: width, height: height, focus: focus, markers: frame.markers, tiers: tiers),
                width: width, height: height, to: stillsDir.appendingPathComponent("\(tag)-focus.png"))
            // Presence truth proxy: R = band truth, G = plate truth, B = mask, dim source under it.
            do {
                let personMask = focus.person
                let band = Presence.bandTruth(rgba: rgba, person: personMask, width: width, height: height)
                let plates = focus.difference.flatMap {
                    Presence.plateTruth(rgba: rgba, difference: $0, person: personMask, bar: focus.pose?.barSegment,
                                        width: width, height: height, threshold: Int(args.params.plateThreshold))
                }
                var out = rgba
                for i in 0..<count {
                    let o = i * 4
                    out[o] = UInt8(Int(out[o]) / 3); out[o + 1] = UInt8(Int(out[o + 1]) / 3); out[o + 2] = UInt8(Int(out[o + 2]) / 3)
                    if band[i] != 0 { out[o] = 255 }
                    if let plates, plates.left[i] != 0 || plates.right[i] != 0 { out[o + 1] = 255 }
                    if focus.mask[i] > 127 { out[o + 2] = 255 }
                    out[o + 3] = 255
                }
                try writePNG(rgba: out, width: width, height: height, to: stillsDir.appendingPathComponent("\(tag)-presence.png"))
            }
            if let tr = focus.trackResult {
                try writePNG(rgba: tr.overlay, width: width, height: height,
                             to: stillsDir.appendingPathComponent("\(tag)-tracks.png"))
                if let tracker = analyzer.tracks?.tracker {
                    var csv = "id,x,y,label,status,age,static,plate,fb,ssd,object\n"
                    for t in tracker.tracks {
                        csv += "\(t.id),\(Int(t.current.x)),\(Int(t.current.y)),\(t.label),\(t.status),\(t.age),"
                        csv += String(format: "%.2f,%.2f,%.2f,%.1f,", t.staticScore, t.plateAgreement, t.fbError, t.ssd)
                        csv += "\(t.objectID.map(String.init) ?? "")\n"
                    }
                    try csv.write(to: stillsDir.appendingPathComponent("\(tag)-tracks.csv"), atomically: true, encoding: .utf8)
                    let encoder = JSONEncoder()
                    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
                    try encoder.encode(tr.reports).write(to: stillsDir.appendingPathComponent("\(tag)-objects.json"))
                }
            }
            if let evidence = focus.evidence {
                try writePNG(rgba: ContactSheet.heatTile(evidence.posterior), width: width, height: height,
                             to: stillsDir.appendingPathComponent("\(tag)-posterior.png"))
                try writePNG(rgba: ContactSheet.heatTile(evidence.pStruct), width: width, height: height,
                             to: stillsDir.appendingPathComponent("\(tag)-struct.png"))
                try writePNG(rgba: ContactSheet.heatTile(evidence.pShadow), width: width, height: height,
                             to: stillsDir.appendingPathComponent("\(tag)-shadow.png"))
            }
            if let diff = focus.difference {
                try writePNG(rgba: ContactSheet.grayTile(diff), width: width, height: height,
                             to: stillsDir.appendingPathComponent("\(tag)-diff.png"))
            }
        }
        if args.verbose, args.subject == .held {
            let h = analyzer.lastHomography
            log(String(format: "  frame %d homography tx=%.1f ty=%.1f %@ refine=%d,%d props=%d discs=%d pose=%@", frameIndex,
                       h.columns.2.x, h.columns.2.y, analyzer.lastRegistrationAccepted ? "ok" : "REJECTED",
                       analyzer.lastRefinement.dx, analyzer.lastRefinement.dy,
                       focus.props.reduce(0) { $0 + Int($1) }, focus.evidence?.discs.count ?? 0,
                       focus.pose?.barSegment == nil ? "none" : "bar"))
        }
        frameIndex += 1
        if frameIndex % 10 == 0 || frameIndex == 1 {
            let elapsed = Date().timeIntervalSince(started)
            log(String(format: "frame %d/%d  markers %d (face %d, body %d)  regions %d  %.1fs", frameIndex,
                       reader.estimatedFrames, frame.markers.indices.count, frame.markers.faceCount,
                       frame.markers.bodyCount, frame.regionCount, elapsed))
        }
    }
    for writer in writers { writer.finishVideo() }
    if let scratchDir {
        let bytes = Scratch.size(scratchDir)
        log(String(format: "scratch: %d frames, %.2f GB at %@", frameIndex, Double(bytes) / 1e9, scratchDir.path))
        if args.params.propagateMaxGap > 0, let metricsDir = args.metricsDir {
            let out = metricsDir.appendingPathComponent("metrics_prop.jsonl")
            try Propagate.run(scratchDir: scratchDir, out: out, frames: frameIndex, params: args.params, log: log)
        }
    }
    if sessionStarted {
        try pumpAudio(upTo: nil, wait: true)
        log("audio: \(audioSamples) sample buffers passed through")
    }
    for writer in writers { try await writer.finish() }
    metricsHandle?.closeFile()

    if let metricsDir = args.metricsDir {
        var summary = try MetricsSummary.build(from: metrics, params: args.params)
        let objective: Objective
        if let url = args.objective {
            objective = try JSONDecoder().decode(Objective.self, from: Data(contentsOf: url))
        } else {
            objective = args.params.evidence == "tracks" ? .presence : .standard
        }
        summary.objective = objective.score(summary)
        try summary.json().write(to: metricsDir.appendingPathComponent("summary.json"), atomically: true, encoding: .utf8)
        if !contactRows.isEmpty {
            let sheet = ContactSheet.assemble(rows: contactRows, tileWidth: width / tileFactor, tileHeight: height / tileFactor)
            try writePNG(rgba: sheet.rgba, width: sheet.width, height: sheet.height, to: metricsDir.appendingPathComponent("contact.png"))
        }
        let keys = ["bgResidualMedian", "bgFalseRate", "regAccepted", "maskTemporalIoU", "maskComponents", "propFlicker",
                    "propArea", "discCount", "discRadiusDelta", "poseBarAgreement", "shadowLikeInProps", "floorContactLeak",
                    "paintPSNR", "paintBoundaryRecall", "paintTemporalDelta", "markerPersistence",
                    "trackCount", "trackNew", "trackLost", "trackRevived", "trackFBError", "trackLabelFlips",
                    "bgFitResidual", "bgInlierFrac", "regTrackDisagreePx", "staticTrackCount", "subjectTrackCount",
                    "attachedTrackCount", "foreignTrackCount", "staticPlateAgreement", "objectCount", "objectAttached", "objectOccluded",
                    "objectIdChurn", "objGeomResidualMean", "objInlierFracMean", "objLiveTracksMean",
                    "objPhotoResidualMean", "objColorDriftMean", "objAreaDeltaMean", "objPersistence",
                    "msTracker", "msObjects", "msTotal"]
        log(String(format: "objective %.3f", summary.objective ?? 0))
        for key in keys {
            if let s = summary.stats[key] {
                log(String(format: "  %-22@ mean %9.4f  p95 %9.4f  max %9.4f  (n=%d)", key, s.mean, s.p95, s.max, s.count))
            }
        }
        if let baselineURL = args.baseline {
            let baseline = try JSONDecoder().decode(MetricsSummary.self, from: Data(contentsOf: baselineURL))
            let baseScore = objective.score(baseline)
            log(String(format: "baseline objective %.3f → candidate %.3f (%+.3f)", baseScore, summary.objective ?? 0, (summary.objective ?? 0) - baseScore))
            for key in keys {
                if let c = summary.stats[key], let b = baseline.stats[key] {
                    log(String(format: "  %-22@ %9.4f → %9.4f  (%+.4f)", key, b.mean, c.mean, c.mean - b.mean))
                }
            }
            let violations = objective.violations(candidate: summary, baseline: baseline)
            if violations.isEmpty {
                log("red lines: none crossed")
            } else {
                for v in violations { log("RED LINE: \(v)") }
            }
        }
    }
    let elapsed = Date().timeIntervalSince(started)
    log(String(format: "done: %d frames in %.1fs (%.2fs/frame); face found in %d frames; empty subject in %d%@",
               frameIndex, elapsed, elapsed / Double(max(1, frameIndex)), facesSeen, emptyMasks,
               args.subject == .held ? "; registration rejected in \(analyzer.rejectedRegistrations)" : ""))
    if let flow = analyzer.flow, flow.available {
        log(String(format: "optical flow sign calibration IoU %.3f", flow.lastCalibrationIoU))
    }
    for writer in writers { print(writer.url.path) }
} catch {
    log("error: \(error)")
    exit(1)
}
