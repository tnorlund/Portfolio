import Foundation

/// A clean background frame recovered from a static-camera clip: the per-pixel,
/// per-channel temporal median of a sample of frames. Anything the subject
/// carries through the shot (a barbell, a band) is never in the same place
/// for most of the clip, so it drops out and the plate is the empty room —
/// the clean background frame the 2017 paper assumed.
public struct BackgroundPlate {
    public let width: Int
    public let height: Int
    /// Packed RGB, 3 bytes per pixel.
    public let rgb: [UInt8]
    /// 1 where at least one sample saw the background at this pixel; 0 where
    /// the subject covered it in every sample (no plate, so no difference).
    public let valid: [UInt8]

    public init(width: Int, height: Int, rgb: [UInt8], valid: [UInt8]? = nil) {
        self.width = width
        self.height = height
        self.rgb = rgb
        self.valid = valid ?? [UInt8](repeating: 1, count: width * height)
    }

    /// Builds the plate from RGBA frame samples (all the same size). Samples
    /// with alpha 0 at a pixel (a warped frame that does not cover it) are
    /// left out of that pixel's median.
    public static func median(width: Int, height: Int, samples: [[UInt8]]) -> BackgroundPlate {
        precondition(!samples.isEmpty, "need at least one frame sample")
        let count = width * height
        var rgb = [UInt8](repeating: 0, count: count * 3)
        var valid = [UInt8](repeating: 1, count: count)
        var column = [UInt8](repeating: 0, count: samples.count)
        for index in 0..<count {
            let offset = index * 4
            for channel in 0..<3 {
                var filled = 0
                for sample in samples where sample[offset + 3] != 0 {
                    column[filled] = sample[offset + channel]
                    filled += 1
                }
                if filled == 0 {
                    valid[index] = 0
                    continue
                }
                column[0..<filled].sort()
                let mid = filled / 2
                rgb[index * 3 + channel] = filled % 2 == 1
                    ? column[mid]
                    : UInt8((Int(column[mid - 1]) + Int(column[mid]) + 1) / 2)
            }
        }
        return BackgroundPlate(width: width, height: height, rgb: rgb, valid: valid)
    }

    /// The plate as RGBA, alpha 255 where valid and 0 where no sample saw the
    /// background, for warping and dumping.
    public var rgba: [UInt8] {
        var out = [UInt8](repeating: 0, count: width * height * 4)
        for index in 0..<(width * height) {
            out[index * 4] = rgb[index * 3]
            out[index * 4 + 1] = rgb[index * 3 + 1]
            out[index * 4 + 2] = rgb[index * 3 + 2]
            out[index * 4 + 3] = valid[index] != 0 ? 255 : 0
        }
        return out
    }

    /// Per-pixel distance from a warped copy of the plate (RGBA; alpha 0 where
    /// the warp left no plate, which counts as no difference).
    public static func difference(rgba: [UInt8], warpedPlate: [UInt8]) -> [UInt8] {
        let count = rgba.count / 4
        var out = [UInt8](repeating: 0, count: count)
        for index in 0..<count {
            let a = index * 4
            if warpedPlate[a + 3] == 0 { continue }
            let dr = abs(Int(rgba[a]) - Int(warpedPlate[a]))
            let dg = abs(Int(rgba[a + 1]) - Int(warpedPlate[a + 1]))
            let db = abs(Int(rgba[a + 2]) - Int(warpedPlate[a + 2]))
            out[index] = UInt8(max(dr, max(dg, db)))
        }
        return out
    }

    /// Misalignment-tolerant distance from a warped plate: for every pixel, the
    /// smallest largest-channel difference against any plate pixel within
    /// `radius` (at half resolution, so 2×radius+1 source pixels). Static edges
    /// that are a few pixels off after registration, or blurred in the plate,
    /// find a match and go quiet; a chrome bar over a dark floor does not.
    public static func tolerantDifference(
        rgba: [UInt8], warpedPlate: [UInt8], width: Int, height: Int, radius: Int
    ) -> [UInt8] {
        let halfWidth = width / 2
        let halfHeight = height / 2
        let halfCount = halfWidth * halfHeight
        var frame = [UInt8](repeating: 0, count: halfCount * 3)
        var plate = [UInt8](repeating: 0, count: halfCount * 3)
        var valid = [UInt8](repeating: 0, count: halfCount)
        for hy in 0..<halfHeight {
            for hx in 0..<halfWidth {
                let h = hy * halfWidth + hx
                var fr = 0, fg = 0, fb = 0, pr = 0, pg = 0, pb = 0, pn = 0
                for dy in 0..<2 {
                    for dx in 0..<2 {
                        let offset = ((hy * 2 + dy) * width + hx * 2 + dx) * 4
                        fr += Int(rgba[offset]); fg += Int(rgba[offset + 1]); fb += Int(rgba[offset + 2])
                        if warpedPlate[offset + 3] != 0 {
                            pr += Int(warpedPlate[offset]); pg += Int(warpedPlate[offset + 1])
                            pb += Int(warpedPlate[offset + 2]); pn += 1
                        }
                    }
                }
                frame[h * 3] = UInt8(fr / 4); frame[h * 3 + 1] = UInt8(fg / 4); frame[h * 3 + 2] = UInt8(fb / 4)
                if pn > 0 {
                    plate[h * 3] = UInt8(pr / pn); plate[h * 3 + 1] = UInt8(pg / pn); plate[h * 3 + 2] = UInt8(pb / pn)
                    valid[h] = 1
                }
            }
        }
        var half = [UInt8](repeating: 0, count: halfCount)
        frame.withUnsafeBufferPointer { f in
            plate.withUnsafeBufferPointer { p in
                valid.withUnsafeBufferPointer { v in
                    for hy in 0..<halfHeight {
                        for hx in 0..<halfWidth {
                            let h = hy * halfWidth + hx
                            if v[h] == 0 { continue }
                            let fr = Int(f[h * 3]), fg = Int(f[h * 3 + 1]), fb = Int(f[h * 3 + 2])
                            var best = 255
                            let y0 = max(0, hy - radius), y1 = min(halfHeight - 1, hy + radius)
                            let x0 = max(0, hx - radius), x1 = min(halfWidth - 1, hx + radius)
                            for ny in y0...y1 {
                                var n = ny * halfWidth + x0
                                for _ in x0...x1 {
                                    if v[n] != 0 {
                                        let pr = Int(p[n * 3]), pg = Int(p[n * 3 + 1]), pb = Int(p[n * 3 + 2])
                                        let d = max(abs(fr - pr), max(abs(fg - pg), abs(fb - pb)))
                                        if d < best { best = d }
                                    }
                                    n += 1
                                }
                            }
                            half[h] = UInt8(best)
                        }
                    }
                }
            }
        }
        var out = [UInt8](repeating: 0, count: width * height)
        for y in 0..<height {
            let hy = min(halfHeight - 1, y / 2)
            for x in 0..<width {
                out[y * width + x] = half[hy * halfWidth + min(halfWidth - 1, x / 2)]
            }
        }
        return out
    }

    /// Per-pixel distance from the plate: the largest channel difference.
    public func difference(rgba: [UInt8]) -> [UInt8] {
        let count = width * height
        var out = [UInt8](repeating: 0, count: count)
        for index in 0..<count where valid[index] != 0 {
            let a = index * 4
            let b = index * 3
            let dr = abs(Int(rgba[a]) - Int(rgb[b]))
            let dg = abs(Int(rgba[a + 1]) - Int(rgb[b + 1]))
            let db = abs(Int(rgba[a + 2]) - Int(rgb[b + 2]))
            out[index] = UInt8(max(dr, max(dg, db)))
        }
        return out
    }
}

public enum Morphology {
    /// 3×3 binary erosion.
    public static func erode(_ mask: [UInt8], width: Int, height: Int) -> [UInt8] {
        var out = [UInt8](repeating: 0, count: mask.count)
        for y in 1..<(height - 1) {
            for x in 1..<(width - 1) {
                let index = y * width + x
                if mask[index] == 0 { continue }
                var keep = true
                for dy in -1...1 where keep {
                    for dx in -1...1 where mask[index + dy * width + dx] == 0 {
                        keep = false
                        break
                    }
                }
                out[index] = keep ? 1 : 0
            }
        }
        return out
    }

    /// 3×3 binary dilation.
    public static func dilate(_ mask: [UInt8], width: Int, height: Int) -> [UInt8] {
        var out = mask
        for y in 0..<height {
            for x in 0..<width {
                let index = y * width + x
                if mask[index] == 0 { continue }
                for dy in -1...1 {
                    let ny = y + dy
                    if ny < 0 || ny >= height { continue }
                    for dx in -1...1 {
                        let nx = x + dx
                        if nx < 0 || nx >= width { continue }
                        out[ny * width + nx] = 1
                    }
                }
            }
        }
        return out
    }

    /// Direct photometric refinement of the plate alignment: the integer
    /// translation (full-resolution pixels, multiples of 4) within ±`range`
    /// quarter-res steps that minimizes the robust mean channel difference
    /// between the frame and the warped plate over pixels not in `exclude`.
    /// No fill, no features: it cannot be biased by the subject, only blind to it.
    public static func refineTranslation(
        rgba: [UInt8], warpedPlate: [UInt8], exclude: [UInt8], width: Int, height: Int, range: Int
    ) -> (dx: Int, dy: Int) {
        let qw = width / 4
        let qh = height / 4
        var frame = [UInt8](repeating: 0, count: qw * qh)
        var plate = [UInt8](repeating: 0, count: qw * qh)
        var usable = [UInt8](repeating: 0, count: qw * qh)
        for qy in 0..<qh {
            for qx in 0..<qw {
                let x = qx * 4 + 2
                let y = qy * 4 + 2
                let index = y * width + x
                let offset = index * 4
                frame[qy * qw + qx] = UInt8((Int(rgba[offset]) + Int(rgba[offset + 1]) + Int(rgba[offset + 2])) / 3)
                if warpedPlate[offset + 3] != 0 && exclude[index] == 0 {
                    plate[qy * qw + qx] = UInt8((Int(warpedPlate[offset]) + Int(warpedPlate[offset + 1]) + Int(warpedPlate[offset + 2])) / 3)
                    usable[qy * qw + qx] = 1
                }
            }
        }
        var bestCost = Double.greatestFiniteMagnitude
        var best = (dx: 0, dy: 0)
        for dy in -range...range {
            for dx in -range...range {
                var sum = 0
                var n = 0
                for qy in max(0, -dy)..<min(qh, qh - dy) {
                    for qx in max(0, -dx)..<min(qw, qw - dx) {
                        let p = (qy + dy) * qw + qx + dx
                        if usable[p] == 0 { continue }
                        sum += min(64, abs(Int(frame[qy * qw + qx]) - Int(plate[p])))
                        n += 1
                    }
                }
                guard n > 0 else { continue }
                let cost = Double(sum) / Double(n) + 0.02 * Double(abs(dx) + abs(dy))
                if cost < bestCost {
                    bestCost = cost
                    best = (dx * 4, dy * 4)
                }
            }
        }
        return best
    }

    /// Shifts an RGBA image by whole pixels; uncovered pixels get alpha 0.
    public static func shifted(_ rgba: [UInt8], width: Int, height: Int, dx: Int, dy: Int) -> [UInt8] {
        if dx == 0 && dy == 0 { return rgba }
        var out = [UInt8](repeating: 0, count: rgba.count)
        for y in 0..<height {
            let sy = y + dy
            if sy < 0 || sy >= height { continue }
            for x in 0..<width {
                let sx = x + dx
                if sx < 0 || sx >= width { continue }
                let src = (sy * width + sx) * 4
                let dst = (y * width + x) * 4
                out[dst] = rgba[src]
                out[dst + 1] = rgba[src + 1]
                out[dst + 2] = rgba[src + 2]
                out[dst + 3] = rgba[src + 3]
            }
        }
        return out
    }

    /// 3×3 minimum filter (grayscale erosion).
    public static func minFilter(_ values: [UInt8], width: Int, height: Int) -> [UInt8] {
        var out = values
        for y in 0..<height {
            for x in 0..<width {
                var best = values[y * width + x]
                for dy in -1...1 {
                    let ny = y + dy
                    if ny < 0 || ny >= height { continue }
                    for dx in -1...1 {
                        let nx = x + dx
                        if nx < 0 || nx >= width { continue }
                        let v = values[ny * width + nx]
                        if v < best { best = v }
                    }
                }
                out[y * width + x] = best
            }
        }
        return out
    }

    /// Marks the 8-connected components of `mask` whose minimum `age` exceeds
    /// `maxAge`.
    public static func componentsTooOld(
        _ mask: [UInt8], age: [UInt8], maxAge: UInt8, width: Int, height: Int
    ) -> [UInt8] {
        let count = width * height
        var label = [UInt8](repeating: 0, count: count)
        var out = [UInt8](repeating: 0, count: count)
        var stack: [Int] = []
        var component: [Int] = []
        for start in 0..<count where mask[start] != 0 && label[start] == 0 {
            stack.removeAll(keepingCapacity: true)
            component.removeAll(keepingCapacity: true)
            stack.append(start)
            label[start] = 1
            var youngest: UInt8 = 255
            while let index = stack.popLast() {
                component.append(index)
                if age[index] < youngest { youngest = age[index] }
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
            if youngest > maxAge {
                for index in component { out[index] = 1 }
            }
        }
        return out
    }

    /// Horizontal closing by run length: zero runs shorter than `gap` between
    /// ones on the same row are filled. Bridges a fragmented horizontal bar in
    /// the connectivity graph without thickening anything.
    public static func bridgeRows(_ mask: [UInt8], width: Int, height: Int, gap: Int) -> [UInt8] {
        var out = mask
        for y in 0..<height {
            let row = y * width
            var lastOne = -1
            for x in 0..<width {
                if mask[row + x] != 0 {
                    if lastOne >= 0 && x - lastOne - 1 < gap && x - lastOne > 1 {
                        for fill in (lastOne + 1)..<x { out[row + fill] = 1 }
                    }
                    lastOne = x
                }
            }
        }
        return out
    }

    /// Fills holes of `mask`: 4-connected background pockets that touch neither
    /// the frame border nor any `keepOpen` pixel. A black plate over a black
    /// rack loses its interior to the difference threshold; its rim still
    /// encloses it, so it fills. A pocket between an arm and the chest borders
    /// person pixels, so it stays open.
    public static func fillHoles(
        _ mask: [UInt8], keepOpen: [UInt8], width: Int, height: Int
    ) -> [UInt8] {
        let count = width * height
        // 0 = unvisited background, 1 = outside (reachable from border/keepOpen), 2 = mask
        var state = [UInt8](repeating: 0, count: count)
        var stack: [Int] = []
        for index in 0..<count {
            if mask[index] != 0 {
                state[index] = 2
            } else {
                let y = index / width
                let x = index - y * width
                if x == 0 || y == 0 || x == width - 1 || y == height - 1 || keepOpen[index] != 0 {
                    state[index] = 1
                    stack.append(index)
                }
            }
        }
        while let index = stack.popLast() {
            let y = index / width
            let x = index - y * width
            if x > 0 && state[index - 1] == 0 { state[index - 1] = 1; stack.append(index - 1) }
            if x < width - 1 && state[index + 1] == 0 { state[index + 1] = 1; stack.append(index + 1) }
            if y > 0 && state[index - width] == 0 { state[index - width] = 1; stack.append(index - width) }
            if y < height - 1 && state[index + width] == 0 { state[index + width] = 1; stack.append(index + width) }
        }
        var out = mask
        for index in 0..<count where state[index] == 0 { out[index] = 1 }
        return out
    }

    /// Keeps only the 8-connected components of `mask` that contain at least
    /// one pixel of `anchor`. Returns a binary mask.
    public static func componentsTouching(
        _ mask: [UInt8], anchor: [UInt8], width: Int, height: Int
    ) -> [UInt8] {
        let count = width * height
        var label = [Int32](repeating: 0, count: count)
        var out = [UInt8](repeating: 0, count: count)
        var stack: [Int] = []
        var component: [Int] = []
        var next: Int32 = 0
        for start in 0..<count where mask[start] != 0 && label[start] == 0 {
            next += 1
            stack.removeAll(keepingCapacity: true)
            component.removeAll(keepingCapacity: true)
            stack.append(start)
            label[start] = next
            var touches = false
            while let index = stack.popLast() {
                component.append(index)
                if anchor[index] != 0 { touches = true }
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
                            label[neighbor] = next
                            stack.append(neighbor)
                        }
                    }
                }
            }
            if touches {
                for index in component { out[index] = 1 }
            }
        }
        return out
    }
}
