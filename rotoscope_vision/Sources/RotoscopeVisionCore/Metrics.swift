import Foundation

/// Deterministic per-frame measurements. Every field is a plain number so the
/// file is greppable, plottable, and diffable; nil means "not applicable this
/// frame" (no flow on frame 0, no pose found, no props).
public struct FrameMetrics: Codable {
    public var frame: Int
    public var time: Double

    public init(frame: Int, time: Double) {
        self.frame = frame
        self.time = time
    }

    // Registration & plate
    public var bgResidualMedian: Double? = nil
    public var bgFalseRate: Double? = nil
    public var regAccepted: Int? = nil
    public var regRefinePx: Double? = nil
    public var regJumpPx: Double? = nil

    // Mask
    public var maskArea: Int = 0
    public var maskAreaDelta: Double? = nil
    public var maskTemporalIoU: Double? = nil
    public var maskComponents: Int = 0
    public var maskHoles: Int = 0
    public var maskBoundaryRatio: Double = 0
    public var maskSoftBand: Double = 0

    // Props
    public var propArea: Int = 0
    public var propComponents: Int = 0
    public var propFlicker: Int? = nil
    public var propTrackedArea: [Int] = []
    public var discCount: Int = 0
    public var discRadii: [Double] = []
    public var discRadiusDelta: Double? = nil
    public var barLineResidual: Double? = nil
    public var barLineLength: Double? = nil
    public var poseBarAgreement: Double? = nil
    public var poseFound: Int = 0

    // Shadow
    public var shadowLikeInProps: Double? = nil
    public var floorContactLeak: Double? = nil

    // Paint
    public var paintPSNR: Double = 0
    public var paintBoundaryRecall: Double = 0
    public var paintRegionCount: Int = 0
    public var paintRegionP50: Int = 0
    public var paintTemporalDelta: Double? = nil
    public var markerPersistence: Double? = nil
    public var markerCount: Int = 0

    // Motion & cost
    public var flowMeanPx: Double? = nil
    public var msVision: Double = 0
    public var msEvidence: Double = 0
    public var msPaint: Double = 0
    public var msTotal: Double = 0
}

public enum MetricsMath {
    public static func median(_ values: [Int]) -> Double {
        guard !values.isEmpty else { return 0 }
        let sorted = values.sorted()
        let mid = sorted.count / 2
        return sorted.count % 2 == 1 ? Double(sorted[mid]) : Double(sorted[mid - 1] + sorted[mid]) / 2
    }

    /// Median difference and fraction above `threshold` over `include` pixels.
    public static func backgroundResidual(difference: [UInt8], include: [UInt8], threshold: Int) -> (median: Double, falseRate: Double) {
        var values: [Int] = []
        values.reserveCapacity(difference.count / 8)
        var above = 0
        var n = 0
        // Subsample every 4th pixel for speed; the statistic is stable.
        for index in stride(from: 0, to: difference.count, by: 4) where include[index] != 0 {
            let d = Int(difference[index])
            values.append(d)
            if d > threshold { above += 1 }
            n += 1
        }
        return (median(values), n > 0 ? Double(above) / Double(n) : 0)
    }

    public static func components(_ mask: [UInt8], width: Int, height: Int) -> Int {
        let count = width * height
        var label = [UInt8](repeating: 0, count: count)
        var stack: [Int] = []
        var total = 0
        for start in 0..<count where mask[start] != 0 && label[start] == 0 {
            total += 1
            stack.append(start)
            label[start] = 1
            while let index = stack.popLast() {
                let y = index / width
                let x = index - y * width
                for dy in -1...1 {
                    let ny = y + dy
                    if ny < 0 || ny >= height { continue }
                    for dx in -1...1 {
                        let nx = x + dx
                        if nx < 0 || nx >= width { continue }
                        let neighbor = ny * width + nx
                        if mask[neighbor] != 0 && label[neighbor] == 0 {
                            label[neighbor] = 1
                            stack.append(neighbor)
                        }
                    }
                }
            }
        }
        return total
    }

    /// Enclosed background pockets (holes) of a binary mask.
    public static func holes(_ mask: [UInt8], width: Int, height: Int) -> Int {
        let filled = Morphology.fillHoles(mask, keepOpen: [UInt8](repeating: 0, count: mask.count), width: width, height: height)
        var diff = [UInt8](repeating: 0, count: mask.count)
        for index in 0..<mask.count where filled[index] != 0 && mask[index] == 0 { diff[index] = 1 }
        return components(diff, width: width, height: height)
    }

    /// Boundary pixels (4-neighborhood) / sqrt(area).
    public static func boundaryRatio(_ mask: [UInt8], width: Int, height: Int) -> (boundary: Int, ratio: Double, area: Int) {
        var boundary = 0
        var area = 0
        for y in 0..<height {
            for x in 0..<width {
                let index = y * width + x
                if mask[index] == 0 { continue }
                area += 1
                if x == 0 || y == 0 || x == width - 1 || y == height - 1
                    || mask[index - 1] == 0 || mask[index + 1] == 0 || mask[index - width] == 0 || mask[index + width] == 0
                {
                    boundary += 1
                }
            }
        }
        return (boundary, area > 0 ? Double(boundary) / Double(area).squareRoot() : 0, area)
    }

    /// Labels components and returns per-label area (index 0 unused).
    public static func labelComponents(_ mask: [UInt8], width: Int, height: Int) -> (labels: [Int32], areas: [Int]) {
        let count = width * height
        var labels = [Int32](repeating: 0, count: count)
        var areas: [Int] = [0]
        var stack: [Int] = []
        var next: Int32 = 0
        for start in 0..<count where mask[start] != 0 && labels[start] == 0 {
            next += 1
            areas.append(0)
            stack.append(start)
            labels[start] = next
            while let index = stack.popLast() {
                areas[Int(next)] += 1
                let y = index / width
                let x = index - y * width
                for dy in -1...1 {
                    let ny = y + dy
                    if ny < 0 || ny >= height { continue }
                    for dx in -1...1 {
                        let nx = x + dx
                        if nx < 0 || nx >= width { continue }
                        let neighbor = ny * width + nx
                        if mask[neighbor] != 0 && labels[neighbor] == 0 {
                            labels[neighbor] = next
                            stack.append(neighbor)
                        }
                    }
                }
            }
        }
        return (labels, areas)
    }

    /// Prop flicker: components of `current` matched to components of the
    /// flow-warped `previous` by overlap. A component that appears, vanishes,
    /// or changes area by more than 30 % counts as one flicker event.
    public static func propFlicker(current: [UInt8], warpedPrevious: [UInt8], width: Int, height: Int) -> (events: Int, trackedAreas: [Int]) {
        let (curLabels, curAreas) = labelComponents(current, width: width, height: height)
        let (prevLabels, prevAreas) = labelComponents(warpedPrevious, width: width, height: height)
        var overlap: [Int: [Int: Int]] = [:]  // cur → prev → pixels
        for index in 0..<current.count where curLabels[index] != 0 && prevLabels[index] != 0 {
            overlap[Int(curLabels[index]), default: [:]][Int(prevLabels[index]), default: 0] += 1
        }
        var events = 0
        var matchedPrev = Set<Int>()
        var tracked: [Int] = []
        for cur in 1..<curAreas.count {
            if curAreas[cur] < 40 { continue }  // ignore specks
            tracked.append(curAreas[cur])
            guard let best = overlap[cur]?.max(by: { $0.value < $1.value }) else {
                events += 1  // appeared
                continue
            }
            matchedPrev.insert(best.key)
            let ratio = Double(curAreas[cur]) / Double(max(1, prevAreas[best.key]))
            if ratio > 1.3 || ratio < 0.7 { events += 1 }
        }
        for prev in 1..<prevAreas.count where prevAreas[prev] >= 40 && !matchedPrev.contains(prev) {
            events += 1  // vanished
        }
        return (events, tracked)
    }

    /// Least-squares line through prop pixels near the pose bar segment:
    /// RMS perpendicular residual to the fitted line, its length, and the
    /// mean distance of those pixels to the pose line itself.
    public static func barLine(props: [UInt8], segment: BodyPose.Segment, width: Int, height: Int) -> (residual: Double, length: Double, agreement: Double)? {
        var xs: [Double] = [], ys: [Double] = []
        var poseDist = 0.0
        for y in 0..<height {
            for x in 0..<width where props[y * width + x] != 0 {
                let (d, along) = segment.distance(x: Double(x), y: Double(y))
                if d < 60 && along > -3 && along < 4 {
                    xs.append(Double(x)); ys.append(Double(y)); poseDist += d
                }
            }
        }
        guard xs.count > 50 else { return nil }
        let n = Double(xs.count)
        let mx = xs.reduce(0, +) / n, my = ys.reduce(0, +) / n
        var sxx = 0.0, sxy = 0.0, syy = 0.0
        for i in 0..<xs.count {
            let dx = xs[i] - mx, dy = ys[i] - my
            sxx += dx * dx; sxy += dx * dy; syy += dy * dy
        }
        // Principal direction.
        let theta = 0.5 * atan2(2 * sxy, sxx - syy)
        let ux = cos(theta), uy = sin(theta)
        var residual = 0.0
        var minT = Double.greatestFiniteMagnitude, maxT = -Double.greatestFiniteMagnitude
        for i in 0..<xs.count {
            let dx = xs[i] - mx, dy = ys[i] - my
            let t = dx * ux + dy * uy
            let perp = -dx * uy + dy * ux
            residual += perp * perp
            minT = min(minT, t); maxT = max(maxT, t)
        }
        return ((residual / n).squareRoot(), maxT - minT, poseDist / n)
    }

    /// Fraction of prop pixels whose chromaticity matches the plate at reduced brightness.
    public static func shadowLike(props: [UInt8], rgba: [UInt8], warpedPlate: [UInt8], params: Params) -> Double? {
        var n = 0, shadow = 0
        for index in 0..<props.count where props[index] != 0 && warpedPlate[index * 4 + 3] != 0 {
            n += 1
            let fr = Double(rgba[index * 4]), fg = Double(rgba[index * 4 + 1]), fb = Double(rgba[index * 4 + 2])
            let pr = Double(warpedPlate[index * 4]), pg = Double(warpedPlate[index * 4 + 1]), pb = Double(warpedPlate[index * 4 + 2])
            let fSum = fr + fg + fb, pSum = pr + pg + pb
            guard pSum > 30, fSum < pSum else { continue }
            let ratio = fSum / pSum
            guard ratio >= params.shadowMinRatio, ratio <= params.shadowMaxRatio else { continue }
            let chroma = max(abs(fr - pr * ratio), max(abs(fg - pg * ratio), abs(fb - pb * ratio)))
            if chroma < params.shadowChromaTolerance { shadow += 1 }
        }
        return n > 0 ? Double(shadow) / Double(n) : nil
    }

    /// Fraction of prop pixels below the floor contact line.
    public static func floorLeak(props: [UInt8], floorY: Double, width: Int, height: Int) -> Double? {
        var n = 0, below = 0
        let limit = Int(floorY) + 6
        for y in 0..<height {
            for x in 0..<width where props[y * width + x] != 0 {
                n += 1
                if y > limit { below += 1 }
            }
        }
        return n > 0 ? Double(below) / Double(n) : nil
    }

    /// PSNR of the painted frame against the source inside the mask.
    public static func psnr(painted: [UInt8], source: [UInt8], mask: [UInt8]) -> Double {
        var sum = 0.0
        var n = 0
        for index in 0..<mask.count where mask[index] != 0 {
            for c in 0..<3 {
                let d = Double(painted[index * 4 + c]) - Double(source[index * 4 + c])
                sum += d * d
            }
            n += 3
        }
        guard n > 0, sum > 0 else { return 99 }
        return 10 * log10(255.0 * 255.0 / (sum / Double(n)))
    }

    /// Fraction of strong source edges (gray Sobel magnitude > tau) inside the
    /// mask that lie within one pixel of a basin boundary.
    public static func boundaryRecall(labels: [UInt32], gray: [UInt8], mask: [UInt8], width: Int, height: Int, tau: Int) -> Double {
        let gradient = Engine.sobel(gray, width: width, height: height).magnitude
        var edges = 0, hit = 0
        for y in 1..<(height - 1) {
            for x in 1..<(width - 1) {
                let index = y * width + x
                if mask[index] == 0 || Int(gradient[index]) <= tau { continue }
                edges += 1
                let l = labels[index]
                var boundary = false
                outer: for dy in -1...1 {
                    for dx in -1...1 where labels[index + dy * width + dx] != l {
                        boundary = true
                        break outer
                    }
                }
                if boundary { hit += 1 }
            }
        }
        return edges > 0 ? Double(hit) / Double(edges) : 1
    }

    /// Mean absolute color change inside the mask against the flow-warped previous paint.
    public static func temporalDelta(current: [UInt8], warpedPrevious: [UInt8], mask: [UInt8]) -> Double? {
        var sum = 0
        var n = 0
        for index in 0..<mask.count where mask[index] != 0 && warpedPrevious[index * 4 + 3] != 0 {
            for c in 0..<3 { sum += abs(Int(current[index * 4 + c]) - Int(warpedPrevious[index * 4 + c])) }
            n += 3
        }
        return n > 0 ? Double(sum) / Double(n) : nil
    }

    /// Fraction of advanced previous markers that land within 2 px of a current marker.
    public static func markerPersistence(advanced: [Int], current: [Int], width: Int, height: Int) -> Double? {
        guard !advanced.isEmpty else { return nil }
        var grid = [UInt8](repeating: 0, count: width * height)
        for index in current {
            let y = index / width, x = index - y * width
            for dy in -2...2 {
                let ny = y + dy
                if ny < 0 || ny >= height { continue }
                for dx in -2...2 {
                    let nx = x + dx
                    if nx >= 0 && nx < width { grid[ny * width + nx] = 1 }
                }
            }
        }
        var hit = 0
        for index in advanced where grid[index] != 0 { hit += 1 }
        return Double(hit) / Double(advanced.count)
    }
}

/// Aggregates per-frame metrics into mean / p95 / max per numeric field and
/// writes `summary.json`; compares against a baseline with red lines.
public struct MetricsSummary: Codable {
    public struct Stat: Codable {
        public var mean: Double
        public var p95: Double
        public var max: Double
        public var min: Double
        public var count: Int
    }
    public var frames: Int
    public var stats: [String: Stat]
    public var params: Params
    public var objective: Double?

    public static func build(from metrics: [FrameMetrics], params: Params) throws -> MetricsSummary {
        let encoder = JSONEncoder()
        var columns: [String: [Double]] = [:]
        for m in metrics {
            let data = try encoder.encode(m)
            guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
            for (key, value) in object {
                if let number = value as? Double { columns[key, default: []].append(number) }
                else if let number = value as? Int { columns[key, default: []].append(Double(number)) }
                else if let array = value as? [Double], !array.isEmpty {
                    columns[key + "Mean", default: []].append(array.reduce(0, +) / Double(array.count))
                } else if let array = value as? [Int], !array.isEmpty {
                    columns[key + "Mean", default: []].append(Double(array.reduce(0, +)) / Double(array.count))
                }
            }
        }
        var stats: [String: Stat] = [:]
        for (key, values) in columns where key != "frame" && key != "time" {
            let sorted = values.sorted()
            let p95 = sorted[min(sorted.count - 1, Int(Double(sorted.count - 1) * 0.95))]
            stats[key] = Stat(
                mean: values.reduce(0, +) / Double(values.count), p95: p95, max: sorted.last ?? 0,
                min: sorted.first ?? 0, count: values.count)
        }
        return MetricsSummary(frames: metrics.count, stats: stats, params: params, objective: nil)
    }

    public func json() throws -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return String(decoding: try encoder.encode(self), as: UTF8.self)
    }
}

/// The objective an optimizer maximizes, and the red lines a change may not
/// cross. Loaded from `eval/objective.json`; defaults are the plan's.
public struct Objective: Codable {
    public struct Term: Codable {
        public var metric: String
        public var stat: String
        public var weight: Double
        /// Value at which the term contributes 0 (lower-is-better metrics)
        /// or 1 (higher-is-better), and the scale over which it moves.
        public var target: Double
        public var scale: Double
        public var higherIsBetter: Bool
    }
    public struct RedLine: Codable {
        public var metric: String
        public var stat: String
        public var maxIncrease: Double
    }
    public var terms: [Term]
    public var redLines: [RedLine]

    public static let standard = Objective(
        terms: [
            Term(metric: "bgFalseRate", stat: "mean", weight: 2.0, target: 0.0, scale: 0.01, higherIsBetter: false),
            Term(metric: "propFlicker", stat: "mean", weight: 2.0, target: 0.0, scale: 1.0, higherIsBetter: false),
            Term(metric: "maskTemporalIoU", stat: "mean", weight: 2.0, target: 1.0, scale: 0.1, higherIsBetter: true),
            Term(metric: "shadowLikeInProps", stat: "mean", weight: 1.0, target: 0.0, scale: 0.1, higherIsBetter: false),
            Term(metric: "floorContactLeak", stat: "mean", weight: 1.0, target: 0.0, scale: 0.05, higherIsBetter: false),
            Term(metric: "discRadiusDelta", stat: "mean", weight: 1.0, target: 0.0, scale: 5.0, higherIsBetter: false),
            Term(metric: "paintBoundaryRecall", stat: "mean", weight: 0.5, target: 1.0, scale: 0.3, higherIsBetter: true),
            Term(metric: "paintTemporalDelta", stat: "mean", weight: 0.5, target: 0.0, scale: 12.0, higherIsBetter: false),
            Term(metric: "maskComponents", stat: "mean", weight: 1.0, target: 1.0, scale: 1.0, higherIsBetter: false),
        ],
        redLines: [
            RedLine(metric: "bgFalseRate", stat: "mean", maxIncrease: 0.005),
            RedLine(metric: "propFlicker", stat: "mean", maxIncrease: 0.5),
            RedLine(metric: "shadowLikeInProps", stat: "mean", maxIncrease: 0.05),
        ])

    public func score(_ summary: MetricsSummary) -> Double {
        var total = 0.0
        for term in terms {
            guard let stat = summary.stats[term.metric] else { continue }
            let value: Double
            switch term.stat {
            case "p95": value = stat.p95
            case "max": value = stat.max
            default: value = stat.mean
            }
            let normalized = term.higherIsBetter
                ? 1 - max(0, min(1, (term.target - value) / term.scale))
                : 1 - max(0, min(1, (value - term.target) / term.scale))
            total += term.weight * normalized
        }
        return total
    }

    /// Red-line violations of `candidate` against `baseline`.
    public func violations(candidate: MetricsSummary, baseline: MetricsSummary) -> [String] {
        var out: [String] = []
        for line in redLines {
            guard let c = candidate.stats[line.metric], let b = baseline.stats[line.metric] else { continue }
            let cv = line.stat == "p95" ? c.p95 : line.stat == "max" ? c.max : c.mean
            let bv = line.stat == "p95" ? b.p95 : line.stat == "max" ? b.max : b.mean
            if cv - bv > line.maxIncrease {
                out.append(String(format: "%@.%@ rose %.4f → %.4f (limit +%.4f)", line.metric, line.stat, bv, cv, line.maxIncrease))
            }
        }
        return out
    }
}
