import Foundation
import RotoscopeVisionCore
import simd

/// Per-frame scratch written by pass 1 of the two-pass pipeline and read back by
/// passes 2 (propagate) and 3 (render + metrics), so later passes never re-run
/// Vision — only the movie frames are re-read. One `NNNN.bin` (fixed-order
/// UInt8/Int16 planes) plus one `NNNN.json` (dims, flow signs, pose, face, and
/// the pass-1 FrameMetrics) per frame. Everything lives under a git-ignored dir.
enum Scratch {
    /// Side data that does not fit a raw plane.
    struct Side: Codable {
        var frame: Int
        var width: Int
        var height: Int
        var signX: Float
        var signY: Float
        var flowAvailable: Bool
        var subjectPixels: Int
        var faceCenterX: Double?
        var faceCenterY: Double?
        var faceRadiusX: Double?
        var faceRadiusY: Double?
        var poseFloorY: Double?
        var barX0: Double?
        var barY0: Double?
        var barX1: Double?
        var barY1: Double?
        var metrics: FrameMetrics
    }

    /// The planes stored for a frame; each `[UInt8]` is width*height (warpedPlate
    /// is width*height*4), the flow is fixed-point Int16 (value*16).
    struct Frame {
        var mask: [UInt8]
        var person: [UInt8]
        var props: [UInt8]
        var difference: [UInt8]
        var others: [UInt8]
        var warpedPlate: [UInt8]?
        var dx: [Int16]
        var dy: [Int16]
        var side: Side
    }

    static func url(_ dir: URL, _ frame: Int, _ ext: String) -> URL {
        dir.appendingPathComponent(String(format: "%04d.%@", frame, ext))
    }

    static func write(_ dir: URL, frame: Int, data: Frame) throws {
        var blob = Data()
        blob.append(contentsOf: data.mask)
        blob.append(contentsOf: data.person)
        blob.append(contentsOf: data.props)
        blob.append(contentsOf: data.difference)
        blob.append(contentsOf: data.others)
        let warped = data.warpedPlate ?? []
        withUnsafeBytes(of: UInt32(warped.count).littleEndian) { blob.append(contentsOf: $0) }
        blob.append(contentsOf: warped)
        data.dx.withUnsafeBytes { blob.append(contentsOf: $0) }
        data.dy.withUnsafeBytes { blob.append(contentsOf: $0) }
        try blob.write(to: url(dir, frame, "bin"))
        let enc = JSONEncoder()
        try enc.encode(data.side).write(to: url(dir, frame, "json"))
    }

    static func readSide(_ dir: URL, frame: Int) throws -> Side {
        try JSONDecoder().decode(Side.self, from: Data(contentsOf: url(dir, frame, "json")))
    }

    static func read(_ dir: URL, frame: Int) throws -> Frame {
        let side = try readSide(dir, frame: frame)
        let count = side.width * side.height
        let blob = try Data(contentsOf: url(dir, frame, "bin"))
        return try blob.withUnsafeBytes { (raw: UnsafeRawBufferPointer) -> Frame in
            var offset = 0
            func plane(_ n: Int) throws -> [UInt8] {
                guard offset + n <= raw.count else { throw UsageError("scratch \(frame) truncated") }
                let a = [UInt8](raw[offset..<offset + n]); offset += n; return a
            }
            let mask = try plane(count)
            let person = try plane(count)
            let props = try plane(count)
            let difference = try plane(count)
            let others = try plane(count)
            let warpedCount = raw.loadUnaligned(fromByteOffset: offset, as: UInt32.self).littleEndian
            offset += 4
            let warped: [UInt8]? = warpedCount > 0 ? try plane(Int(warpedCount)) : nil
            func shorts(_ n: Int) throws -> [Int16] {
                let bytes = n * 2
                guard offset + bytes <= raw.count else { throw UsageError("scratch \(frame) truncated") }
                var out = [Int16](repeating: 0, count: n)
                for i in 0..<n { out[i] = raw.loadUnaligned(fromByteOffset: offset + i * 2, as: Int16.self) }
                offset += bytes
                return out
            }
            let dx = try shorts(count)
            let dy = try shorts(count)
            return Frame(mask: mask, person: person, props: props, difference: difference, others: others,
                         warpedPlate: warped, dx: dx, dy: dy, side: side)
        }
    }

    /// Directory byte size (for logging).
    static func size(_ dir: URL) -> Int64 {
        guard let e = FileManager.default.enumerator(at: dir, includingPropertiesForKeys: [.fileSizeKey]) else { return 0 }
        var total: Int64 = 0
        for case let u as URL in e {
            total += Int64((try? u.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0)
        }
        return total
    }
}
