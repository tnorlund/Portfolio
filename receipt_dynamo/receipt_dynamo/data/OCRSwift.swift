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

    enum CodingKeys: String, CodingKey {
        case x, y, width, height
    }
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

    enum CodingKeys: String, CodingKey {
        case text, boundingBox = "bounding_box", topLeft = "top_left", topRight = "top_right", bottomLeft = "bottom_left", bottomRight = "bottom_right", angleDegrees = "angle_degrees", angleRadians = "angle_radians", confidence
    }
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
    let extractedData: String?

    enum CodingKeys: String, CodingKey {
        case text, boundingBox = "bounding_box", topLeft = "top_left", topRight = "top_right", bottomLeft = "bottom_left", bottomRight = "bottom_right", angleDegrees = "angle_degrees", angleRadians = "angle_radians", confidence, letters, extractedData = "extracted_data"
    }
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

    enum CodingKeys: String, CodingKey {
        case text, boundingBox = "bounding_box", topLeft = "top_left", topRight = "top_right", bottomLeft = "bottom_left", bottomRight = "bottom_right", angleDegrees = "angle_degrees", angleRadians = "angle_radians", confidence, words
    }
}

// MARK: - Helpers

func log(_ message: String) {
    print("[LOG] \(message)")
}

func cgImage(from nsImage: NSImage) -> CGImage? {
    guard let imageData = nsImage.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: imageData) else {
        return nil
    }
    return bitmap.cgImage
}

func normalizedRect(from rectObs: VNRectangleObservation) -> NormalizedRect {
    let bb = rectObs.boundingBox
    return NormalizedRect(x: bb.origin.x, y: bb.origin.y, width: bb.size.width, height: bb.size.height)
}

func codablePoint(from cgPoint: CGPoint) -> Point {
    return Point(x: cgPoint.x, y: cgPoint.y)
}

func angles(for rectObs: VNRectangleObservation) -> (degrees: Float, radians: Float) {
    let dx = rectObs.topRight.x - rectObs.topLeft.x
    let dy = rectObs.topRight.y - rectObs.topLeft.y
    let rad = atan2(dy, dx)
    let deg = rad * 180.0 / .pi
    return (Float(deg), Float(rad))
}

func extractStructuredData(from text: String) -> String? {
    let types: NSTextCheckingResult.CheckingType = [.date, .phoneNumber, .address, .link]
    let detector = try? NSDataDetector(types: types.rawValue)
    var extractedValue: String? = nil
    
    detector?.matches(in: text, options: [], range: NSRange(location: 0, length: text.utf16.count)).forEach { match in
        if let date = match.date {
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
            extractedValue = formatter.string(from: date)
        } else if let phoneNumber = match.phoneNumber {
            extractedValue = phoneNumber
        } else if let addressComponents = match.addressComponents {
            extractedValue = addressComponents.map { "\($0.key): \($0.value)" }.joined(separator: ", ")
        } else if let url = match.url {
            extractedValue = url.absoluteString
        }
    }
    return extractedValue
}

func performOCRSync(from imageURL: URL) throws -> [Line] {
    log("Loading image from \(imageURL.path)")
    guard let nsImage = NSImage(contentsOf: imageURL),
          let cgImg = cgImage(from: nsImage) else {
        log("❌ Error: Unable to load image at \(imageURL.path)")
        throw NSError(domain: "OCRScript", code: -1, userInfo: [
            NSLocalizedDescriptionKey: "Unable to load image at \(imageURL.path)"
        ])
    }

    log("Performing OCR on image")
    let requestHandler = VNImageRequestHandler(cgImage: cgImg, options: [:])
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true

    do {
        try requestHandler.perform([request])
    } catch {
        log("❌ OCR failed: \(error.localizedDescription)")
        throw error
    }

    guard let observations = request.results, !observations.isEmpty else {
        log("⚠️ Warning: No text recognized.")
        return []
    }

    log("Processing recognized text")
    let lines: [Line] = observations.compactMap { (obs: VNRecognizedTextObservation) -> Line? in
        guard let candidate = obs.topCandidates(1).first else {
            log("⚠️ Skipping observation with no text candidates.")
            return nil
        }

        log("✅ Recognized text: \(candidate.string)")
        return Line(
            text: candidate.string,
            boundingBox: normalizedRect(from: obs),
            topLeft: codablePoint(from: obs.topLeft),
            topRight: codablePoint(from: obs.topRight),
            bottomLeft: codablePoint(from: obs.bottomLeft),
            bottomRight: codablePoint(from: obs.bottomRight),
            angleDegrees: angles(for: obs).degrees,
            angleRadians: angles(for: obs).radians,
            confidence: candidate.confidence,
            words: candidate.string.split(separator: " ").map { word in
                let wordText = String(word)
                return Word(
                    text: wordText,
                    boundingBox: normalizedRect(from: obs),
                    topLeft: codablePoint(from: obs.topLeft),
                    topRight: codablePoint(from: obs.topRight),
                    bottomLeft: codablePoint(from: obs.bottomLeft),
                    bottomRight: codablePoint(from: obs.bottomRight),
                    angleDegrees: angles(for: obs).degrees,
                    angleRadians: angles(for: obs).radians,
                    confidence: candidate.confidence,
                    letters: wordText.map { char in
                        Letter(
                            text: String(char),
                            boundingBox: normalizedRect(from: obs),
                            topLeft: codablePoint(from: obs.topLeft),
                            topRight: codablePoint(from: obs.topRight),
                            bottomLeft: codablePoint(from: obs.bottomLeft),
                            bottomRight: codablePoint(from: obs.bottomRight),
                            angleDegrees: angles(for: obs).degrees,
                            angleRadians: angles(for: obs).radians,
                            confidence: candidate.confidence
                        )
                    },
                    extractedData: extractStructuredData(from: wordText)
                )
            }
        )
    }
    log("✅ OCR processing complete. Found \(lines.count) lines.")
    return lines
}
