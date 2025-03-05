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
    guard let nsImage = NSImage(contentsOf: imageURL),
          let cgImg = cgImage(from: nsImage) else {
        throw NSError(domain: "OCRScript", code: -1, userInfo: [
            NSLocalizedDescriptionKey: "Unable to load image at \(imageURL.path)"
        ])
    }

    let requestHandler = VNImageRequestHandler(cgImage: cgImg, options: [:])
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true

    try requestHandler.perform([request])

    guard let observations = request.results, !observations.isEmpty else {
        throw NSError(domain: "OCRScript", code: -2, userInfo: [
            NSLocalizedDescriptionKey: "No text recognized."
        ])
    }

    return observations.compactMap { obs in
        guard let candidate = obs.topCandidates(1).first else { return nil }
        
        let words = candidate.string.split(separator: " ").map { word -> Word in
            let wordText = String(word)
            let wordRect = normalizedRect(from: obs)
            return Word(
                text: wordText,
                boundingBox: wordRect,
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
                        boundingBox: wordRect,
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
            words: words
        )
    }
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