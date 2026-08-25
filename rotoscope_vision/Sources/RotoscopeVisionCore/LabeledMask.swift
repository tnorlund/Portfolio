import Foundation

/// A mask where every pixel carries who it belongs to: 0 background, 1 the
/// person, 2 + k the k-th attached object. Per-object soft alphas are kept
/// alongside so downstream can composite objects separately.
public struct LabeledMask {
    public var width: Int
    public var height: Int
    public var labels: [UInt8]
    /// Object id → soft alpha (0–255) plane.
    public var alphas: [Int: [UInt8]]
    /// Object id → label value used in `labels`.
    public var objectLabels: [Int: UInt8]

    public init(width: Int, height: Int) {
        self.width = width
        self.height = height
        self.labels = [UInt8](repeating: 0, count: width * height)
        self.alphas = [:]
        self.objectLabels = [:]
    }

    /// Union of all objects as a binary prop mask.
    public var props: [UInt8] {
        labels.map { $0 >= 2 ? 1 : 0 }
    }

    /// Max of the person mask and every object alpha.
    public func mask(person: [UInt8]) -> [UInt8] {
        var out = person
        for (_, alpha) in alphas {
            for i in 0..<out.count where alpha[i] > out[i] { out[i] = alpha[i] }
        }
        return out
    }

    public static let palette: [(UInt8, UInt8, UInt8)] = [
        (255, 255, 255), (66, 133, 244), (251, 140, 0), (30, 200, 120), (229, 57, 53), (171, 71, 188),
        (255, 214, 0), (0, 172, 193),
    ]

    /// RGBA tile: the source dimmed, labels tinted.
    public func tile(over rgba: [UInt8]) -> [UInt8] {
        var out = rgba
        for i in 0..<labels.count {
            let o = i * 4
            let l = Int(labels[i])
            if l == 0 {
                out[o] = UInt8(Int(out[o]) * 35 / 100)
                out[o + 1] = UInt8(Int(out[o + 1]) * 35 / 100)
                out[o + 2] = UInt8(Int(out[o + 2]) * 35 / 100)
            } else {
                let c = LabeledMask.palette[min(LabeledMask.palette.count - 1, l)]
                out[o] = UInt8((Int(out[o]) + Int(c.0) * 2) / 3)
                out[o + 1] = UInt8((Int(out[o + 1]) + Int(c.1) * 2) / 3)
                out[o + 2] = UInt8((Int(out[o + 2]) + Int(c.2) * 2) / 3)
            }
            out[o + 3] = 255
        }
        return out
    }
}

public enum TrackTile {
    /// Colour per track label for overlays.
    public static func color(for label: TrackLabel) -> (UInt8, UInt8, UInt8) {
        switch label {
        case .unknown: return (200, 200, 200)
        case .background: return (120, 120, 120)
        case .subject: return (66, 133, 244)
        case .other: return (150, 90, 200)
        case .attached: return (251, 140, 0)
        case .shadowLike: return (255, 214, 0)
        case .moving: return (229, 57, 53)
        case .foreign: return (0, 200, 120)
        }
    }

    /// Source dimmed outside the person, tracks drawn as 3×3 dots by label,
    /// optional object outlines (object id → colour by palette).
    public static func render(
        rgba: [UInt8], width: Int, height: Int, person: [UInt8], tracks: [Track], objectOf: (Track) -> Int?
    ) -> [UInt8] {
        var out = rgba
        for i in 0..<(width * height) where person[i] == 0 {
            let o = i * 4
            out[o] = UInt8(Int(out[o]) * 45 / 100)
            out[o + 1] = UInt8(Int(out[o + 1]) * 45 / 100)
            out[o + 2] = UInt8(Int(out[o + 2]) * 45 / 100)
        }
        func dot(_ x: Int, _ y: Int, _ c: (UInt8, UInt8, UInt8), radius: Int) {
            for dy in -radius...radius {
                let py = y + dy
                if py < 0 || py >= height { continue }
                for dx in -radius...radius {
                    let px = x + dx
                    if px < 0 || px >= width { continue }
                    let o = (py * width + px) * 4
                    out[o] = c.0; out[o + 1] = c.1; out[o + 2] = c.2; out[o + 3] = 255
                }
            }
        }
        for track in tracks where track.status == .live {
            let x = Int(track.current.x.rounded()), y = Int(track.current.y.rounded())
            var c = color(for: track.label)
            if let object = objectOf(track) {
                c = LabeledMask.palette[min(LabeledMask.palette.count - 1, 2 + object % (LabeledMask.palette.count - 2))]
            }
            dot(x, y, c, radius: 1)
        }
        return out
    }
}
