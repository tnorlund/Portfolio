import AVFoundation
import Foundation
import RotoscopeVisionCore

/// rotoscope-vision — run the best-feature rotoscope over a movie, with Apple
/// Vision supplying the focus tiers (person/subject mask + eye landmarks) and
/// lifting the subject off the background.
///
///   rotoscope-vision IMG_0974.mov [--out-dir DIR] [--width W] [--budget N]
///       [--blur R] [--face-quota F] [--spacing FACE,BODY,BG]
///       [--subject person|foreground|people|none] [--keep-background]
///       [--matte white|black] [--max-frames N] [--stills DIR] [--stills-every N]
///
/// Writes `<name>-rotoscope.mov` (ProRes 4444 with alpha) and
/// `<name>-rotoscope-preview.mp4` (H.264 over the matte), both with the source
/// audio passed through.

struct Arguments {
    var input: URL
    var outDir: URL
    var width: Int?
    var options = EngineOptions()
    var subject: SubjectMode = .person
    var keepBackground = false
    var matte: (red: UInt8, green: UInt8, blue: UInt8) = (255, 255, 255)
    var maxFrames: Int?
    var stillsDir: URL?
    var stillsEvery = 30
    var skipMov = false
    var skipAudio = false

    static func parse(_ argv: [String]) throws -> Arguments {
        var args = Array(argv.dropFirst())
        guard let first = args.first, !first.hasPrefix("--") else {
            throw UsageError("usage: rotoscope-vision <input.mov> [options]")
        }
        args.removeFirst()
        let input = URL(fileURLWithPath: first)
        var parsed = Arguments(input: input, outDir: input.deletingLastPathComponent())
        func value(_ flag: String) throws -> String {
            guard !args.isEmpty else { throw UsageError("\(flag) needs a value") }
            return args.removeFirst()
        }
        while !args.isEmpty {
            let flag = args.removeFirst()
            switch flag {
            case "--out-dir": parsed.outDir = URL(fileURLWithPath: try value(flag), isDirectory: true)
            case "--width": parsed.width = Int(try value(flag))
            case "--budget": parsed.options.markerBudget = Int(try value(flag)) ?? parsed.options.markerBudget
            case "--blur": parsed.options.blurRadius = Int(try value(flag)) ?? parsed.options.blurRadius
            case "--face-quota":
                let face = Double(try value(flag)) ?? 0.3
                parsed.options.quotas = TierValues(face: face, body: 1 - face, background: 0)
            case "--spacing":
                let parts = try value(flag).split(separator: ",").compactMap { Double($0) }
                if parts.count == 3 {
                    parsed.options.spacing = TierValues(face: parts[0], body: parts[1], background: parts[2])
                }
            case "--subject":
                let raw = try value(flag)
                guard let mode = SubjectMode(rawValue: raw) else { throw UsageError("unknown --subject \(raw)") }
                parsed.subject = mode
            case "--keep-background": parsed.keepBackground = true
            case "--matte":
                parsed.matte = try value(flag) == "black" ? (0, 0, 0) : (255, 255, 255)
            case "--max-frames": parsed.maxFrames = Int(try value(flag))
            case "--stills": parsed.stillsDir = URL(fileURLWithPath: try value(flag), isDirectory: true)
            case "--stills-every": parsed.stillsEvery = max(1, Int(try value(flag)) ?? 30)
            case "--no-mov": parsed.skipMov = true
            case "--no-audio": parsed.skipAudio = true
            default: throw UsageError("unknown option \(flag)")
            }
        }
        if parsed.keepBackground {
            // Keep a small background share so the flood stays contained.
            parsed.options.quotas = TierValues(
                face: parsed.options.quotas.face, body: parsed.options.quotas.body, background: 0.1)
        }
        return parsed
    }
}

struct UsageError: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }
}

func log(_ message: String) {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
}

do {
    let args = try Arguments.parse(CommandLine.arguments)
    let started = Date()
    let reader = try await FrameReader(url: args.input, targetWidth: args.width)
    let width = reader.width
    let height = reader.height
    log("input \(args.input.lastPathComponent): \(width)×\(height) @ \(reader.frameRate) fps, \(String(format: "%.2f", reader.duration)) s (~\(reader.estimatedFrames) frames)")
    log("subject \(args.subject.rawValue), budget \(args.options.markerBudget), blur \(args.options.blurRadius), background \(args.keepBackground ? "kept" : "removed")")

    let stem = args.input.deletingPathExtension().lastPathComponent
    try FileManager.default.createDirectory(at: args.outDir, withIntermediateDirectories: true)
    if let stillsDir = args.stillsDir {
        try FileManager.default.createDirectory(at: stillsDir, withIntermediateDirectories: true)
    }
    let hasAudio = reader.audioTrack != nil && !args.skipAudio
    let audioFormat = hasAudio ? reader.audioFormat : nil
    var writers: [FrameWriter] = []
    if !args.skipMov {
        writers.append(
            try FrameWriter(
                url: args.outDir.appendingPathComponent("\(stem)-rotoscope.mov"), width: width, height: height,
                codec: .proRes4444, audioFormat: audioFormat))
    }
    writers.append(
        try FrameWriter(
            url: args.outDir.appendingPathComponent("\(stem)-rotoscope-preview.mp4"), width: width,
            height: height, codec: .h264(matte: args.matte), audioFormat: audioFormat))

    let analyzer = FocusAnalyzer(width: width, height: height, mode: args.subject)
    // Audio is interleaved with video as we go: AVAssetWriter holds a track
    // input once it runs ahead of a sibling track, so feeding audio only at the
    // end would deadlock the video input.
    let audio = hasAudio ? try reader.audioTrack.map { try AudioReader(asset: reader.asset, track: $0) } : nil
    var pendingAudio = audio?.next()
    var audioSamples = 0
    let audioLead = CMTime(seconds: 0.5, preferredTimescale: 600)
    func pumpAudio(upTo time: CMTime?, wait: Bool) throws {
        while let sample = pendingAudio {
            if let time, CMSampleBufferGetPresentationTimeStamp(sample) > time + audioLead { return }
            var accepted = true
            for writer in writers where try !writer.appendAudio(sample, wait: wait) { accepted = false }
            if !accepted { return }
            audioSamples += 1
            pendingAudio = audio?.next()
        }
        // Audio exhausted: tell the writers so they stop waiting for it.
        for writer in writers { writer.finishAudio() }
    }
    if pendingAudio == nil { for writer in writers { writer.finishAudio() } }
    try reader.start()
    var frameIndex = 0
    var sessionStarted = false
    var facesSeen = 0
    var emptyMasks = 0
    while let (buffer, time) = try reader.next() {
        if let max = args.maxFrames, frameIndex >= max { break }
        if !sessionStarted {
            for writer in writers { try writer.start(at: time) }
            sessionStarted = true
        }
        let rgba = rgbaBytes(from: buffer)
        let focus = try analyzer.analyze(buffer)
        if focus.face != nil { facesSeen += 1 }
        if focus.subjectPixels == 0 { emptyMasks += 1 }
        // A frame with no subject keeps the whole frame as body so it is not blank.
        let tiers = focus.subjectPixels == 0 && !args.keepBackground
            ? [UInt8](repeating: FocusTier.body.rawValue, count: width * height)
            : focus.tiers
        let alpha = focus.subjectPixels == 0 ? nil : focus.mask
        // No detected face this frame: spend the face share on the body instead.
        var options = args.options
        if focus.face == nil {
            options.quotas = TierValues(
                face: 0, body: options.quotas.face + options.quotas.body, background: options.quotas.background)
        }
        let frame = Engine.process(
            rgba: rgba, width: width, height: height, tiers: tiers, alpha: alpha,
            removeBackground: !args.keepBackground, options: options)
        do {
            for writer in writers { try writer.append(rgba: frame.rgba, at: time) }
        } catch {
            let pendingTime = pendingAudio.map { CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp($0)) }
            log("video append failed at frame \(frameIndex) t=\(CMTimeGetSeconds(time)) audioSamples=\(audioSamples) pendingAudio=\(String(describing: pendingTime))")
            throw error
        }
        try pumpAudio(upTo: time, wait: false)
        if let stillsDir = args.stillsDir, frameIndex % args.stillsEvery == 0 {
            let tag = String(format: "%04d", frameIndex)
            try writePNG(rgba: composite(rgba: frame.rgba, over: args.matte), width: width, height: height,
                         to: stillsDir.appendingPathComponent("\(tag)-rotoscope.png"))
            try writePNG(
                rgba: debugOverlay(rgba: rgba, width: width, height: height, focus: focus, markers: frame.markers, tiers: tiers),
                width: width, height: height, to: stillsDir.appendingPathComponent("\(tag)-focus.png"))
        }
        frameIndex += 1
        if frameIndex % 10 == 0 || frameIndex == 1 {
            let elapsed = Date().timeIntervalSince(started)
            log(String(format: "frame %d/%d  markers %d (face %d, body %d)  regions %d  %.1fs", frameIndex,
                       reader.estimatedFrames, frame.markers.indices.count, frame.markers.faceCount,
                       frame.markers.bodyCount, frame.regionCount, elapsed))
        }
    }
    for writer in writers { writer.finishVideo() }
    if sessionStarted {
        try pumpAudio(upTo: nil, wait: true)
        log("audio: \(audioSamples) sample buffers passed through")
    }
    for writer in writers { try await writer.finish() }
    let elapsed = Date().timeIntervalSince(started)
    log(String(format: "done: %d frames in %.1fs (%.2fs/frame); face found in %d frames; empty subject in %d",
               frameIndex, elapsed, elapsed / Double(max(1, frameIndex)), facesSeen, emptyMasks))
    for writer in writers { print(writer.url.path) }
} catch {
    log("error: \(error)")
    exit(1)
}
