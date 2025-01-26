#!/usr/bin/env swift

import Foundation
import Vision
import AppKit

// MARK: - Data Models

struct Point: Codable {
    let x: CGFloat
    let y: CGFloat
}

struct NormalizedRect: Codable {
    let x: CGFloat
    let y: CGFloat
    let width: CGFloat
    let height: CGFloat
}

struct Letter: Codable {
    let text: String
    let boundingBox: NormalizedRect
    let topLeft: Point
    let topRight: Point
    let bottomLeft: Point
    let bottomRight: Point
    let angleDegrees: Float
    let angleRadians: Float
    let confidence: Float
}

struct Word: Codable {
    let text: String
    let boundingBox: NormalizedRect
    let topLeft: Point
    let topRight: Point
    let bottomLeft: Point
    let bottomRight: Point
    let angleDegrees: Float
    let angleRadians: Float
    let confidence: Float
    let letters: [Letter]
}

struct Line: Codable {
    let text: String
    let boundingBox: NormalizedRect
    let topLeft: Point
    let topRight: Point
    let bottomLeft: Point
    let bottomRight: Point
    let angleDegrees: Float
    let angleRadians: Float
    let confidence: Float
    let words: [Word]
}

struct OCRResult: Codable {
    let lines: [Line]
}

// MARK: - Helpers

/// Converts an `NSImage` to a `CGImage`.
func cgImage(from nsImage: NSImage) -> CGImage? {
    guard 
        let imageData = nsImage.tiffRepresentation,
        let bitmap = NSBitmapImageRep(data: imageData) 
    else {
        return nil
    }
    return bitmap.cgImage
}

/// Computes the angle in degrees and radians for the top edge of a `VNRectangleObservation`.
func angles(for rectObs: VNRectangleObservation) -> (degrees: Float, radians: Float) {
    let dx = rectObs.topRight.x - rectObs.topLeft.x
    let dy = rectObs.topRight.y - rectObs.topLeft.y
    let rad = atan2(dy, dx)
    let deg = rad * 180.0 / .pi
    return (Float(deg), Float(rad))
}

/// Converts the bounding box of a `VNRectangleObservation` into a `NormalizedRect`.
func normalizedRect(from rectObs: VNRectangleObservation) -> NormalizedRect {
    let bb = rectObs.boundingBox
    return NormalizedRect(x: bb.origin.x,
                          y: bb.origin.y,
                          width: bb.size.width,
                          height: bb.size.height)
}

/// Converts a `CGPoint` to our `Point` struct.
func codablePoint(from cgPoint: CGPoint) -> Point {
    Point(x: cgPoint.x, y: cgPoint.y)
}

/// Returns a `VNRectangleObservation` for a substring range within a recognized text candidate.
func rectangleObservation(
    in recognizedText: VNRecognizedText,
    range: Range<String.Index>
) -> VNRectangleObservation? {
    // boundingBox(for:) can throw, so we wrap with try?
    return try? recognizedText.boundingBox(for: range)
}

// MARK: - Synchronous OCR Engine

/// Performs OCR in a **synchronous** fashion (no completion handler).
/// Throws an error if Vision fails or can't load the image.
func performOCRSync(from imageURL: URL) throws -> [Line] {
    // 1) Load the image
    guard 
        let nsImage = NSImage(contentsOf: imageURL),
        let cgImg = cgImage(from: nsImage) 
    else {
        throw NSError(domain: "OCRScript", code: -1, userInfo: [
            NSLocalizedDescriptionKey: "Unable to load image at \(imageURL.path)"
        ])
    }

    // 2) Create Vision request + request handler
    let requestHandler = VNImageRequestHandler(cgImage: cgImg, options: [:])
    let request = VNRecognizeTextRequest()

    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true

    // 3) Perform request synchronously (blocking)
    try requestHandler.perform([request])

    // 4) Process results or handle "no text recognized"
    guard 
        let observations = request.results,
        !observations.isEmpty  
    else {
        throw NSError(domain: "OCRScript", code: -2, userInfo: [
            NSLocalizedDescriptionKey: "No text recognized."
        ])
    }

    // 5) Convert results into `[Line]` array
    var lines = [Line]()
    for obs in observations {
        guard let candidate = obs.topCandidates(1).first else { continue }

        let lineText = candidate.string
        let lineConfidence = candidate.confidence
        let (lineDegrees, lineRadians) = angles(for: obs)
        let lineBoundingBox = normalizedRect(from: obs)

        let lineTopLeft     = codablePoint(from: obs.topLeft)
        let lineTopRight    = codablePoint(from: obs.topRight)
        let lineBottomLeft  = codablePoint(from: obs.bottomLeft)
        let lineBottomRight = codablePoint(from: obs.bottomRight)

        // Split line by whitespace to get words
        let wordsArray = lineText.components(separatedBy: .whitespacesAndNewlines)
        var runningIndex = lineText.startIndex
        var wordModels = [Word]()

        for w in wordsArray where !w.isEmpty {
            guard 
                let range = lineText.range(of: w, range: runningIndex..<lineText.endIndex)
            else {
                continue
            }
            let wordRectObs = rectangleObservation(in: candidate, range: range)
            let (wordDegrees, wordRadians) = wordRectObs.map(angles(for:)) ?? (0, 0)
            let wordBox = wordRectObs.map(normalizedRect) 
                ?? NormalizedRect(x: 0, y: 0, width: 0, height: 0)

            let wordTopLeft     = wordRectObs.map { codablePoint(from: $0.topLeft) }     ?? Point(x: 0, y: 0)
            let wordTopRight    = wordRectObs.map { codablePoint(from: $0.topRight) }    ?? Point(x: 0, y: 0)
            let wordBottomLeft  = wordRectObs.map { codablePoint(from: $0.bottomLeft) }  ?? Point(x: 0, y: 0)
            let wordBottomRight = wordRectObs.map { codablePoint(from: $0.bottomRight) } ?? Point(x: 0, y: 0)

            // Build letters
            var letterModels = [Letter]()
            for i in w.indices {
                let globalLetterStart = lineText.index(range.lowerBound, 
                                                       offsetBy: i.utf16Offset(in: w))
                let globalLetterRange = globalLetterStart..<lineText.index(globalLetterStart, offsetBy: 1)

                let letterRectObs = rectangleObservation(in: candidate, range: globalLetterRange)
                let (letterDegrees, letterRadians) = letterRectObs.map(angles(for:)) ?? (0, 0)
                let letterBox = letterRectObs.map(normalizedRect) 
                    ?? NormalizedRect(x: 0, y: 0, width: 0, height: 0)

                let letterTopLeft     = letterRectObs.map { codablePoint(from: $0.topLeft) }     ?? Point(x: 0, y: 0)
                let letterTopRight    = letterRectObs.map { codablePoint(from: $0.topRight) }    ?? Point(x: 0, y: 0)
                let letterBottomLeft  = letterRectObs.map { codablePoint(from: $0.bottomLeft) }  ?? Point(x: 0, y: 0)
                let letterBottomRight = letterRectObs.map { codablePoint(from: $0.bottomRight) } ?? Point(x: 0, y: 0)

                let letterModel = Letter(
                    text: String(w[i]),
                    boundingBox: letterBox,
                    topLeft: letterTopLeft,
                    topRight: letterTopRight,
                    bottomLeft: letterBottomLeft,
                    bottomRight: letterBottomRight,
                    angleDegrees: letterDegrees,
                    angleRadians: letterRadians,
                    confidence: lineConfidence // Vision only provides line-level confidence
                )
                letterModels.append(letterModel)
            }

            let wordModel = Word(
                text: w,
                boundingBox: wordBox,
                topLeft: wordTopLeft,
                topRight: wordTopRight,
                bottomLeft: wordBottomLeft,
                bottomRight: wordBottomRight,
                angleDegrees: wordDegrees,
                angleRadians: wordRadians,
                confidence: lineConfidence,
                letters: letterModels
            )
            wordModels.append(wordModel)

            // Advance the running index past this word
            runningIndex = range.upperBound
        }

        let lineModel = Line(
            text: lineText,
            boundingBox: lineBoundingBox,
            topLeft: lineTopLeft,
            topRight: lineTopRight,
            bottomLeft: lineBottomLeft,
            bottomRight: lineBottomRight,
            angleDegrees: lineDegrees,
            angleRadians: lineRadians,
            confidence: lineConfidence,
            words: wordModels
        )
        lines.append(lineModel)
    }

    return lines
}

// MARK: - Main

// Example usage: 
//   swift OCRSwift.swift <outputDirectory> <img1.png> <img2.png> ... <imgN.png>
// No run loop needed—this script will exit on its own once done.

let args = CommandLine.arguments
guard args.count >= 3 else {
    print("Usage: \(args[0]) <outputDirectory> <imagePath1> <imagePath2> ... <imagePathN>")
    exit(EXIT_FAILURE)
}

let outputDirectory = args[1]
let imagePaths = Array(args.dropFirst(2))

// Make sure outputDirectory exists (create if needed)
let fileManager = FileManager.default
var isDir: ObjCBool = false
if !fileManager.fileExists(atPath: outputDirectory, isDirectory: &isDir) {
    do {
        try fileManager.createDirectory(atPath: outputDirectory, withIntermediateDirectories: true)
    } catch {
        print("❌ Failed to create output directory: \(error.localizedDescription)")
        exit(EXIT_FAILURE)
    }
} else if !isDir.boolValue {
    print("❌ The path \(outputDirectory) exists but is not a directory.")
    exit(EXIT_FAILURE)
}

// Process each image synchronously
for imagePath in imagePaths {
    let imageURL = URL(fileURLWithPath: imagePath)
    // e.g. "myreceipt.png" => "myreceipt.json"
    let baseName = imageURL.deletingPathExtension().lastPathComponent
    let outJsonURL = URL(fileURLWithPath: "\(outputDirectory)/\(baseName).json")

    do {
        let lines = try performOCRSync(from: imageURL)

        // Build the final JSON object
        let ocrResult = OCRResult(lines: lines)
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.outputFormatting = .prettyPrinted
        let jsonData = try encoder.encode(ocrResult)

        // Write JSON to file
        try jsonData.write(to: outJsonURL)

        print("✅ OCR completed for \(imagePath).")
        print("   → Wrote JSON to: \(outJsonURL.path)")
    } catch {
        print("❌ OCR failed for \(imagePath): \(error.localizedDescription)")
    }
}

// The script naturally ends here, so `subprocess.run(...)` will return in Python.