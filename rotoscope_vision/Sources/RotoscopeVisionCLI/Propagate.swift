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
    static func run(scratchDir: URL, out: URL, frames n: Int, params: Params, log: (String) -> Void) throws {
        guard n > 0 else { return }
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

        @inline(__always) func sample(_ img: [UInt8], _ x: Int, _ y: Int, _ c: Int) -> Int {
            Int(img[(y * width + x) * 4 + c])
        }
        @inline(__always) func colourOK(_ carried: [UInt8], _ actual: [UInt8], _ i: Int) -> Bool {
            let dr = abs(Int(carried[i * 4]) - Int(actual[i * 4]))
            let dg = abs(Int(carried[i * 4 + 1]) - Int(actual[i * 4 + 1]))
            let db = abs(Int(carried[i * 4 + 2]) - Int(actual[i * 4 + 2]))
            return Float(max(dr, max(dg, db))) <= tol
        }

        // Forward sweep: gather result[t-1] into t through flow[t].
        for t in 1..<n where avail[t] {
            let dx = fdx[t], dy = fdy[t]
            let prevR = result[t - 1], prevA = age[t - 1], prevC = color[t - 1]
            let src = rgba[t - 1]
            for y in 0..<height {
                for x in 0..<width {
                    let i = y * width + x
                    if result[t][i] != 0 { continue }
                    if person[t][i] != 0 || others[t][i] != 0 { continue }
                    // A held object still differs from the background plate where
                    // it lands; a shadow or background match does not.
                    if Int(difference[t][i]) < Int(params.plateThreshold) / 3 { continue }
                    let sx = Int((Float(x) + dx[i]).rounded()), sy = Int((Float(y) + dy[i]).rounded())
                    if sx < 0 || sx >= width || sy < 0 || sy >= height { continue }
                    let j = sy * width + sx
                    if prevR[j] == 0 { continue }
                    let a = prevA[j] + 1
                    if a > Int32(maxGap) { continue }
                    // Carried colour = origin colour from the previous frame's carrier.
                    var carriedPix = [UInt8](repeating: 0, count: 4)
                    carriedPix[0] = prevC[j * 4]; carriedPix[1] = prevC[j * 4 + 1]; carriedPix[2] = prevC[j * 4 + 2]
                    let dr = abs(Int(carriedPix[0]) - Int(rgba[t][i * 4]))
                    let dg = abs(Int(carriedPix[1]) - Int(rgba[t][i * 4 + 1]))
                    let db = abs(Int(carriedPix[2]) - Int(rgba[t][i * 4 + 2]))
                    if Float(max(dr, max(dg, db))) > tol { continue }
                    _ = src
                    result[t][i] = 1
                    age[t][i] = a
                    color[t][i * 4] = carriedPix[0]; color[t][i * 4 + 1] = carriedPix[1]; color[t][i * 4 + 2] = carriedPix[2]
                }
            }
        }
        // Backward sweep: scatter result[t] into t-1 through flow[t].
        for t in stride(from: n - 1, through: 1, by: -1) where avail[t] {
            let dx = fdx[t], dy = fdy[t]
            for y in 0..<height {
                for x in 0..<width {
                    let i = y * width + x
                    if result[t][i] == 0 { continue }
                    let a = age[t][i] + 1
                    if a > Int32(maxGap) { continue }
                    let dxp = Int((Float(x) + dx[i]).rounded()), dyp = Int((Float(y) + dy[i]).rounded())
                    if dxp < 0 || dxp >= width || dyp < 0 || dyp >= height { continue }
                    let d = dyp * width + dxp
                    if result[t - 1][d] != 0 { continue }
                    if person[t - 1][d] != 0 || others[t - 1][d] != 0 { continue }
                    if Int(difference[t - 1][d]) < Int(params.plateThreshold) / 3 { continue }
                    let dr = abs(Int(color[t][i * 4]) - Int(rgba[t - 1][d * 4]))
                    let dg = abs(Int(color[t][i * 4 + 1]) - Int(rgba[t - 1][d * 4 + 1]))
                    let db = abs(Int(color[t][i * 4 + 2]) - Int(rgba[t - 1][d * 4 + 2]))
                    if Float(max(dr, max(dg, db))) > tol { continue }
                    result[t - 1][d] = 1
                    age[t - 1][d] = a
                    color[t - 1][d * 4] = color[t][i * 4]; color[t - 1][d * 4 + 1] = color[t][i * 4 + 1]
                    color[t - 1][d * 4 + 2] = color[t][i * 4 + 2]
                }
            }
        }

        // Drop propagated components below minRenderArea (per frame).
        for f in 0..<n {
            result[f] = Morphology.removeSmallComponents(result[f], width: width, height: height, minArea: params.minRenderArea)
        }

        // Pass 3: recompute the presence + guard-rail metrics from the
        // propagated masks and write metrics_prop.jsonl.
        let enc = JSONEncoder()
        var lines = Data()
        var prevProps: [UInt8]?
        for f in 0..<n {
            guard let side = sides[f] else { continue }
            let sf = try Scratch.read(scratchDir, frame: f)   // reload for difference/warpedPlate
            let props = result[f]
            var maskProp = sf.mask
            for i in 0..<count where props[i] != 0 { maskProp[i] = 255 }
            var m = side.metrics
            m.maskComponents = MetricsMath.components(maskProp, width: width, height: height)
            let (_, _, propArea) = MetricsMath.boundaryRatio(props.map { $0 != 0 ? 255 : 0 }, width: width, height: height)
            m.propArea = propArea
            m.propComponents = MetricsMath.components(props, width: width, height: height)
            // Presence recall on the propagated mask.
            let band = Presence.bandTruth(rgba: sf.rgba, person: sf.person, width: width, height: height)
            let b = Presence.recall(truth: band, mask: maskProp, minimum: 300)
            m.bandTruth = b.count; m.bandRecall = b.recall
            var bar: BodyPose.Segment? = nil
            if let x0 = side.barX0, let y0 = side.barY0, let x1 = side.barX1, let y1 = side.barY1 {
                bar = BodyPose.Segment(x0: x0, y0: y0, x1: x1, y1: y1)
            }
            if let plates = Presence.plateTruth(rgba: sf.rgba, difference: sf.difference, person: sf.person, bar: bar,
                                                width: width, height: height, threshold: Int(params.plateThreshold)) {
                let l = Presence.recall(truth: plates.left, mask: maskProp, minimum: 800)
                let r = Presence.recall(truth: plates.right, mask: maskProp, minimum: 800)
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
            lines.append(try enc.encode(m)); lines.append(0x0A)
            _ = sample
        }
        try lines.write(to: out)
        log("propagation: maxGap \(maxGap) -> \(out.lastPathComponent)")
    }
}
