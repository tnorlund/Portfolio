import Foundation

/// Assembles keyframe tiles into one PNG so a whole run can be judged from
/// a single image: one row per keyframe, one column per view.
public enum ContactSheet {
    /// Box-downscales RGBA by an integer factor.
    public static func downscale(_ rgba: [UInt8], width: Int, height: Int, factor: Int) -> (rgba: [UInt8], width: Int, height: Int) {
        let w = width / factor, h = height / factor
        var out = [UInt8](repeating: 255, count: w * h * 4)
        let n = factor * factor
        for y in 0..<h {
            for x in 0..<w {
                var r = 0, g = 0, b = 0
                for dy in 0..<factor {
                    var offset = ((y * factor + dy) * width + x * factor) * 4
                    for _ in 0..<factor {
                        r += Int(rgba[offset]); g += Int(rgba[offset + 1]); b += Int(rgba[offset + 2])
                        offset += 4
                    }
                }
                let o = (y * w + x) * 4
                out[o] = UInt8(r / n); out[o + 1] = UInt8(g / n); out[o + 2] = UInt8(b / n)
            }
        }
        return (out, w, h)
    }

    /// Lays out `rows` × `columns` equally sized RGBA tiles with a 4 px gutter.
    public static func assemble(rows: [[[UInt8]]], tileWidth: Int, tileHeight: Int) -> (rgba: [UInt8], width: Int, height: Int) {
        let gutter = 4
        let columns = rows.map { $0.count }.max() ?? 0
        let width = columns * tileWidth + (columns + 1) * gutter
        let height = rows.count * tileHeight + (rows.count + 1) * gutter
        var out = [UInt8](repeating: 40, count: width * height * 4)
        for i in stride(from: 3, to: out.count, by: 4) { out[i] = 255 }
        for (r, row) in rows.enumerated() {
            for (c, tile) in row.enumerated() {
                let ox = gutter + c * (tileWidth + gutter)
                let oy = gutter + r * (tileHeight + gutter)
                for y in 0..<tileHeight {
                    let src = y * tileWidth * 4
                    let dst = ((oy + y) * width + ox) * 4
                    out.replaceSubrange(dst..<(dst + tileWidth * 4), with: tile[src..<(src + tileWidth * 4)])
                }
            }
        }
        return (out, width, height)
    }

    /// Grayscale field (0–255) as an RGBA tile.
    public static func grayTile(_ field: [UInt8]) -> [UInt8] {
        var out = [UInt8](repeating: 255, count: field.count * 4)
        for i in 0..<field.count {
            out[i * 4] = field[i]; out[i * 4 + 1] = field[i]; out[i * 4 + 2] = field[i]
        }
        return out
    }

    /// Probability field as a heat tile (black → orange → white).
    public static func heatTile(_ field: [Float]) -> [UInt8] {
        var out = [UInt8](repeating: 255, count: field.count * 4)
        for i in 0..<field.count {
            let v = max(0, min(1, Double(field[i])))
            let r = UInt8(min(255, v * 2 * 255))
            let g = UInt8(min(255, max(0, (v - 0.35) * 1.6) * 255))
            let b = UInt8(min(255, max(0, (v - 0.75) * 4) * 255))
            out[i * 4] = r; out[i * 4 + 1] = g; out[i * 4 + 2] = b
        }
        return out
    }
}
