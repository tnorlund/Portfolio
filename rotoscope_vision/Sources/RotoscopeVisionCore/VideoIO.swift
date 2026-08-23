import AVFoundation
import CoreImage
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// Decodes a movie's video track into upright BGRA frames at the processing
/// size, applying the track's preferred transform (iPhone rotation metadata).
public final class FrameReader {
    public let width: Int
    public let height: Int
    public let frameRate: Float
    public let duration: Double
    public let estimatedFrames: Int
    public let audioTrack: AVAssetTrack?
    public let audioFormat: CMFormatDescription?
    public let asset: AVURLAsset

    private let reader: AVAssetReader
    private let output: AVAssetReaderTrackOutput
    private let transform: CGAffineTransform
    private let context = CIContext(options: [.cacheIntermediates: false])
    private var pool: CVPixelBufferPool?

    public init(url: URL, targetWidth: Int?) async throws {
        let asset = AVURLAsset(url: url)
        self.asset = asset
        guard let track = try await asset.loadTracks(withMediaType: .video).first else {
            throw RotoscopeVisionError.video("no video track in \(url.path)")
        }
        let naturalSize = try await track.load(.naturalSize)
        let preferred = try await track.load(.preferredTransform)
        frameRate = try await track.load(.nominalFrameRate)
        duration = CMTimeGetSeconds(try await asset.load(.duration))
        audioTrack = try await asset.loadTracks(withMediaType: .audio).first
        audioFormat = try await audioTrack?.load(.formatDescriptions).first

        // Upright size after the preferred transform.
        let upright = CGRect(origin: .zero, size: naturalSize).applying(preferred)
        var outWidth = Int(abs(upright.width).rounded())
        var outHeight = Int(abs(upright.height).rounded())
        if let targetWidth, targetWidth > 0, targetWidth < outWidth {
            let scale = Double(targetWidth) / Double(outWidth)
            outWidth = targetWidth
            outHeight = Int((Double(outHeight) * scale).rounded())
        }
        // Even dimensions keep H.264 happy.
        outWidth -= outWidth % 2
        outHeight -= outHeight % 2
        width = outWidth
        height = outHeight
        let scale = CGAffineTransform(
            scaleX: CGFloat(outWidth) / abs(upright.width), y: CGFloat(outHeight) / abs(upright.height))
        // Move the transformed image back to the origin, then scale to the output size.
        transform = preferred
            .concatenating(CGAffineTransform(translationX: -upright.minX, y: -upright.minY))
            .concatenating(scale)
        estimatedFrames = Int((duration * Double(frameRate)).rounded())

        reader = try AVAssetReader(asset: asset)
        output = AVAssetReaderTrackOutput(
            track: track,
            outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
        output.alwaysCopiesSampleData = false
        reader.add(output)
    }

    public func start() throws {
        guard reader.startReading() else {
            throw RotoscopeVisionError.video("could not start reading: \(reader.error?.localizedDescription ?? "unknown")")
        }
    }

    /// Next upright frame as a BGRA pixel buffer of `width`×`height`, or nil at the end.
    public func next() throws -> (buffer: CVPixelBuffer, time: CMTime)? {
        guard let sample = output.copyNextSampleBuffer() else {
            if reader.status == .failed {
                throw RotoscopeVisionError.video(reader.error?.localizedDescription ?? "read failed")
            }
            return nil
        }
        guard let source = CMSampleBufferGetImageBuffer(sample) else { return try next() }
        let time = CMSampleBufferGetPresentationTimeStamp(sample)
        let target = try makeBuffer()
        let image = CIImage(cvPixelBuffer: source).transformed(by: transform)
        context.render(
            image, to: target, bounds: CGRect(x: 0, y: 0, width: width, height: height),
            colorSpace: CGColorSpace(name: CGColorSpace.sRGB))
        return (target, time)
    }

    private func makeBuffer() throws -> CVPixelBuffer {
        if pool == nil {
            let attributes: [CFString: Any] = [
                kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_32BGRA,
                kCVPixelBufferWidthKey: width,
                kCVPixelBufferHeightKey: height,
                kCVPixelBufferIOSurfacePropertiesKey: [:],
            ]
            var created: CVPixelBufferPool?
            CVPixelBufferPoolCreate(kCFAllocatorDefault, nil, attributes as CFDictionary, &created)
            pool = created
        }
        var buffer: CVPixelBuffer?
        guard let pool, CVPixelBufferPoolCreatePixelBuffer(kCFAllocatorDefault, pool, &buffer) == kCVReturnSuccess,
            let buffer
        else { throw RotoscopeVisionError.pixelBuffer("could not allocate frame buffer") }
        return buffer
    }
}

/// BGRA pixel buffer → tightly packed RGBA bytes.
public func rgbaBytes(from buffer: CVPixelBuffer) -> [UInt8] {
    CVPixelBufferLockBaseAddress(buffer, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
    let width = CVPixelBufferGetWidth(buffer)
    let height = CVPixelBufferGetHeight(buffer)
    let stride = CVPixelBufferGetBytesPerRow(buffer)
    var output = [UInt8](repeating: 0, count: width * height * 4)
    guard let base = CVPixelBufferGetBaseAddress(buffer) else { return output }
    output.withUnsafeMutableBufferPointer { dst in
        for y in 0..<height {
            let row = base.advanced(by: y * stride).assumingMemoryBound(to: UInt8.self)
            var src = 0
            var out = y * width * 4
            for _ in 0..<width {
                dst[out] = row[src + 2]
                dst[out + 1] = row[src + 1]
                dst[out + 2] = row[src]
                dst[out + 3] = row[src + 3]
                src += 4
                out += 4
            }
        }
    }
    return output
}

public enum OutputCodec {
    /// ProRes 4444 in a .mov keeps the alpha channel.
    case proRes4444
    /// H.264 .mp4 preview; transparent pixels are composited over `matte`.
    case h264(matte: (red: UInt8, green: UInt8, blue: UInt8))
}

/// Encodes straight-alpha RGBA frames, optionally passing the source audio through.
public final class FrameWriter {
    public let url: URL
    public let width: Int
    public let height: Int
    private let codec: OutputCodec
    private let writer: AVAssetWriter
    private let input: AVAssetWriterInput
    private let adaptor: AVAssetWriterInputPixelBufferAdaptor
    private var audioInput: AVAssetWriterInput?
    private var audioFinished = false

    public init(
        url: URL, width: Int, height: Int, codec: OutputCodec, audioFormat: CMFormatDescription?
    ) throws {
        self.url = url
        self.width = width
        self.height = height
        self.codec = codec
        try? FileManager.default.removeItem(at: url)
        let fileType: AVFileType
        var settings: [String: Any] = [
            AVVideoWidthKey: width,
            AVVideoHeightKey: height,
        ]
        switch codec {
        case .proRes4444:
            fileType = .mov
            settings[AVVideoCodecKey] = AVVideoCodecType.proRes4444
        case .h264:
            fileType = .mp4
            settings[AVVideoCodecKey] = AVVideoCodecType.h264
            settings[AVVideoCompressionPropertiesKey] = [AVVideoAverageBitRateKey: 12_000_000]
        }
        writer = try AVAssetWriter(outputURL: url, fileType: fileType)
        input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
        input.expectsMediaDataInRealTime = false
        adaptor = AVAssetWriterInputPixelBufferAdaptor(
            assetWriterInput: input,
            sourcePixelBufferAttributes: [
                kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
                kCVPixelBufferWidthKey as String: width,
                kCVPixelBufferHeightKey as String: height,
            ])
        writer.add(input)
        if let audioFormat {
            // Passthrough (nil settings) needs the source format as a hint so
            // the .mp4 muxer can write the AAC config; without it the track is
            // silently dropped.
            let audio = AVAssetWriterInput(mediaType: .audio, outputSettings: nil, sourceFormatHint: audioFormat)
            audio.expectsMediaDataInRealTime = false
            if writer.canAdd(audio) {
                writer.add(audio)
                audioInput = audio
            }
        }
    }

    public func start(at time: CMTime) throws {
        guard writer.startWriting() else {
            throw RotoscopeVisionError.video("could not start writing \(url.lastPathComponent): \(writer.error?.localizedDescription ?? "unknown")")
        }
        writer.startSession(atSourceTime: time)
    }

    public func append(rgba: [UInt8], at time: CMTime) throws {
        var waited = 0
        while !input.isReadyForMoreMediaData {
            usleep(2000)
            waited += 1
            if waited > 15000 {
                throw RotoscopeVisionError.video("video input for \(url.lastPathComponent) never became ready")
            }
        }
        guard let pool = adaptor.pixelBufferPool else {
            throw RotoscopeVisionError.pixelBuffer("writer pool unavailable")
        }
        var buffer: CVPixelBuffer?
        guard CVPixelBufferPoolCreatePixelBuffer(kCFAllocatorDefault, pool, &buffer) == kCVReturnSuccess,
            let buffer
        else { throw RotoscopeVisionError.pixelBuffer("could not allocate output buffer") }
        CVPixelBufferLockBaseAddress(buffer, [])
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        if let base = CVPixelBufferGetBaseAddress(buffer) {
            rgba.withUnsafeBufferPointer { src in
                for y in 0..<height {
                    let row = base.advanced(by: y * stride).assumingMemoryBound(to: UInt8.self)
                    var offset = y * width * 4
                    var out = 0
                    for _ in 0..<width {
                        let r = Int(src[offset])
                        let g = Int(src[offset + 1])
                        let b = Int(src[offset + 2])
                        let a = Int(src[offset + 3])
                        switch codec {
                        case .proRes4444:
                            // Premultiplied BGRA.
                            row[out] = UInt8((b * a + 127) / 255)
                            row[out + 1] = UInt8((g * a + 127) / 255)
                            row[out + 2] = UInt8((r * a + 127) / 255)
                            row[out + 3] = UInt8(a)
                        case .h264(let matte):
                            row[out] = UInt8((b * a + Int(matte.blue) * (255 - a) + 127) / 255)
                            row[out + 1] = UInt8((g * a + Int(matte.green) * (255 - a) + 127) / 255)
                            row[out + 2] = UInt8((r * a + Int(matte.red) * (255 - a) + 127) / 255)
                            row[out + 3] = 255
                        }
                        offset += 4
                        out += 4
                    }
                }
            }
        }
        CVPixelBufferUnlockBaseAddress(buffer, [])
        guard adaptor.append(buffer, withPresentationTime: time) else {
            throw RotoscopeVisionError.video("append failed for \(url.lastPathComponent): \(writer.error?.localizedDescription ?? "unknown")")
        }
    }

    /// Call once all video frames are appended, before any audio. The writer
    /// interleaves tracks, so an unfinished video input that is behind the
    /// audio timeline would block the audio input forever.
    public func finishVideo() {
        input.markAsFinished()
    }

    /// Copies one compressed audio sample straight through. Returns false (and
    /// appends nothing) when the writer is not ready, so callers can interleave
    /// audio with video instead of blocking; `wait` forces a bounded wait.
    @discardableResult
    public func appendAudio(_ sample: CMSampleBuffer, wait: Bool = false) throws -> Bool {
        guard let audioInput else { return true }
        var waited = 0
        while !audioInput.isReadyForMoreMediaData {
            if !wait { return false }
            usleep(2000)
            waited += 1
            if waited > 5000 { throw RotoscopeVisionError.video("audio input never became ready") }
        }
        return audioInput.append(sample)
    }

    /// Call as soon as the source audio is exhausted. The audio track is often
    /// a little shorter than the video, and until the writer knows no more
    /// audio is coming it holds the video input waiting for audio to catch up.
    public func finishAudio() {
        guard !audioFinished else { return }
        audioFinished = true
        audioInput?.markAsFinished()
    }

    public func finish() async throws {
        finishAudio()
        await writer.finishWriting()
        if writer.status == .failed {
            throw RotoscopeVisionError.video("finishing \(url.lastPathComponent) failed: \(writer.error?.localizedDescription ?? "unknown")")
        }
    }
}

/// Reads the compressed audio samples of a track for passthrough.
public final class AudioReader {
    private let reader: AVAssetReader
    private let output: AVAssetReaderTrackOutput

    public init(asset: AVAsset, track: AVAssetTrack) throws {
        reader = try AVAssetReader(asset: asset)
        output = AVAssetReaderTrackOutput(track: track, outputSettings: nil)
        reader.add(output)
        guard reader.startReading() else {
            throw RotoscopeVisionError.video("could not read audio: \(reader.error?.localizedDescription ?? "unknown")")
        }
    }

    /// Next audio sample with a valid, numeric presentation time. Trailing
    /// packets from the decoder can carry invalid timestamps; feeding those to
    /// AVAssetWriter stalls the interleave forever, so they are skipped.
    public func next() -> CMSampleBuffer? {
        while let sample = output.copyNextSampleBuffer() {
            let time = CMSampleBufferGetPresentationTimeStamp(sample)
            if time.isValid && time.isNumeric && CMSampleBufferIsValid(sample)
                && CMSampleBufferGetNumSamples(sample) > 0
            {
                return sample
            }
        }
        return nil
    }
}

/// Straight-alpha RGBA composited over a solid matte (opaque result).
public func composite(rgba: [UInt8], over matte: (red: UInt8, green: UInt8, blue: UInt8)) -> [UInt8] {
    var out = rgba
    var offset = 0
    while offset < rgba.count {
        let a = Int(rgba[offset + 3])
        out[offset] = UInt8((Int(rgba[offset]) * a + Int(matte.red) * (255 - a) + 127) / 255)
        out[offset + 1] = UInt8((Int(rgba[offset + 1]) * a + Int(matte.green) * (255 - a) + 127) / 255)
        out[offset + 2] = UInt8((Int(rgba[offset + 2]) * a + Int(matte.blue) * (255 - a) + 127) / 255)
        out[offset + 3] = 255
        offset += 4
    }
    return out
}

/// Writes straight-alpha RGBA bytes as a PNG.
public func writePNG(rgba: [UInt8], width: Int, height: Int, to url: URL) throws {
    let data = Data(rgba)
    guard let provider = CGDataProvider(data: data as CFData),
        let image = CGImage(
            width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: width * 4,
            space: CGColorSpace(name: CGColorSpace.sRGB)!,
            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue),
            provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent),
        let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)
    else { throw RotoscopeVisionError.pixelBuffer("could not encode PNG") }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else {
        throw RotoscopeVisionError.pixelBuffer("could not write \(url.path)")
    }
}

/// Source frame with the background dimmed, the face ellipse outlined, and
/// every marker drawn by tier (face blue, body orange, background gray).
public func debugOverlay(
    rgba: [UInt8], width: Int, height: Int, focus: FrameFocus, markers: Markers, tiers: [UInt8]
) -> [UInt8] {
    var out = rgba
    for index in 0..<(width * height) where focus.mask[index] <= 127 {
        let offset = index * 4
        out[offset] = UInt8(Int(out[offset]) * 35 / 100)
        out[offset + 1] = UInt8(Int(out[offset + 1]) * 35 / 100)
        out[offset + 2] = UInt8(Int(out[offset + 2]) * 35 / 100)
    }
    func dot(_ x: Int, _ y: Int, _ color: (UInt8, UInt8, UInt8), radius: Int) {
        for dy in -radius...radius {
            for dx in -radius...radius {
                let px = x + dx
                let py = y + dy
                if px < 0 || py < 0 || px >= width || py >= height { continue }
                let offset = (py * width + px) * 4
                out[offset] = color.0
                out[offset + 1] = color.1
                out[offset + 2] = color.2
                out[offset + 3] = 255
            }
        }
    }
    if let face = focus.face {
        var angle = 0.0
        while angle < 2 * Double.pi {
            let x = Int((face.centerX + face.radiusX * cos(angle)) * Double(width))
            let y = Int((face.centerY + face.radiusY * sin(angle)) * Double(height))
            dot(x, y, (255, 255, 255), radius: 0)
            angle += 0.01
        }
    }
    for index in markers.indices {
        let y = index / width
        let x = index - y * width
        let color: (UInt8, UInt8, UInt8)
        switch tiers[index] {
        case FocusTier.face.rawValue: color = (30, 136, 229)
        case FocusTier.body.rawValue: color = (251, 140, 0)
        default: color = (160, 160, 160)
        }
        dot(x, y, color, radius: 1)
    }
    return out
}
