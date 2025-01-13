#!/usr/bin/env swift

import Foundation
import Vision
import AppKit

// MARK: - Data Models

/// A simple wrapper to store a point that can be encoded/decoded as JSON.
struct Point: Codable {
    let x: CGFloat
    let y: CGFloat
}

/// A rectangle in normalized Vision coordinates (0–1)
struct NormalizedRect: Codable {
    let x: CGFloat
    let y: CGFloat
    let width: CGFloat
    let height: CGFloat
}

/// Each recognized letter
struct Letter: Codable {
    let text: String       // the letter itself
    let boundingBox: NormalizedRect

    // The four corners in normalized coordinates
    let topLeft: Point
    let topRight: Point
    let bottomLeft: Point
    let bottomRight: Point

    // Both degrees and radians
    let angleDegrees: Float
    let angleRadians: Float

    // Reusing line-level confidence (Vision doesn't split letter confidences)
    let confidence: Float
}

/// Each recognized word
struct Word: Codable {
    let text: String
    let boundingBox: NormalizedRect

    // The four corners in normalized coordinates
    let topLeft: Point
    let topRight: Point
    let bottomLeft: Point
    let bottomRight: Point

    let angleDegrees: Float
    let angleRadians: Float

    let confidence: Float
    let letters: [Letter]  // detailed breakdown of letters
}

/// Each recognized line
struct Line: Codable {
    let text: String
    let boundingBox: NormalizedRect

    // The four corners in normalized coordinates
    let topLeft: Point
    let topRight: Point
    let bottomLeft: Point
    let bottomRight: Point

    // Both degrees and radians
    let angleDegrees: Float
    let angleRadians: Float

    let confidence: Float
    let words: [Word]
}

/// The top-level OCR result
struct OCRResult: Codable {
    let lines: [Line]
}

// MARK: - Helpers

/// Converts an `NSImage` to `CGImage`.
func cgImage(from nsImage: NSImage) -> CGImage? {
    guard let imageData = nsImage.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: imageData) else {
        return nil
    }
    return bitmap.cgImage
}

/// Computes both degrees and radians based on the top edge’s slope (from topLeft to topRight).
/// - Parameter rectObs: The VNRectangleObservation
/// - Returns: (degrees, radians)
private func angles(for rectObs: VNRectangleObservation) -> (degrees: Float, radians: Float) {
    let topLeft = rectObs.topLeft
    let topRight = rectObs.topRight

    let dx = topRight.x - topLeft.x
    let dy = topRight.y - topLeft.y
    let angleRadians = atan2(dy, dx)
    let angleDegrees = angleRadians * 180.0 / .pi

    return (Float(angleDegrees), Float(angleRadians))
}

/// Converts the bounding box of a `VNRectangleObservation` into a `NormalizedRect`.
/// Vision's boundingBox is an axis-aligned CGRect in normalized coordinates (0..1).
private func normalizedRect(from rectObs: VNRectangleObservation) -> NormalizedRect {
    let bb = rectObs.boundingBox
    return NormalizedRect(
        x: bb.origin.x,
        y: bb.origin.y,
        width: bb.size.width,
        height: bb.size.height
    )
}

/// Convenience: converts a CGPoint into a `Point` struct.
private func codablePoint(from cgPoint: CGPoint) -> Point {
    return Point(x: cgPoint.x, y: cgPoint.y)
}

/// Convenience to convert a partial-range bounding box from a recognized text candidate
/// into a `VNRectangleObservation` so we can get corners, bounding box, etc.
private func rectangleObservation(
    in recognizedText: VNRecognizedText,
    range: Range<String.Index>
) -> VNRectangleObservation? {
    // boundingBox(for:) can throw, so we wrap with try?
    return try? recognizedText.boundingBox(for: range)
}

// MARK: - OCR Engine

/// Recognize text from an image file at `imageURL`.
/// Returns an array of `Line` objects (with word and letter data).
func performOCR(
    from imageURL: URL,
    completion: @escaping (Result<[Line], Error>) -> Void
) {
    guard let nsImage = NSImage(contentsOf: imageURL),
          let cgImg = cgImage(from: nsImage) else {
        let err = NSError(domain: "OCRScript",
                          code: -1,
                          userInfo: [NSLocalizedDescriptionKey: "Unable to load image at \(imageURL.path)"])
        completion(.failure(err))
        return
    }

    // Create request handler
    let requestHandler = VNImageRequestHandler(cgImage: cgImg, options: [:])

    // Create the text recognition request
    let request = VNRecognizeTextRequest { req, err in
        if let err = err {
            completion(.failure(err))
            return
        }

        guard let observations = req.results as? [VNRecognizedTextObservation],
              !observations.isEmpty else {
            let noTextErr = NSError(domain: "OCRScript",
                                    code: -2,
                                    userInfo: [NSLocalizedDescriptionKey: "No text recognized."])
            completion(.failure(noTextErr))
            return
        }

        // Process each line
        let lines: [Line] = observations.compactMap { obs in
            // The best text candidate for this entire line
            guard let candidate = obs.topCandidates(1).first else { return nil }

            // Store line-level data
            let lineText = candidate.string
            let lineConfidence = candidate.confidence

            // Extract degrees/radians for this line
            let (lineDegrees, lineRadians) = angles(for: obs)
            let lineBoundingBox = normalizedRect(from: obs)

            // Corners in normalized coordinates
            let lineTopLeft     = codablePoint(from: obs.topLeft)
            let lineTopRight    = codablePoint(from: obs.topRight)
            let lineBottomLeft  = codablePoint(from: obs.bottomLeft)
            let lineBottomRight = codablePoint(from: obs.bottomRight)

            // Break the line text into words (naive approach: split by whitespace).
            let wordsArray = lineText.components(separatedBy: .whitespacesAndNewlines)

            var runningIndex = lineText.startIndex
            var wordModels: [Word] = []

            for w in wordsArray where !w.isEmpty {
                // Find the substring range in the entire line text
                guard let range = lineText.range(of: w, range: runningIndex..<lineText.endIndex)
                else { continue }

                // Attempt to get bounding box for this word range
                let wordRectObs = rectangleObservation(in: candidate, range: range)

                // Compute angles for this word
                let (wordDegrees, wordRadians) = wordRectObs.map(angles(for:)) ?? (0, 0)
                let wordBox = wordRectObs.map(normalizedRect) ??
                    NormalizedRect(x: 0, y: 0, width: 0, height: 0)

                // Corners for this word
                let wordTopLeft     = wordRectObs.map { codablePoint(from: $0.topLeft) } ?? Point(x: 0, y: 0)
                let wordTopRight    = wordRectObs.map { codablePoint(from: $0.topRight) } ?? Point(x: 0, y: 0)
                let wordBottomLeft  = wordRectObs.map { codablePoint(from: $0.bottomLeft) } ?? Point(x: 0, y: 0)
                let wordBottomRight = wordRectObs.map { codablePoint(from: $0.bottomRight) } ?? Point(x: 0, y: 0)

                // Break word into letters
                var letterModels: [Letter] = []
                for i in w.indices {
                    // Each letter is the single-character substring in that word
                    let globalLetterStart = lineText.index(range.lowerBound,
                                                           offsetBy: i.utf16Offset(in: w))
                    let globalLetterRange = globalLetterStart..<lineText.index(globalLetterStart, offsetBy: 1)

                    let letterRectObs = rectangleObservation(in: candidate, range: globalLetterRange)
                    let (letterDegrees, letterRadians) = letterRectObs.map(angles(for:)) ?? (0, 0)
                    let letterBox = letterRectObs.map(normalizedRect) ??
                        NormalizedRect(x: 0, y: 0, width: 0, height: 0)

                    // Corners for this letter
                    let letterTopLeft     = letterRectObs.map { codablePoint(from: $0.topLeft) } ?? Point(x: 0, y: 0)
                    let letterTopRight    = letterRectObs.map { codablePoint(from: $0.topRight) } ?? Point(x: 0, y: 0)
                    let letterBottomLeft  = letterRectObs.map { codablePoint(from: $0.bottomLeft) } ?? Point(x: 0, y: 0)
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

                // Advance our runningIndex beyond this word
                runningIndex = range.upperBound
            }

            // Build the final line model
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

            return lineModel
        }

        // If we get here, we have an array of lines
        completion(.success(lines))
    }

    // Configure the request
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true

    // Execute the request
    do {
        try requestHandler.perform([request])
    } catch {
        completion(.failure(error))
    }
}

// MARK: - Main Script

guard CommandLine.arguments.count == 3 else {
    print("Usage: \(CommandLine.arguments[0]) <inputImagePath> <outputJsonPath>")
    exit(EXIT_FAILURE)
}

let inputImagePath = CommandLine.arguments[1]
let outputJsonPath = CommandLine.arguments[2]

let imageURL = URL(fileURLWithPath: inputImagePath)
let outputURL = URL(fileURLWithPath: outputJsonPath)

performOCR(from: imageURL) { result in
    switch result {
    case .success(let lines):
        do {
            let ocrResult = OCRResult(lines: lines)
            let encoder = JSONEncoder()
            encoder.keyEncodingStrategy = .convertToSnakeCase
            encoder.outputFormatting = .prettyPrinted
            let jsonData = try encoder.encode(ocrResult)
            
            // Write to file
            try jsonData.write(to: outputURL)
            print("✅ OCR completed. Results saved to: \(outputURL.path)")
            print("Recognized \(lines.count) lines of text.")
        } catch {
            print("❌ Failed to encode or write JSON: \(error.localizedDescription)")
        }
        
    case .failure(let error):
        print("❌ OCR failed: \(error.localizedDescription)")
    }
    exit(EXIT_SUCCESS)
}

// Keep the script alive until asynchronous OCR is done
RunLoop.main.run()