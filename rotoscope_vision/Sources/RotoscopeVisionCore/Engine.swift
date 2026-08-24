import Foundation

/// Swift port of `portfolio/components/home/Rotoscope/algorithm.ts`.
///
/// Same stage order and the same integer kernels as the browser engine:
/// Rec. 601 gray → box blur → |gray − blur| → integer 3×3 Sobel → Shi–Tomasi
/// on a 3×3 tensor window → tiered marker selection → marker-controlled
/// minimum-barrier watershed on the gray Sobel magnitude → one mean color per
/// basin. The only additions are (1) the focus tiers come in as a per-pixel
/// map instead of an ellipse + polygon, so Vision can author them, and (2) the
/// watershed accepts a barrier mask so background pixels are never flooded.
public enum FocusTier: UInt8 {
    case face = 0
    case body = 1
    case background = 2
}

public struct TierValues {
    public var face: Double
    public var body: Double
    public var background: Double

    public init(face: Double, body: Double, background: Double) {
        self.face = face
        self.body = body
        self.background = background
    }
}

public struct EngineOptions {
    /// Radius of the low-frequency box blur that stands in for the clean background frame.
    public var blurRadius: Int = 3
    public var markerBudget: Int = 1200
    /// Fractional marker allocation; normalized at runtime.
    public var quotas = TierValues(face: 0.3, body: 0.7, background: 0.0)
    /// Manhattan suppression radius per focus tier.
    public var spacing = TierValues(face: 2, body: 4, background: 8)
    /// Flood the watershed on the strongest per-channel Sobel magnitude instead
    /// of the gray one, so a color boundary with little luma contrast (an
    /// orange band on skin) still walls off its own basin. Off = paper/browser
    /// behavior.
    public var colorEdges = false

    public init() {}
}

public struct Markers {
    public var indices: [Int]
    public var faceCount: Int
    public var bodyCount: Int
    public var backgroundCount: Int
}

public struct Segmentation {
    public var labels: [UInt32]
    public var regionCount: Int
}

public struct RotoscopeFrame {
    /// RGBA, straight (non-premultiplied) alpha.
    public var rgba: [UInt8]
    public var markers: Markers
    public var regionCount: Int
    /// Basin label per pixel (0 = barrier) and the Rec. 601 gray, for metrics.
    public var labels: [UInt32]
    public var gray: [UInt8]
}

public enum Engine {
    public static let maxPixels = 2048 * 2048

    @inline(__always)
    public static func rec601Gray(_ red: Int, _ green: Int, _ blue: Int) -> Int {
        (77 * red + 150 * green + 29 * blue + 128) >> 8
    }

    public static func grayscale(rgba: [UInt8], count: Int) -> [UInt8] {
        var gray = [UInt8](repeating: 0, count: count)
        rgba.withUnsafeBufferPointer { src in
            gray.withUnsafeMutableBufferPointer { dst in
                var offset = 0
                for index in 0..<count {
                    dst[index] = UInt8(
                        rec601Gray(Int(src[offset]), Int(src[offset + 1]), Int(src[offset + 2])))
                    offset += 4
                }
            }
        }
        return gray
    }

    /// Separable box blur with the same edge clamping and rounding as the TS engine.
    public static func boxBlur(_ source: [UInt8], width: Int, height: Int, radius: Int) -> [UInt8] {
        let count = width * height
        var horizontal = [UInt16](repeating: 0, count: count)
        var output = [UInt8](repeating: 0, count: count)
        source.withUnsafeBufferPointer { src in
            horizontal.withUnsafeMutableBufferPointer { mid in
                for y in 0..<height {
                    let row = y * width
                    var sum = 0
                    var right = min(width - 1, radius)
                    for x in 0...right { sum += Int(src[row + x]) }
                    for x in 0..<width {
                        let left = max(0, x - radius)
                        right = min(width - 1, x + radius)
                        mid[row + x] = UInt16(roundHalfUp(Double(sum) / Double(right - left + 1)))
                        let remove = x - radius
                        let add = x + radius + 1
                        if remove >= 0 { sum -= Int(src[row + remove]) }
                        if add < width { sum += Int(src[row + add]) }
                    }
                }
            }
        }
        horizontal.withUnsafeBufferPointer { mid in
            output.withUnsafeMutableBufferPointer { dst in
                for x in 0..<width {
                    var sum = 0
                    var bottom = min(height - 1, radius)
                    for y in 0...bottom { sum += Int(mid[y * width + x]) }
                    for y in 0..<height {
                        let top = max(0, y - radius)
                        bottom = min(height - 1, y + radius)
                        dst[y * width + x] = UInt8(roundHalfUp(Double(sum) / Double(bottom - top + 1)))
                        let remove = y - radius
                        let add = y + radius + 1
                        if remove >= 0 { sum -= Int(mid[remove * width + x]) }
                        if add < height { sum += Int(mid[add * width + x]) }
                    }
                }
            }
        }
        return output
    }

    /// Box blur normalized by a binary mask: pixels outside the mask neither
    /// contribute nor are averaged, so a lifted subject's silhouette does not
    /// become the strongest "texture" in the difference image. Separable
    /// approximation: each pass normalizes by the mask along its own axis.
    public static func boxBlurMasked(
        _ source: [UInt8], mask: [UInt8], width: Int, height: Int, radius: Int
    ) -> [UInt8] {
        let count = width * height
        var horizontal = [UInt16](repeating: 0, count: count)
        var output = [UInt8](repeating: 0, count: count)
        source.withUnsafeBufferPointer { src in
            mask.withUnsafeBufferPointer { inside in
                horizontal.withUnsafeMutableBufferPointer { mid in
                    for y in 0..<height {
                        let row = y * width
                        var sum = 0
                        var weight = 0
                        var right = min(width - 1, radius)
                        for x in 0...right where inside[row + x] != 0 {
                            sum += Int(src[row + x])
                            weight += 1
                        }
                        for x in 0..<width {
                            right = min(width - 1, x + radius)
                            mid[row + x] = weight > 0
                                ? UInt16(roundHalfUp(Double(sum) / Double(weight)))
                                : UInt16(src[row + x])
                            let remove = x - radius
                            let add = x + radius + 1
                            if remove >= 0 && inside[row + remove] != 0 {
                                sum -= Int(src[row + remove])
                                weight -= 1
                            }
                            if add < width && inside[row + add] != 0 {
                                sum += Int(src[row + add])
                                weight += 1
                            }
                        }
                    }
                }
            }
        }
        horizontal.withUnsafeBufferPointer { mid in
            mask.withUnsafeBufferPointer { inside in
                output.withUnsafeMutableBufferPointer { dst in
                    for x in 0..<width {
                        var sum = 0
                        var weight = 0
                        var bottom = min(height - 1, radius)
                        for y in 0...bottom where inside[y * width + x] != 0 {
                            sum += Int(mid[y * width + x])
                            weight += 1
                        }
                        for y in 0..<height {
                            bottom = min(height - 1, y + radius)
                            dst[y * width + x] = weight > 0
                                ? UInt8(roundHalfUp(Double(sum) / Double(weight)))
                                : UInt8(mid[y * width + x])
                            let remove = y - radius
                            let add = y + radius + 1
                            if remove >= 0 && inside[remove * width + x] != 0 {
                                sum -= Int(mid[remove * width + x])
                                weight -= 1
                            }
                            if add < height && inside[add * width + x] != 0 {
                                sum += Int(mid[add * width + x])
                                weight += 1
                            }
                        }
                    }
                }
            }
        }
        return output
    }

    /// JavaScript `Math.round` semantics for the non-negative values used here.
    @inline(__always)
    static func roundHalfUp(_ value: Double) -> Int {
        Int((value + 0.5).rounded(.down))
    }

    public static func absoluteDifference(_ gray: [UInt8], _ blurred: [UInt8]) -> [UInt8] {
        var difference = [UInt8](repeating: 0, count: gray.count)
        for index in 0..<gray.count {
            difference[index] = UInt8(abs(Int(gray[index]) - Int(blurred[index])))
        }
        return difference
    }

    public struct Gradient {
        public var x: [Int16]
        public var y: [Int16]
        public var magnitude: [UInt8]
    }

    /// Integer 3×3 Sobel; magnitude is (|gx| + |gy| + 2) >> 2 clamped to 255.
    public static func sobel(_ source: [UInt8], width: Int, height: Int) -> Gradient {
        let count = width * height
        var gx = [Int16](repeating: 0, count: count)
        var gy = [Int16](repeating: 0, count: count)
        var magnitude = [UInt8](repeating: 0, count: count)
        guard width >= 3, height >= 3 else { return Gradient(x: gx, y: gy, magnitude: magnitude) }
        source.withUnsafeBufferPointer { src in
            gx.withUnsafeMutableBufferPointer { dx in
                gy.withUnsafeMutableBufferPointer { dy in
                    magnitude.withUnsafeMutableBufferPointer { mag in
                        for y in 1..<(height - 1) {
                            let row = y * width
                            for x in 1..<(width - 1) {
                                let index = row + x
                                let topLeft = Int(src[index - width - 1])
                                let top = Int(src[index - width])
                                let topRight = Int(src[index - width + 1])
                                let left = Int(src[index - 1])
                                let right = Int(src[index + 1])
                                let bottomLeft = Int(src[index + width - 1])
                                let bottom = Int(src[index + width])
                                let bottomRight = Int(src[index + width + 1])
                                let horizontal = -topLeft + topRight - 2 * left + 2 * right - bottomLeft + bottomRight
                                let vertical = -topLeft - 2 * top - topRight + bottomLeft + 2 * bottom + bottomRight
                                dx[index] = Int16(horizontal)
                                dy[index] = Int16(vertical)
                                mag[index] = UInt8(min(255, (abs(horizontal) + abs(vertical) + 2) >> 2))
                            }
                        }
                    }
                }
            }
        }
        return Gradient(x: gx, y: gy, magnitude: magnitude)
    }

    @inline(__always)
    static func minimumEigenvalue(_ a: Double, _ b: Double, _ c: Double) -> Float {
        let trace = a + c
        let discriminant = ((a - c) * (a - c) + 4 * b * b).squareRoot()
        return Float(max(0, (trace - discriminant) * 0.5))
    }

    /// Shi–Tomasi corner score on a 3×3 structure-tensor window of the difference gradients.
    public static func shiTomasiScores(difference: [UInt8], width: Int, height: Int) -> [Float] {
        let gradients = sobel(difference, width: width, height: height)
        let count = width * height
        var scores = [Float](repeating: 0, count: count)
        guard width >= 5, height >= 5 else { return scores }
        gradients.x.withUnsafeBufferPointer { gx in
            gradients.y.withUnsafeBufferPointer { gy in
                scores.withUnsafeMutableBufferPointer { out in
                    for y in 2..<(height - 2) {
                        for x in 2..<(width - 2) {
                            var xx = 0.0
                            var xy = 0.0
                            var yy = 0.0
                            for dy in -1...1 {
                                var index = (y + dy) * width + x - 1
                                for _ in 0..<3 {
                                    let dxv = Double(gx[index])
                                    let dyv = Double(gy[index])
                                    xx += dxv * dxv
                                    xy += dxv * dyv
                                    yy += dyv * dyv
                                    index += 1
                                }
                            }
                            out[y * width + x] = minimumEigenvalue(xx, xy, yy)
                        }
                    }
                }
            }
        }
        return scores
    }

    static func tierQuota(budget: Int, quotas: TierValues) -> [Int] {
        var face = max(0, quotas.face)
        var body = max(0, quotas.body)
        var background = max(0, quotas.background)
        var sum = face + body + background
        if sum <= 0 {
            face = 0.5
            body = 0.3
            background = 0.2
            sum = 1
        }
        let exact = [face / sum, body / sum, background / sum].map { Double(budget) * $0 }
        var allocated = exact.map { Int($0.rounded(.down)) }
        var remaining = budget - allocated.reduce(0, +)
        let order = (0..<3).sorted { left, right in
            let remainderLeft = exact[left] - Double(allocated[left])
            let remainderRight = exact[right] - Double(allocated[right])
            return remainderLeft > remainderRight || (remainderLeft == remainderRight && left < right)
        }
        for index in order where remaining > 0 {
            allocated[index] += 1
            remaining -= 1
        }
        return allocated
    }

    @inline(__always)
    static func isLocalMaximum(_ scores: UnsafeBufferPointer<Float>, _ index: Int, _ width: Int) -> Bool {
        let score = scores[index]
        for dy in -1...1 {
            for dx in -1...1 {
                if dx == 0 && dy == 0 { continue }
                let neighbor = index + dy * width + dx
                let neighborScore = scores[neighbor]
                if neighborScore > score || (neighborScore == score && neighbor < index) {
                    return false
                }
            }
        }
        return true
    }

    static func blockDiamond(_ blocked: inout [UInt8], index: Int, width: Int, height: Int, radius: Int) {
        let centerY = index / width
        let centerX = index - centerY * width
        for dy in -radius...radius {
            let y = centerY + dy
            if y < 0 || y >= height { continue }
            let horizontal = radius - abs(dy)
            for dx in -horizontal...horizontal {
                let x = centerX + dx
                if x >= 0 && x < width { blocked[y * width + x] = 1 }
            }
        }
    }

    static func candidates(
        tier: UInt8, scores: [Float], width: Int, height: Int, tiers: [UInt8], spacing: Int
    ) -> [Int] {
        let count = width * height
        var seen = [UInt8](repeating: 0, count: count)
        var found: [Int] = []
        scores.withUnsafeBufferPointer { score in
            tiers.withUnsafeBufferPointer { tierMap in
                // Strong local maxima preserve the Shi–Tomasi ranking.
                if width >= 5 && height >= 5 {
                    for y in 2..<(height - 2) {
                        for x in 2..<(width - 2) {
                            let index = y * width + x
                            if score[index] > 0 && tierMap[index] == tier && isLocalMaximum(score, index, width) {
                                found.append(index)
                                seen[index] = 1
                            }
                        }
                    }
                }
                // A best candidate per spatial cell guarantees coverage on smooth tiers.
                let cell = max(2, spacing)
                var tileY = 0
                while tileY < height {
                    var tileX = 0
                    let yEnd = min(height, tileY + cell)
                    while tileX < width {
                        let xEnd = min(width, tileX + cell)
                        var bestIndex = -1
                        var bestScore: Float = -1
                        for y in tileY..<yEnd {
                            for x in tileX..<xEnd {
                                let index = y * width + x
                                if tierMap[index] != tier { continue }
                                let value = score[index]
                                if value > bestScore || (value == bestScore && index < bestIndex) {
                                    bestIndex = index
                                    bestScore = value
                                }
                            }
                        }
                        if bestIndex >= 0 && seen[bestIndex] == 0 {
                            found.append(bestIndex)
                            seen[bestIndex] = 1
                        }
                        tileX += cell
                    }
                    tileY += cell
                }
            }
        }
        found.sort { left, right in
            scores[left] > scores[right] || (scores[left] == scores[right] && left < right)
        }
        return found
    }

    /// Tiered marker selection. `tiers` is a per-pixel `FocusTier` raw value.
    /// `seeds` are marker positions carried from the previous frame (already
    /// moved by optical flow); they are accepted first, within their tier's
    /// quota, so basins keep their identity from frame to frame.
    public static func selectMarkers(
        scores: [Float], width: Int, height: Int, tiers: [UInt8], options: EngineOptions, seeds: [Int] = []
    ) -> Markers {
        let count = width * height
        precondition(scores.count == count && tiers.count == count, "score/tier length mismatch")
        let budget = max(1, min(options.markerBudget, count))
        let quotas = tierQuota(budget: budget, quotas: options.quotas)
        let spacings = [options.spacing.face, options.spacing.body, options.spacing.background]
            .map { max(1, min(64, Int($0.rounded()))) }
        var blocked = [UInt8](repeating: 0, count: count)
        var markers: [Int] = []
        var counts = [0, 0, 0]

        for seed in seeds where seed >= 0 && seed < count {
            let tier = Int(tiers[seed])
            if tier > 2 || quotas[tier] <= 0 || counts[tier] >= quotas[tier] || blocked[seed] != 0 { continue }
            markers.append(seed)
            counts[tier] += 1
            blockDiamond(&blocked, index: seed, width: width, height: height, radius: spacings[tier])
        }

        for tier in 0..<3 {
            if quotas[tier] <= 0 { continue }
            let found = candidates(
                tier: UInt8(tier), scores: scores, width: width, height: height,
                tiers: tiers, spacing: spacings[tier])
            for index in found {
                if counts[tier] >= quotas[tier] { break }
                if blocked[index] != 0 { continue }
                markers.append(index)
                counts[tier] += 1
                blockDiamond(&blocked, index: index, width: width, height: height, radius: spacings[tier])
            }
        }
        // Degenerate frames still need one marker so every pixel is labeled.
        if markers.isEmpty {
            markers.append(count / 2)
            counts[2] = 1
        }
        return Markers(indices: markers, faceCount: counts[0], bodyCount: counts[1], backgroundCount: counts[2])
    }

    /// Deterministic marker-controlled minimum-barrier flood over 256 FIFO buckets.
    /// Pixels with `barrier[i] != 0` are never entered and keep label 0.
    public static func watershed(
        gradient: [UInt8], width: Int, height: Int, markers: [Int], barrier: [UInt8]?
    ) -> Segmentation {
        let count = width * height
        precondition(gradient.count == count, "gradient length mismatch")
        var labels = [UInt32](repeating: 0, count: count)
        var queued = [UInt8](repeating: 0, count: count)
        var next = [Int32](repeating: -1, count: count)
        var heads = [Int32](repeating: -1, count: 256)
        var tails = [Int32](repeating: -1, count: 256)
        var regionCount: UInt32 = 0
        var visited = 0

        if let barrier {
            for index in 0..<count where barrier[index] != 0 {
                queued[index] = 1
                visited += 1
            }
        }

        @inline(__always)
        func enqueue(_ index: Int, _ level: Int) {
            if heads[level] < 0 { heads[level] = Int32(index) } else { next[Int(tails[level])] = Int32(index) }
            tails[level] = Int32(index)
        }

        for index in markers where index >= 0 && index < count && queued[index] == 0 {
            regionCount += 1
            labels[index] = regionCount
            queued[index] = 1
            visited += 1
            enqueue(index, 0)
        }
        if regionCount == 0 {
            // Every reachable pixel must carry a label; seed the first open pixel.
            var seed = count / 2
            if queued[seed] != 0, let open = queued.firstIndex(of: 0) { seed = open }
            if queued[seed] == 0 {
                regionCount = 1
                labels[seed] = 1
                queued[seed] = 1
                visited += 1
                enqueue(seed, 0)
            }
        }

        let neighborX = [-1, 0, 1, -1, 1, -1, 0, 1]
        let neighborY = [-1, -1, -1, 0, 0, 1, 1, 1]
        var level = 0
        gradient.withUnsafeBufferPointer { grad in
            while visited < count {
                while level < 256 && heads[level] < 0 { level += 1 }
                if level >= 256 { break }
                let index = Int(heads[level])
                heads[level] = next[index]
                if heads[level] < 0 { tails[level] = -1 }
                next[index] = -1
                let y = index / width
                let x = index - y * width
                let label = labels[index]
                for neighbor in 0..<8 {
                    let nx = x + neighborX[neighbor]
                    let ny = y + neighborY[neighbor]
                    if nx < 0 || nx >= width || ny < 0 || ny >= height { continue }
                    let nextIndex = ny * width + nx
                    if queued[nextIndex] != 0 { continue }
                    queued[nextIndex] = 1
                    labels[nextIndex] = label
                    visited += 1
                    let nextLevel = max(level, Int(grad[nextIndex]))
                    enqueue(nextIndex, nextLevel)
                }
            }
        }
        return Segmentation(labels: labels, regionCount: Int(regionCount))
    }

    /// One mean source color per basin. Label 0 (barrier) pixels get `alpha` 0
    /// when `alpha` is supplied, otherwise they keep their source color.
    public static func colorize(
        rgba: [UInt8], labels: [UInt32], count: Int, regionCount: Int, alpha: [UInt8]?
    ) -> [UInt8] {
        var red = [UInt32](repeating: 0, count: regionCount + 1)
        var green = [UInt32](repeating: 0, count: regionCount + 1)
        var blue = [UInt32](repeating: 0, count: regionCount + 1)
        var population = [UInt32](repeating: 0, count: regionCount + 1)
        var offset = 0
        for index in 0..<count {
            let label = Int(labels[index])
            if label != 0 && label <= regionCount {
                red[label] &+= UInt32(rgba[offset])
                green[label] &+= UInt32(rgba[offset + 1])
                blue[label] &+= UInt32(rgba[offset + 2])
                population[label] += 1
            }
            offset += 4
        }
        var output = [UInt8](repeating: 0, count: rgba.count)
        offset = 0
        for index in 0..<count {
            let label = Int(labels[index])
            let size = label <= regionCount ? Int(population[label]) : 0
            if size > 0 {
                output[offset] = UInt8(roundHalfUp(Double(red[label]) / Double(size)))
                output[offset + 1] = UInt8(roundHalfUp(Double(green[label]) / Double(size)))
                output[offset + 2] = UInt8(roundHalfUp(Double(blue[label]) / Double(size)))
            } else {
                output[offset] = rgba[offset]
                output[offset + 1] = rgba[offset + 1]
                output[offset + 2] = rgba[offset + 2]
            }
            if let alpha {
                if label == 0 {
                    output[offset] = 0
                    output[offset + 1] = 0
                    output[offset + 2] = 0
                    output[offset + 3] = 0
                } else {
                    output[offset + 3] = alpha[index]
                }
            } else {
                output[offset + 3] = rgba[offset + 3]
            }
            offset += 4
        }
        return output
    }

    /// Full single-frame pass. `tiers` assigns every pixel a `FocusTier`; when
    /// `removeBackground` is set, background-tier pixels are a watershed
    /// barrier and come out transparent, with `alpha` (soft mask, 0–255) as
    /// the foreground alpha.
    public static func process(
        rgba: [UInt8], width: Int, height: Int, tiers: [UInt8], alpha: [UInt8]?,
        removeBackground: Bool, options: EngineOptions, seeds: [Int] = []
    ) -> RotoscopeFrame {
        let count = width * height
        precondition(rgba.count == count * 4, "RGBA byte length mismatch")
        precondition(count <= maxPixels, "frame exceeds the pixel limit")
        let gray = grayscale(rgba: rgba, count: count)
        let blurRadius = max(1, min(64, options.blurRadius))
        var subject: [UInt8]? = nil
        if removeBackground {
            subject = tiers.map { $0 == FocusTier.background.rawValue ? 0 : 1 }
        }
        let blurred = subject.map { boxBlurMasked(gray, mask: $0, width: width, height: height, radius: blurRadius) }
            ?? boxBlur(gray, width: width, height: height, radius: blurRadius)
        let difference = absoluteDifference(gray, blurred)
        let scores = shiTomasiScores(difference: difference, width: width, height: height)
        let markers = selectMarkers(scores: scores, width: width, height: height, tiers: tiers, options: options, seeds: seeds)
        var gradient = sobel(gray, width: width, height: height).magnitude
        if options.colorEdges {
            for channel in 0..<3 {
                var plane = [UInt8](repeating: 0, count: count)
                var offset = channel
                for index in 0..<count {
                    plane[index] = rgba[offset]
                    offset += 4
                }
                let magnitude = sobel(plane, width: width, height: height).magnitude
                for index in 0..<count where magnitude[index] > gradient[index] {
                    gradient[index] = magnitude[index]
                }
            }
        }
        let barrier: [UInt8]? = subject.map { $0.map { $0 == 0 ? 1 : 0 } }
        let segmented = watershed(
            gradient: gradient, width: width, height: height, markers: markers.indices, barrier: barrier)
        let pixels = colorize(
            rgba: rgba, labels: segmented.labels, count: count, regionCount: segmented.regionCount,
            alpha: removeBackground ? (alpha ?? [UInt8](repeating: 255, count: count)) : nil)
        return RotoscopeFrame(
            rgba: pixels, markers: markers, regionCount: segmented.regionCount, labels: segmented.labels, gray: gray)
    }
}
