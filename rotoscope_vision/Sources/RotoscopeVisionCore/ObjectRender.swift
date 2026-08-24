import Foundation
import simd

/// Turns tracked objects into pixels. Deformable (and not-yet-decided)
/// objects are grown every frame from their own tracks; rigid objects capture
/// a template once, at the frame where they are best supported, and render it
/// by their tracked transform so their interior never depends on per-frame
/// evidence again. No shape prior anywhere: the support is the tracks, the
/// edges are the image's.
public enum ObjectRender {
    /// Watershed-grown mask of an object from its member tracks inside a crop.
    /// Returns a full-frame binary mask (empty when there is nothing to grow).
    // swiftlint:disable:next function_body_length
    public static func grow(
        points: [SIMD2<Float>], rgba: [UInt8], difference: [UInt8]?, person: [UInt8], others: [UInt8]?,
        width: Int, height: Int, params: Params
    ) -> [UInt8] {
        let count = width * height
        var out = [UInt8](repeating: 0, count: count)
        guard points.count >= 3 else { return out }
        let pad = Int(params.hullRadius) + 24
        var minX = width, maxX = 0, minY = height, maxY = 0
        for p in points {
            minX = min(minX, Int(p.x)); maxX = max(maxX, Int(p.x))
            minY = min(minY, Int(p.y)); maxY = max(maxY, Int(p.y))
        }
        minX = max(0, minX - pad); minY = max(0, minY - pad)
        maxX = min(width - 1, maxX + pad); maxY = min(height - 1, maxY + pad)
        let cw = maxX - minX + 1, ch = maxY - minY + 1
        guard cw >= 8, ch >= 8 else { return out }
        // Colour gradient of the crop.
        var planes = [[UInt8]](repeating: [UInt8](repeating: 0, count: cw * ch), count: 3)
        var cropGray = [UInt8](repeating: 0, count: cw * ch)
        for y in 0..<ch {
            for x in 0..<cw {
                let o = ((minY + y) * width + minX + x) * 4
                planes[0][y * cw + x] = rgba[o]; planes[1][y * cw + x] = rgba[o + 1]; planes[2][y * cw + x] = rgba[o + 2]
                cropGray[y * cw + x] = UInt8(Engine.rec601Gray(Int(rgba[o]), Int(rgba[o + 1]), Int(rgba[o + 2])))
            }
        }
        var gradient = Engine.sobel(cropGray, width: cw, height: ch).magnitude
        for plane in planes {
            let m = Engine.sobel(plane, width: cw, height: ch).magnitude
            for i in 0..<gradient.count where m[i] > gradient[i] { gradient[i] = m[i] }
        }
        // Markers: object tracks, then the crop border and plate-agreeing
        // pixels as background, then person pixels as subject.
        var markers: [Int] = []
        var markerClass: [UInt8] = []  // 1 object, 2 background, 3 subject
        var seeded = [UInt8](repeating: 0, count: cw * ch)
        for p in points {
            let x = Int(p.x.rounded()) - minX, y = Int(p.y.rounded()) - minY
            if x < 0 || y < 0 || x >= cw || y >= ch { continue }
            let i = y * cw + x
            if seeded[i] != 0 { continue }
            seeded[i] = 1; markers.append(i); markerClass.append(1)
        }
        let step = 6
        for y in stride(from: 0, to: ch, by: step) {
            for x in stride(from: 0, to: cw, by: step) {
                let i = y * cw + x
                let full = (minY + y) * width + minX + x
                let border = x < 3 || y < 3 || x >= cw - 3 || y >= ch - 3
                let quiet = difference.map { Int($0[full]) < Int(params.plateThreshold) / 2 } ?? false
                let other = others?[full] != 0
                if person[full] != 0 {
                    if seeded[i] == 0 { seeded[i] = 1; markers.append(i); markerClass.append(3) }
                } else if border || other || (quiet && !near(points, x: minX + x, y: minY + y, within: Float(params.hullRadius) * 2)) {
                    if seeded[i] == 0 { seeded[i] = 1; markers.append(i); markerClass.append(2) }
                }
            }
        }
        guard markerClass.contains(1) else { return out }
        let segmentation = Engine.watershed(gradient: gradient, width: cw, height: ch, markers: markers, barrier: nil)
        // Object basins, gated by evidence: difference or proximity to a track.
        for y in 0..<ch {
            for x in 0..<cw {
                let label = Int(segmentation.labels[y * cw + x])
                guard label > 0, label <= markerClass.count, markerClass[label - 1] == 1 else { continue }
                let full = (minY + y) * width + minX + x
                if person[full] != 0 { continue }
                let evidence = difference.map { Int($0[full]) >= Int(params.plateThreshold) / 3 } ?? true
                if evidence || near(points, x: minX + x, y: minY + y, within: 8) { out[full] = 1 }
            }
        }
        // Keep the components that contain a track; drop stray basins.
        var anchor = [UInt8](repeating: 0, count: count)
        for p in points {
            let x = Int(p.x.rounded()), y = Int(p.y.rounded())
            if x >= 0 && y >= 0 && x < width && y < height { anchor[y * width + x] = 1 }
        }
        return Morphology.componentsTouching(out, anchor: anchor, width: width, height: height)
    }

    /// How well a grown mask is supported: mean difference evidence inside it
    /// times the number of tracks that landed inside it. Larger is better.
    public static func support(mask: [UInt8], difference: [UInt8]?, points: [SIMD2<Float>]) -> Float {
        var area = 0
        var evidence = 0
        for i in 0..<mask.count where mask[i] != 0 {
            area += 1
            evidence += Int(difference?[i] ?? 255)
        }
        guard area > 0 else { return 0 }
        return Float(evidence) / Float(area) * Float(points.count)
    }

    @inline(__always)
    private static func near(_ points: [SIMD2<Float>], x: Int, y: Int, within: Float) -> Bool {
        let q = SIMD2<Float>(Float(x), Float(y))
        for p in points where simd_distance(p, q) <= within { return true }
        return false
    }

    /// Capture a rigid template from the current frame: the grown mask and
    /// the frame's colours inside it, in this frame's coordinates, plus the
    /// transform at capture so later frames can map back.
    public static func capture(mask: [UInt8], rgba: [UInt8], width: Int, height: Int, frame: Int) -> RigidTemplate? {
        var minX = width, maxX = -1, minY = height, maxY = -1
        for y in 0..<height {
            for x in 0..<width where mask[y * width + x] != 0 {
                minX = min(minX, x); maxX = max(maxX, x); minY = min(minY, y); maxY = max(maxY, y)
            }
        }
        guard maxX >= minX, maxY >= minY else { return nil }
        let tw = maxX - minX + 1, th = maxY - minY + 1
        var tmask = [UInt8](repeating: 0, count: tw * th)
        var trgb = [UInt8](repeating: 0, count: tw * th * 3)
        for y in 0..<th {
            for x in 0..<tw {
                let full = (minY + y) * width + minX + x
                tmask[y * tw + x] = mask[full] != 0 ? 255 : 0
                trgb[(y * tw + x) * 3] = rgba[full * 4]
                trgb[(y * tw + x) * 3 + 1] = rgba[full * 4 + 1]
                trgb[(y * tw + x) * 3 + 2] = rgba[full * 4 + 2]
            }
        }
        return RigidTemplate(originX: minX, originY: minY, width: tw, height: th, mask: tmask, rgb: trgb, capturedFrame: frame)
    }

    /// Render a template captured under `captureTransform` into the frame
    /// under `transform`: p_now = T_now · T_cap⁻¹ · p_cap. Returns alpha
    /// (0–255) and the photometric residual against the frame.
    public static func render(
        template: RigidTemplate, captureTransform: Similarity, transform: Similarity, rgba: [UInt8], person: [UInt8],
        width: Int, height: Int
    ) -> (alpha: [UInt8], photoResidual: Double?) {
        var alpha = [UInt8](repeating: 0, count: width * height)
        let toCapture = captureTransform * transform.inverse  // frame → capture frame
        // Bounding box of the transformed template corners.
        let forward = transform * captureTransform.inverse
        let corners = [
            SIMD2<Float>(Float(template.originX), Float(template.originY)),
            SIMD2<Float>(Float(template.originX + template.width), Float(template.originY)),
            SIMD2<Float>(Float(template.originX), Float(template.originY + template.height)),
            SIMD2<Float>(Float(template.originX + template.width), Float(template.originY + template.height)),
        ].map { forward.apply($0) }
        let minX = max(0, Int(corners.map { $0.x }.min()!.rounded(.down)) - 1)
        let maxX = min(width - 1, Int(corners.map { $0.x }.max()!.rounded(.up)) + 1)
        let minY = max(0, Int(corners.map { $0.y }.min()!.rounded(.down)) - 1)
        let maxY = min(height - 1, Int(corners.map { $0.y }.max()!.rounded(.up)) + 1)
        guard minX <= maxX, minY <= maxY else { return (alpha, nil) }
        var residual = 0.0
        var n = 0
        for y in minY...maxY {
            for x in minX...maxX {
                let q = toCapture.apply(SIMD2<Float>(Float(x), Float(y)))
                let tx = q.x - Float(template.originX), ty = q.y - Float(template.originY)
                if tx < 0 || ty < 0 || tx >= Float(template.width - 1) || ty >= Float(template.height - 1) { continue }
                let a = Bilinear.sample(template.mask, width: template.width, height: template.height, x: tx, y: ty)
                if a < 8 { continue }
                let index = y * width + x
                if person[index] != 0 { continue }
                alpha[index] = UInt8(min(255, a))
                if a > 200 {
                    let ix = Int(tx), iy = Int(ty)
                    let t = (iy * template.width + ix) * 3
                    let f = index * 4
                    residual += Double(max(abs(Int(rgba[f]) - Int(template.rgb[t])), max(abs(Int(rgba[f + 1]) - Int(template.rgb[t + 1])), abs(Int(rgba[f + 2]) - Int(template.rgb[t + 2])))))
                    n += 1
                }
            }
        }
        return (alpha, n > 0 ? residual / Double(n) : nil)
    }
}
