import AVFoundation
import CoreMedia
import Foundation
import RotoscopeVisionCore

/// Pass 2 + pass 3 of the two-pass pipeline: carry the non-person held-object
/// layer across gaps with the stored optical flow — forward by gather, backward
/// by scatter — gated at every step by a colour check (the origin colour, warped
/// along, must still match the actual frame) and a `propagateMaxGap` age cap.
/// Then recompute the presence and guard-rail metrics from the propagated masks
/// and write `metrics_prop.jsonl`. Nothing here re-runs Vision; frames come from
/// the pass-1 scratch. No object identity, no category prior.
enum Propagate {
    struct RenderTarget {
        var dir: URL
        var stem: String
        var matte: (red: UInt8, green: UInt8, blue: UInt8)
        var options: EngineOptions
        var removeBackground: Bool
        var keepBackground: Bool
        var skipMov: Bool
        var audioAsset: AVAsset?
        var audioTrack: AVAssetTrack?
        var audioFormat: CMFormatDescription?
    }

    @discardableResult
    static func run(scratchDir: URL, out: URL, frames n: Int, params: Params,
                    stillsDir: URL? = nil, stillsEvery: Int = 15, render: RenderTarget? = nil,
                    log: (String) -> Void) async throws -> [FrameMetrics] {
        guard n > 0 else { return [] }
        let debug = ProcessInfo.processInfo.environment["PROP_DEBUG"] != nil
        let s0 = try Scratch.read(scratchDir, frame: 0)
        let width = s0.side.width, height = s0.side.height, count = width * height
        let maxGap = params.propagateMaxGap
        let tol = Float(params.propagatePhotoTolerance)

        // Load the per-frame data the propagation needs (rgba + person + flow +
        // others), plus the pass-1 side for metric recompute inputs.
        var layer = [[UInt8]](repeating: [], count: n)   // non-person held-object label
        var person = [[UInt8]](repeating: [], count: n)
        var others = [[UInt8]](repeating: [], count: n)
        var difference = [[UInt8]](repeating: [], count: n)
        var rgba = [[UInt8]](repeating: [], count: n)
        var fdx = [[Float]](repeating: [], count: n)
        var fdy = [[Float]](repeating: [], count: n)
        var avail = [Bool](repeating: false, count: n)
        var sides = [Scratch.Side?](repeating: nil, count: n)
        for f in 0..<n {
            let sf = f == 0 ? s0 : try Scratch.read(scratchDir, frame: f)
            sides[f] = sf.side
            person[f] = sf.person
            others[f] = sf.others
            difference[f] = sf.difference
            rgba[f] = sf.rgba
            avail[f] = sf.side.flowAvailable
            fdx[f] = sf.dx.map { Float($0) / 16 }
            fdy[f] = sf.dy.map { Float($0) / 16 }
            // Layer = non-person part of the mask, person dilated by 2 so the
            // person boundary is never propagated.
            var pd = sf.person
            for _ in 0..<2 { pd = Morphology.dilate(pd, width: width, height: height) }
            var l = [UInt8](repeating: 0, count: count)
            for i in 0..<count where sf.mask[i] > 127 && pd[i] == 0 { l[i] = 1 }
            layer[f] = l
        }

        // result[f] = layer + verified propagations; age = frames since origin.
        var result = layer
        var age = layer.map { l in l.map { $0 != 0 ? Int32(0) : Int32.max } }
        // Carried origin colour per pixel (r,g,b) for the colour gate.
        func makeColor(_ f: Int) -> [UInt8] { rgba[f] }
        var color = (0..<n).map { makeColor($0) }

        let diffFloor = Int(params.plateThreshold) / 3
        // Warp a whole component (given its list of (dst-in-dstFrame, src-in-
        // srcFrame) pixel pairs) and add it to result[dstFrame] as ONE unit,
        // only if the component's MEAN colour residual (origin colour vs the
        // destination frame) is within tolerance, MOST of it still differs from
        // the background plate, it is not already ~covered, and it is inside the
        // age cap. Per-component gating keeps props whole — no per-pixel speckle.
        func commit(_ dests: [(dst: Int, src: Int)], into dstFrame: Int, from srcFrame: Int) {
            if dests.count < params.minRenderArea { return }
            var sumResid = 0.0, diffPass = 0, covered = 0, maxAge: Int32 = 0
            for (dst, src) in dests {
                let dr = abs(Int(color[srcFrame][src * 4]) - Int(rgba[dstFrame][dst * 4]))
                let dg = abs(Int(color[srcFrame][src * 4 + 1]) - Int(rgba[dstFrame][dst * 4 + 1]))
                let db = abs(Int(color[srcFrame][src * 4 + 2]) - Int(rgba[dstFrame][dst * 4 + 2]))
                sumResid += Double(max(dr, max(dg, db)))
                if Int(difference[dstFrame][dst]) >= diffFloor { diffPass += 1 }
                if result[dstFrame][dst] != 0 { covered += 1 }
                maxAge = max(maxAge, age[srcFrame][src])
            }
            let area = Double(dests.count)
            // Debug: why a large left-side component (the left plate) stops.
            if debug && dests.count > 1500 {
                var cx = 0.0; for (dst, _) in dests { cx += Double(dst % width) }; cx /= area
                if cx < Double(width) / 3 {
                    let reason: String
                    if maxAge + 1 > Int32(maxGap) { reason = "age \(maxAge + 1)>maxGap" }
                    else if sumResid / area > Double(tol) { reason = "resid" }
                    else if Double(diffPass) / area < 0.3 { reason = "diffFrac" }
                    else if Double(covered) / area >= 0.9 { reason = "covered" }
                    else { reason = "OK" }
                    log(String(format: "  left-comp %d->%d cx%.0f area%.0f meanResid%.1f diffFrac%.2f cover%.2f age%d %@",
                               srcFrame, dstFrame, cx, area, sumResid / area, Double(diffPass) / area, Double(covered) / area, maxAge + 1, reason))
                }
            }
            if maxAge + 1 > Int32(maxGap) { return }
            if sumResid / area > Double(tol) { return }
            // Some of the component must still disagree with the plate. A third
            // is enough: a dark plate's interior matches the dark rack behind it,
            // only its rim differs, but it is still the plate.
            if Double(diffPass) / area < 0.3 { return }
            if Double(covered) / area >= 0.9 { return }
            for (dst, src) in dests where result[dstFrame][dst] == 0 {
                result[dstFrame][dst] = 1
                age[dstFrame][dst] = maxAge + 1
                color[dstFrame][dst * 4] = color[srcFrame][src * 4]
                color[dstFrame][dst * 4 + 1] = color[srcFrame][src * 4 + 1]
                color[dstFrame][dst * 4 + 2] = color[srcFrame][src * 4 + 2]
            }
        }

        // Forward sweep: gather each component of result[t-1] into t (a
        // destination q in t maps back to q + flow[t](q) in t-1).
        for t in 1..<n where avail[t] {
            let dx = fdx[t], dy = fdy[t]
            let (lab, areas) = MetricsMath.labelComponents(result[t - 1], width: width, height: height)
            var members: [Int32: [(dst: Int, src: Int)]] = [:]
            for y in 0..<height {
                for x in 0..<width {
                    let q = y * width + x
                    if person[t][q] != 0 || others[t][q] != 0 { continue }
                    let sx = Int((Float(x) + dx[q]).rounded()), sy = Int((Float(y) + dy[q]).rounded())
                    if sx < 0 || sx >= width || sy < 0 || sy >= height { continue }
                    let src = sy * width + sx
                    let c = lab[src]
                    if c <= 0 || areas[Int(c)] < params.minRenderArea { continue }
                    members[c, default: []].append((q, src))
                }
            }
            for (_, dests) in members { commit(dests, into: t, from: t - 1) }
        }
        // Backward sweep: scatter each component of result[t] into t-1 (a source
        // pixel p in t maps to p + flow[t](p) in t-1).
        for t in stride(from: n - 1, through: 1, by: -1) where avail[t] {
            let dx = fdx[t], dy = fdy[t]
            let (lab, areas) = MetricsMath.labelComponents(result[t], width: width, height: height)
            var members: [Int32: [(dst: Int, src: Int)]] = [:]
            for y in 0..<height {
                for x in 0..<width {
                    let p = y * width + x
                    let c = lab[p]
                    if c <= 0 || areas[Int(c)] < params.minRenderArea { continue }
                    let dpx = Int((Float(x) + dx[p]).rounded()), dpy = Int((Float(y) + dy[p]).rounded())
                    if dpx < 0 || dpx >= width || dpy < 0 || dpy >= height { continue }
                    let d = dpy * width + dpx
                    if person[t - 1][d] != 0 || others[t - 1][d] != 0 { continue }
                    members[c, default: []].append((d, p))
                }
            }
            for (_, dests) in members { commit(dests, into: t - 1, from: t) }
        }

        // Drop propagated components below minRenderArea (per frame).
        for f in 0..<n {
            result[f] = Morphology.removeSmallComponents(result[f], width: width, height: height, minArea: params.minRenderArea)
        }

        // Pass 3: recompute the presence + guard-rail metrics from the
        // propagated masks and write metrics_prop.jsonl (and, if asked, the
        // rotoscoped video painted from the propagated masks).
        // Video-only writers; source audio is muxed in afterwards. Interleaving
        // audio during these rapid appends deadlocks the proRes muxer.
        var writers: [(w: FrameWriter, url: URL)] = []
        if let r = render {
            if !r.skipMov {
                let u = r.dir.appendingPathComponent("\(r.stem)-rotoscope.mov")
                writers.append((try FrameWriter(url: u, width: width, height: height, codec: .proRes4444, audioFormat: nil), u))
            }
            let pu = r.dir.appendingPathComponent("\(r.stem)-rotoscope-preview.mp4")
            writers.append((try FrameWriter(url: pu, width: width, height: height, codec: .h264(matte: r.matte), audioFormat: nil), pu))
        }
        var writersStarted = false
        // Flow-advanced markers seed the paint each frame, like pass 1.
        let flow = OpticalFlow(width: width, height: height, accuracy: params.flowAccuracy)
        var previousMarkers: [Int] = []
        let enc = JSONEncoder()
        var lines = Data()
        var outMetrics: [FrameMetrics] = []
        var prevProps: [UInt8]?
        for f in 0..<n {
            guard let side = sides[f] else { continue }
            let sf = try Scratch.read(scratchDir, frame: f)   // reload for difference/warpedPlate
            let props = result[f]
            var maskProp = sf.mask
            for i in 0..<count where props[i] != 0 { maskProp[i] = 255 }
            // Pass 1 binarises the mask at >127 before every mask metric; match
            // it exactly (the raw alpha's soft edge would otherwise read as many
            // phantom components and over-cover the truth).
            let maskBin = maskProp.map { $0 > 127 ? UInt8(1) : 0 }
            var m = side.metrics
            m.maskComponents = MetricsMath.components(maskBin, width: width, height: height)
            let (_, _, propArea) = MetricsMath.boundaryRatio(props.map { $0 != 0 ? 255 : 0 }, width: width, height: height)
            m.propArea = propArea
            m.propComponents = MetricsMath.components(props, width: width, height: height)
            // Presence recall on the propagated mask.
            let band = Presence.bandTruth(rgba: sf.rgba, person: sf.person, width: width, height: height)
            let b = Presence.recall(truth: band, mask: maskBin, minimum: 300)
            m.bandTruth = b.count; m.bandRecall = b.recall
            var bar: BodyPose.Segment? = nil
            if let x0 = side.barX0, let y0 = side.barY0, let x1 = side.barX1, let y1 = side.barY1 {
                bar = BodyPose.Segment(x0: x0, y0: y0, x1: x1, y1: y1)
            }
            if let plates = Presence.plateTruth(rgba: sf.rgba, difference: sf.difference, person: sf.person, bar: bar,
                                                width: width, height: height, threshold: Int(params.plateThreshold)) {
                let l = Presence.recall(truth: plates.left, mask: maskBin, minimum: 800)
                let r = Presence.recall(truth: plates.right, mask: maskBin, minimum: 800)
                m.plateTruthLeft = l.count; m.plateRecallLeft = l.recall
                m.plateTruthRight = r.count; m.plateRecallRight = r.recall
            } else {
                m.plateRecallLeft = nil; m.plateRecallRight = nil
            }
            // Guard rails.
            var include = [UInt8](repeating: 1, count: count)
            var dil = maskProp.map { $0 > 127 ? UInt8(1) : 0 }
            for i in 0..<count where props[i] != 0 { dil[i] = 1 }
            for _ in 0..<6 { dil = Morphology.dilate(dil, width: width, height: height) }
            for i in 0..<count where dil[i] != 0 || sf.others[i] != 0 { include[i] = 0 }
            let resid = MetricsMath.backgroundResidual(difference: sf.difference, include: include, threshold: Int(params.plateThreshold))
            m.bgFalseRate = resid.falseRate
            if let wp = sf.warpedPlate {
                m.shadowLikeInProps = MetricsMath.shadowLike(props: props, rgba: sf.rgba, warpedPlate: wp, params: params)
            }
            if let floorY = side.poseFloorY {
                m.floorContactLeak = MetricsMath.floorLeak(props: props, floorY: floorY, width: width, height: height)
            }
            if f > 0, avail[f], let prevProps {
                // Warp previous props through this frame's flow (gather).
                var warped = [UInt8](repeating: 0, count: count)
                let dx = fdx[f], dy = fdy[f]
                for y in 0..<height {
                    for x in 0..<width {
                        let i = y * width + x
                        let sx = Int((Float(x) + dx[i]).rounded()), sy = Int((Float(y) + dy[i]).rounded())
                        if sx >= 0 && sx < width && sy >= 0 && sy < height { warped[i] = prevProps[sy * width + sx] }
                    }
                }
                m.propFlicker = MetricsMath.propFlicker(current: props, warpedPrevious: warped, width: width, height: height).events
            }
            prevProps = props
            // Every-15 still: dim source, GREEN original prop, RED propagated
            // addition, BLUE person — a false object is a red blob off the props.
            if let stillsDir, f % stillsEvery == 0 {
                var img = sf.rgba
                for i in 0..<count {
                    let o = i * 4
                    img[o] = UInt8(Int(img[o]) / 3); img[o + 1] = UInt8(Int(img[o + 1]) / 3); img[o + 2] = UInt8(Int(img[o + 2]) / 3)
                    if sf.person[i] != 0 { img[o + 2] = 255 }
                    if layer[f][i] != 0 { img[o + 1] = 255 }        // original prop
                    if props[i] != 0 && layer[f][i] == 0 { img[o] = 255 }  // propagated addition
                    img[o + 3] = 255
                }
                try writePNG(rgba: img, width: width, height: height,
                             to: stillsDir.appendingPathComponent(String(format: "%04d-prop%d.png", f, maxGap)))
            }
            // Re-render the rotoscoped frame from the propagated mask.
            if let r = render {
                flow.load(dx: fdx[f], dy: fdy[f], available: avail[f])
                // Empty-subject fallback, exactly as pass 1: paint the whole
                // frame as body so the output is not transparent.
                let emptySubject = side.subjectPixels == 0 && !r.keepBackground
                var tiers = [UInt8](repeating: emptySubject ? FocusTier.body.rawValue : FocusTier.background.rawValue, count: count)
                let hasFace = side.faceCenterX != nil
                if !emptySubject {
                    for y in 0..<height {
                        for x in 0..<width {
                            let i = y * width + x
                            if maskBin[i] == 0 { continue }
                            var tier = FocusTier.body
                            if hasFace, let cx = side.faceCenterX, let cy = side.faceCenterY,
                               let rx = side.faceRadiusX, let ry = side.faceRadiusY {
                                let nx = (Double(x) + 0.5) / Double(width), ny = (Double(y) + 0.5) / Double(height)
                                let fx = (nx - cx) / max(0.0001, rx), fy = (ny - cy) / max(0.0001, ry)
                                if fx * fx + fy * fy <= 1 { tier = .face }
                            }
                            tiers[i] = tier.rawValue
                        }
                    }
                }
                var options = r.options
                if !hasFace {
                    options.quotas = TierValues(face: 0, body: r.options.quotas.face + r.options.quotas.body, background: r.options.quotas.background)
                }
                // Flow-advanced markers from the previous painted frame, like pass 1.
                var seeds: [Int] = []
                if params.propagateMarkers && flow.available && !previousMarkers.isEmpty {
                    seeds = flow.advance(indices: previousMarkers)
                }
                let painted = Engine.process(rgba: sf.rgba, width: width, height: height, tiers: tiers,
                                             alpha: emptySubject ? nil : maskProp, removeBackground: r.removeBackground,
                                             options: options, seeds: seeds)
                if f > 0 && flow.available {
                    m.markerPersistence = MetricsMath.markerPersistence(
                        advanced: seeds, current: painted.markers.indices, width: width, height: height)
                }
                let time = CMTime(seconds: side.metrics.time, preferredTimescale: 600)
                if !writersStarted { for e in writers { try e.w.start(at: time) }; writersStarted = true }
                for e in writers { try e.w.append(rgba: painted.rgba, at: time) }
                previousMarkers = painted.markers.indices
            }
            lines.append(try enc.encode(m)); lines.append(0x0A)
            outMetrics.append(m)
        }
        for e in writers { e.w.finishVideo() }
        for e in writers { e.w.finishAudio() }
        for e in writers { try await e.w.finish() }
        // Mux the source audio into each rendered file (interleaving during the
        // rapid video appends above deadlocks the proRes muxer, so do it here).
        if let r = render, let asset = r.audioAsset, let track = r.audioTrack {
            for e in writers { try await muxAudio(video: e.url, audioAsset: asset, audioTrack: track) }
        }
        try lines.write(to: out)
        log("propagation: maxGap \(maxGap) -> \(out.lastPathComponent)")
        return outMetrics
    }

    /// Combine a rendered video-only file's video with the source audio track,
    /// in place, by a passthrough export (no re-encode).
    static func muxAudio(video: URL, audioAsset: AVAsset, audioTrack: AVAssetTrack) async throws {
        let videoAsset = AVURLAsset(url: video)
        guard let vTrack = try await videoAsset.loadTracks(withMediaType: .video).first else { return }
        let vDur = try await videoAsset.load(.duration)
        let aDur = try await audioAsset.load(.duration)
        let comp = AVMutableComposition()
        guard let cv = comp.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid) else { return }
        try cv.insertTimeRange(CMTimeRange(start: .zero, duration: vDur), of: vTrack, at: .zero)
        if let ca = comp.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid) {
            try? ca.insertTimeRange(CMTimeRange(start: .zero, duration: CMTimeMinimum(vDur, aDur)), of: audioTrack, at: .zero)
        }
        let fileType: AVFileType = video.pathExtension.lowercased() == "mp4" ? .mp4 : .mov
        guard let export = AVAssetExportSession(asset: comp, presetName: AVAssetExportPresetPassthrough) else { return }
        let tmp = video.deletingLastPathComponent().appendingPathComponent("._mux-\(video.lastPathComponent)")
        try? FileManager.default.removeItem(at: tmp)
        export.outputURL = tmp
        export.outputFileType = fileType
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            export.exportAsynchronously { cont.resume() }
        }
        if export.status == .completed {
            try? FileManager.default.removeItem(at: video)
            try FileManager.default.moveItem(at: tmp, to: video)
        } else {
            try? FileManager.default.removeItem(at: tmp)
            throw RotoscopeVisionError.video("audio mux failed for \(video.lastPathComponent): \(export.error?.localizedDescription ?? "unknown")")
        }
    }
}
