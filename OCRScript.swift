#!/usr/bin/env swift

import Foundation
import Vision
import AppKit

// MARK: - Data Models

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
    let angle: Float       // approximate rotation in degrees
    let confidence: Float  // reusing line-level confidence as Vision doesn't split letter confidences
}

/// Each recognized word
struct Word: Codable {
    let text: String
    let boundingBox: NormalizedRect
    let angle: Float
    let confidence: Float
    let letters: [Letter]  // detailed breakdown of letters
}

/// Each recognized line
struct Line: Codable {
    let text: String
    let boundingBox: NormalizedRect
    let angle: Float
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

/// Converts a Vision rectangle (with corners) into an approximate rotation angle in degrees.
///
/// We compute the angle based on the top edge's slope.
/// - Parameter rectObs: The VNRectangleObservation
/// - Returns: Angle in degrees, where 0 means horizontally aligned text.
private func rotationAngle(rectObs: VNRectangleObservation) -> Float {
    // The corners are in normalized image coordinates (origin at bottom-left).
    let topLeft = rectObs.topLeft
    let topRight = rectObs.topRight
    
    let dx = topRight.x - topLeft.x
    let dy = topRight.y - topLeft.y
    let angleRadians = atan2(dy, dx)
    let angleDegrees = angleRadians * 180.0 / .pi
    
    return Float(angleDegrees)
}

/// Converts the bounding box of a `VNRectangleObservation` into a `NormalizedRect`.
/// Vision's boundingBox is an axis-aligned CGRect in normalized coordinates (0..1).
private func normalizedRect(from rectObs: VNRectangleObservation) -> NormalizedRect {
    let bb = rectObs.boundingBox
    return NormalizedRect(x: bb.origin.x,
                          y: bb.origin.y,
                          width: bb.size.width,
                          height: bb.size.height)
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
            let lineAngle = rotationAngle(rectObs: obs)
            let lineBoundingBox = normalizedRect(from: obs)
            
            // Break the line text into words (naive approach: split by whitespace).
            let wordsArray = lineText.components(separatedBy: .whitespacesAndNewlines)
            
            var runningIndex = lineText.startIndex
            var wordModels: [Word] = []
            
            for w in wordsArray where !w.isEmpty {
                // Find the substring range in the entire line text
                guard let range = lineText.range(of: w,
                                                 range: runningIndex..<lineText.endIndex)
                else { continue }
                
                // Attempt to get bounding box for this word range
                let wordRectObs = rectangleObservation(in: candidate, range: range)
                
                // Create the Word object
                let wordAngle = wordRectObs.map(rotationAngle) ?? 0
                let wordBox = wordRectObs.map(normalizedRect) ?? NormalizedRect(x: 0, y: 0, width: 0, height: 0)
                
                // Break word into letters
                var letterModels: [Letter] = []
                for i in w.indices {
                    // Each letter is the single-character substring in that word
                    let globalLetterStart = lineText.index(range.lowerBound,
                                                           offsetBy: i.utf16Offset(in: w))
                    let globalLetterRange = globalLetterStart..<lineText.index(globalLetterStart, offsetBy: 1)
                    
                    let letterRectObs = rectangleObservation(in: candidate, range: globalLetterRange)
                    let letterAngle   = letterRectObs.map(rotationAngle) ?? 0
                    let letterBox     = letterRectObs.map(normalizedRect) ?? NormalizedRect(x: 0, y: 0, width: 0, height: 0)
                    
                    let letterModel = Letter(
                        text: String(w[i]),
                        boundingBox: letterBox,
                        angle: letterAngle,
                        confidence: lineConfidence // Vision only provides line-level confidence
                    )
                    letterModels.append(letterModel)
                }
                
                let wordModel = Word(
                    text: w,
                    boundingBox: wordBox,
                    angle: wordAngle,
                    confidence: lineConfidence,
                    letters: letterModels
                )
                wordModels.append(wordModel)
                
                // Advance our runningIndex beyond this word
                runningIndex = range.upperBound
            }
            
            let lineModel = Line(
                text: lineText,
                boundingBox: lineBoundingBox,
                angle: lineAngle,
                confidence: lineConfidence,
                words: wordModels
            )
            
            return lineModel
        }
        
        completion(.success(lines))
    }
    
    // Configure the request
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    
    // Execute
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