import Foundation

/// A tracked plate disc at one end of the bar.
public struct Disc: Codable, Equatable {
    public var cx: Double
    public var cy: Double
    public var radius: Double
    /// −1 = before the bar segment's start, +1 = past its end.
    public var side: Int
    public var age: Int
}

public struct PropEvidenceResult {
    public var posterior: [Float]
    public var pDiff: [Float]
    public var pShadow: [Float]
    public var pStruct: [Float]
    public var pPrior: [Float]
    public var props: [UInt8]
    public var discs: [Disc]
    public var barUsed: Bool
}

/// Probabilistic prop evidence: instead of thresholding the plate difference
/// and repairing the result with morphology, every cue becomes a per-pixel
/// probability, the cues fuse into one posterior, and a single decision
/// (smooth → threshold → connected to the person → holes filled) makes the
/// mask. Priors never create props on their own; they let weaker difference
/// evidence through where a prop is expected.
public final class SoftEvidence {
    public let width: Int
    public let height: Int
    public var params: Params
    private var previousPosterior: [Float]?
    private var discs: [Disc] = []

    public init(width: Int, height: Int, params: Params) {
        self.width = width
        self.height = height
        self.params = params
    }

    public var trackedDiscs: [Disc] { discs }

    @inline(__always)
    private func logistic(_ value: Double, center: Double, width: Double) -> Float {
        Float(1 / (1 + exp(-(value - center) / max(0.5, width))))
    }

    // swiftlint:disable:next function_body_length
    public func compute(
        rgba: [UInt8], difference: [UInt8], warpedPlate: [UInt8]?, person: [UInt8], others: [UInt8]?,
        pose: BodyPose?, flow: OpticalFlow?, minY: Int, maxY: Int
    ) -> PropEvidenceResult {
        let count = width * height
        let p = params

        // --- difference evidence, discounted by shadow likelihood ---
        var pDiff = [Float](repeating: 0, count: count)
        var pWeak = [Float](repeating: 0, count: count)
        var pShadow = [Float](repeating: 0, count: count)
        for index in 0..<count {
            let d = Double(difference[index])
            pDiff[index] = logistic(d, center: p.diffCenter, width: p.diffWidth)
            pWeak[index] = logistic(d, center: p.diffCenter * 0.5, width: p.diffWidth)
            if let plate = warpedPlate, plate[index * 4 + 3] != 0 {
                let fr = Double(rgba[index * 4]), fg = Double(rgba[index * 4 + 1]), fb = Double(rgba[index * 4 + 2])
                let pr = Double(plate[index * 4]), pg = Double(plate[index * 4 + 1]), pb = Double(plate[index * 4 + 2])
                let fSum = fr + fg + fb
                let pSum = pr + pg + pb
                if pSum > 30 && fSum < pSum {
                    let ratio = fSum / pSum
                    if ratio >= p.shadowMinRatio && ratio <= p.shadowMaxRatio {
                        // Chroma distance between the frame and the plate scaled
                        // to the frame's brightness; a shadow keeps chroma.
                        let scale = fSum / pSum
                        let chroma = max(abs(fr - pr * scale), max(abs(fg - pg * scale), abs(fb - pb * scale)))
                        let tol = p.shadowChromaTolerance
                        pShadow[index] = Float(max(0, min(1, 1 - (chroma - tol * 0.5) / tol)))
                    }
                }
            }
            let keep = 1 - Float(p.shadowStrength) * pShadow[index]
            pDiff[index] *= keep
            pWeak[index] *= keep
        }

        // --- structural prior: bar line and plate discs from pose ---
        var pStruct = [Float](repeating: 0, count: count)
        var barUsed = false
        if let segment = pose?.barSegment, segment.length > 20 {
            barUsed = true
            let ext = 0.9 * Double(width) / max(1, segment.length)  // how far the bar reaches past the elbows
            let half = p.barHalfWidth
            for y in minY...max(minY, maxY) {
                for x in 0..<width {
                    let (dist, along) = segment.distance(x: Double(x), y: Double(y))
                    if along < -ext || along > 1 + ext { continue }
                    let v = exp(-(dist * dist) / (2 * half * half))
                    if v > 0.02 { pStruct[y * width + x] = Float(v) }
                }
            }
        }
        for disc in discs where disc.age < 3 {
            addDisc(disc, to: &pStruct)
        }

        // --- fuse: evidence, structure-boosted weak evidence, flow prior ---
        var prior = [Float](repeating: 0, count: count)
        if let previousPosterior {
            prior = flow?.warp(previousPosterior, fill: 0) ?? previousPosterior
            let decay = Float(p.priorDecay)
            for index in 0..<count { prior[index] *= decay }
        }
        var posterior = [Float](repeating: 0, count: count)
        let sw = Float(p.structWeight)
        let pw = Float(p.priorWeight)
        for index in 0..<count {
            if person[index] != 0 { continue }
            let y = index / width
            if y < minY || y > maxY { continue }
            if let others, others[index] != 0 { continue }
            var v = pDiff[index]
            // Structure lets weak evidence through where a prop is expected.
            v = v + (1 - v) * sw * pStruct[index] * pWeak[index]
            // The flow-warped prior sustains a prop while faint evidence remains.
            let faint = logistic(Double(difference[index]), center: p.diffCenter * 0.33, width: p.diffWidth) * (1 - Float(p.shadowStrength) * pShadow[index])
            v = v + (1 - v) * pw * prior[index] * faint
            posterior[index] = min(1, v)
        }

        // --- decision ---
        var props = decide(posterior: posterior, person: person)
        // Plate discs: fit at the bar ends from this decision, then let them
        // pull in the dark interiors and decide again.
        if p.trackDiscs, let segment = pose?.barSegment, segment.length > 20 {
            let fitted = fitDiscs(props: props, segment: segment)
            updateDiscTracks(fitted)
            if !discs.isEmpty {
                var boosted = posterior
                var discPrior = [Float](repeating: 0, count: count)
                for disc in discs where disc.age < 3 { addDisc(disc, to: &discPrior) }
                for index in 0..<count where discPrior[index] > 0 && person[index] == 0 {
                    let y = index / width
                    if y < minY || y > maxY { continue }
                    if let others, others[index] != 0 { continue }
                    // Inside a tracked disc the interior is asserted; the plate is
                    // black on a dark rack and carries no difference of its own.
                    boosted[index] = max(boosted[index], discPrior[index] * (1 - Float(p.shadowStrength) * pShadow[index] * 0.5))
                    pStruct[index] = max(pStruct[index], discPrior[index])
                }
                posterior = boosted
                props = decide(posterior: posterior, person: person)
            }
        } else {
            for i in discs.indices { discs[i].age += 1 }
            discs.removeAll { $0.age > 6 }
        }
        previousPosterior = posterior
        return PropEvidenceResult(
            posterior: posterior, pDiff: pDiff, pShadow: pShadow, pStruct: pStruct, pPrior: prior,
            props: props, discs: discs, barUsed: barUsed)
    }

    private func decide(posterior: [Float], person: [UInt8]) -> [UInt8] {
        let count = width * height
        var smooth = posterior
        let r = params.smoothRadius
        if r > 0 {
            smooth = SoftEvidence.boxBlur(posterior, width: width, height: height, radius: r)
        }
        let threshold = Float(params.decisionThreshold)
        var binary = [UInt8](repeating: 0, count: count)
        for index in 0..<count where smooth[index] > threshold && person[index] == 0 { binary[index] = 1 }
        var graph = binary
        for index in 0..<count where person[index] != 0 { graph[index] = 1 }
        let connected = Morphology.componentsTouching(graph, anchor: person, width: width, height: height)
        var props = [UInt8](repeating: 0, count: count)
        for index in 0..<count where connected[index] != 0 && person[index] == 0 { props[index] = 1 }
        return Morphology.fillHoles(props, keepOpen: person, width: width, height: height)
    }

    private func addDisc(_ disc: Disc, to field: inout [Float]) {
        let r = disc.radius
        let x0 = max(0, Int(disc.cx - r - 6)), x1 = min(width - 1, Int(disc.cx + r + 6))
        let y0 = max(0, Int(disc.cy - r - 6)), y1 = min(height - 1, Int(disc.cy + r + 6))
        if x0 > x1 || y0 > y1 { return }
        for y in y0...y1 {
            for x in x0...x1 {
                let d = hypot(Double(x) - disc.cx, Double(y) - disc.cy)
                let v: Double = d <= r ? 1 : max(0, 1 - (d - r) / 6)
                if v > 0 { field[y * width + x] = max(field[y * width + x], Float(v)) }
            }
        }
    }

    /// Finds compact, large prop blobs beyond either end of the bar segment
    /// and returns them as candidate discs (centroid + equivalent radius).
    private func fitDiscs(props: [UInt8], segment: BodyPose.Segment) -> [Disc] {
        let count = width * height
        var label = [Int32](repeating: 0, count: count)
        var found: [Disc] = []
        var stack: [Int] = []
        var next: Int32 = 0
        for start in 0..<count where props[start] != 0 && label[start] == 0 {
            next += 1
            stack.append(start)
            label[start] = next
            var n = 0
            var sx = 0.0, sy = 0.0
            var minX = width, maxX = 0, minY = height, maxY = 0
            var sideVotes = 0
            while let index = stack.popLast() {
                let y = index / width
                let x = index - y * width
                n += 1
                sx += Double(x); sy += Double(y)
                minX = min(minX, x); maxX = max(maxX, x); minY = min(minY, y); maxY = max(maxY, y)
                let (_, along) = segment.distance(x: Double(x), y: Double(y))
                if along < -0.15 { sideVotes -= 1 } else if along > 1.15 { sideVotes += 1 }
                for dy in -1...1 {
                    let ny = y + dy
                    if ny < 0 || ny >= height { continue }
                    for dx in -1...1 {
                        let nx = x + dx
                        if nx < 0 || nx >= width { continue }
                        let neighbor = ny * width + nx
                        if props[neighbor] != 0 && label[neighbor] == 0 {
                            label[neighbor] = next
                            stack.append(neighbor)
                        }
                    }
                }
            }
            // A blob is a disc candidate when it is big, roughly square in
            // extent, and fills its box the way a circle does (π/4 ≈ 0.785).
            let bw = Double(maxX - minX + 1), bh = Double(maxY - minY + 1)
            let radius = (Double(n) / Double.pi).squareRoot()
            let aspect = min(bw, bh) / max(bw, bh)
            let fill = Double(n) / (bw * bh)
            // Blobs that include a stretch of bar are wider than tall; accept
            // them when the taller dimension still reads as a disc.
            let discRadius = min(bw, bh) / 2
            if discRadius >= 28 && discRadius <= Double(width) / 3 && fill > 0.45 && (aspect > 0.55 || bh > 56) && abs(sideVotes) > n / 4 {
                found.append(Disc(cx: sx / Double(n), cy: sy / Double(n), radius: max(radius, discRadius * 0.9), side: sideVotes < 0 ? -1 : 1, age: 0))
            }
        }
        return found
    }

    private func updateDiscTracks(_ fitted: [Disc]) {
        let s = params.discSmoothing
        var updated: [Disc] = []
        for side in [-1, 1] {
            let candidate = fitted.filter { $0.side == side }.max { $0.radius < $1.radius }
            if let previous = discs.first(where: { $0.side == side }) {
                if let c = candidate, hypot(c.cx - previous.cx, c.cy - previous.cy) < previous.radius * 1.5 + 40 {
                    updated.append(Disc(
                        cx: previous.cx * s + c.cx * (1 - s), cy: previous.cy * s + c.cy * (1 - s),
                        radius: previous.radius * s + c.radius * (1 - s), side: side, age: 0))
                } else if previous.age < 8 {
                    var kept = previous
                    kept.age += 1
                    updated.append(kept)
                }
            } else if let c = candidate {
                updated.append(c)
            }
        }
        discs = updated
    }

    static func boxBlur(_ field: [Float], width: Int, height: Int, radius: Int) -> [Float] {
        let count = width * height
        var horizontal = [Float](repeating: 0, count: count)
        var out = [Float](repeating: 0, count: count)
        for y in 0..<height {
            let row = y * width
            var sum: Float = 0
            var right = min(width - 1, radius)
            for x in 0...right { sum += field[row + x] }
            for x in 0..<width {
                let left = max(0, x - radius)
                right = min(width - 1, x + radius)
                horizontal[row + x] = sum / Float(right - left + 1)
                if x - radius >= 0 { sum -= field[row + x - radius] }
                if x + radius + 1 < width { sum += field[row + x + radius + 1] }
            }
        }
        for x in 0..<width {
            var sum: Float = 0
            var bottom = min(height - 1, radius)
            for y in 0...bottom { sum += horizontal[y * width + x] }
            for y in 0..<height {
                let top = max(0, y - radius)
                bottom = min(height - 1, y + radius)
                out[y * width + x] = sum / Float(bottom - top + 1)
                if y - radius >= 0 { sum -= horizontal[(y - radius) * width + x] }
                if y + radius + 1 < height { sum += horizontal[(y + radius + 1) * width + x] }
            }
        }
        return out
    }
}
