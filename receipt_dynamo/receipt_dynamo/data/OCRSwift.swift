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

struct ExtractedData: Codable {
    let type: String
    let value: String
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
    let extractedData: ExtractedData?

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

struct OCRResult: Codable {
    let lines: [Line]
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

func normalizedRect(from boundingBox: CGRect) -> NormalizedRect {
    NormalizedRect(x: boundingBox.origin.x, y: boundingBox.origin.y, width: boundingBox.size.width, height: boundingBox.size.height)
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
func extractStructuredData(from text: String) -> ExtractedData? {
    let types: NSTextCheckingResult.CheckingType = [.date, .phoneNumber, .address, .link]
    guard let detector = try? NSDataDetector(types: types.rawValue) else { return nil }

    if let match = detector.firstMatch(in: text, options: [], range: NSRange(location: 0, length: text.utf16.count)) {
        if let date = match.date {
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
            return ExtractedData(type: "date", value: formatter.string(from: date))
        } else if let phoneNumber = match.phoneNumber {
            return ExtractedData(type: "phone_number", value: phoneNumber)
        } else if let addressComponents = match.addressComponents {
            let formattedAddress = addressComponents.map { "\($0.key): \($0.value)" }.joined(separator: ", ")
            return ExtractedData(type: "address", value: formattedAddress)
        } else if let url = match.url {
            return ExtractedData(type: "url", value: url.absoluteString)
        }
    }
    return nil
}



func performOCRSync(from imageURL: URL) throws -> [Line] {
    log("Loading image from \(imageURL.path)")
    guard let nsImage = NSImage(contentsOf: imageURL),
          let cgImg = cgImage(from: nsImage) else {
        throw NSError(domain: "OCRScript", code: -1, userInfo: [
            NSLocalizedDescriptionKey: "Unable to load image at \(imageURL.path)"
        ])
    }

    log("Performing OCR on image")
    let requestHandler = VNImageRequestHandler(cgImage: cgImg, options: [:])
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true

    try requestHandler.perform([request])

    guard let observations = request.results, !observations.isEmpty else {
        log("⚠️ No text recognized.")
        return []
    }

    log("Processing recognized text")
    var lines: [Line] = []

    for obs in observations {
        guard let candidate = obs.topCandidates(1).first else { continue }

        var words: [Word] = []
        let lineText = candidate.string
        var currentIndex = lineText.startIndex

        for wordText in lineText.split(separator: " ") {
            guard let range = lineText.range(of: wordText, range: currentIndex..<lineText.endIndex),
                  let wordBox = try? candidate.boundingBox(for: range) else {
                log("⚠️ Skipping word due to missing bounding box: \(wordText)")
                continue
            }

            let letters: [Letter] = wordText.enumerated().compactMap { (i, char) in
                let charStart = lineText.index(range.lowerBound, offsetBy: i)
                let charEnd = lineText.index(after: charStart)
                guard let letterBox = try? candidate.boundingBox(for: charStart..<charEnd) else {
                    return nil
                }
                return Letter(
                    text: String(char),
                    boundingBox: normalizedRect(from: letterBox.boundingBox),
                    topLeft: codablePoint(from: letterBox.topLeft),
                    topRight: codablePoint(from: letterBox.topRight),
                    bottomLeft: codablePoint(from: letterBox.bottomLeft),
                    bottomRight: codablePoint(from: letterBox.bottomRight),
                    angleDegrees: 0,
                    angleRadians: 0,
                    confidence: candidate.confidence
                )
            }

            words.append(Word(
                text: String(wordText),
                boundingBox: normalizedRect(from: wordBox.boundingBox),
                topLeft: codablePoint(from: wordBox.topLeft),
                topRight: codablePoint(from: wordBox.topRight),
                bottomLeft: codablePoint(from: wordBox.bottomLeft),
                bottomRight: codablePoint(from: wordBox.bottomRight),
                angleDegrees: 0,
                angleRadians: 0,
                confidence: candidate.confidence,
                letters: letters,
                extractedData: extractStructuredData(from: String(wordText))
            ))

            currentIndex = range.upperBound
        }

        lines.append(Line(
            text: candidate.string,
            boundingBox: normalizedRect(from: obs.boundingBox),
            topLeft: codablePoint(from: obs.topLeft),
            topRight: codablePoint(from: obs.topRight),
            bottomLeft: codablePoint(from: obs.bottomLeft),
            bottomRight: codablePoint(from: obs.bottomRight),
            angleDegrees: 0,
            angleRadians: 0,
            confidence: candidate.confidence,
            words: words
        ))
    }

    log("✅ OCR processing complete. Found \(lines.count) lines.")
    return lines
}


// MARK: - Main Execution

let args = CommandLine.arguments
guard args.count >= 3 else {
    print("Usage: \(args[0]) <outputDirectory> <imagePath1> <imagePath2> ...")
    exit(EXIT_FAILURE)
}

let outputDirectory = args[1]
let imagePaths = Array(args.dropFirst(2))

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
    print("❌ Path \(outputDirectory) exists but isn't a directory.")
    exit(EXIT_FAILURE)
}

for imagePath in imagePaths {
    let imageURL = URL(fileURLWithPath: imagePath)
    let baseName = imageURL.deletingPathExtension().lastPathComponent
    let outJsonURL = URL(fileURLWithPath: "\(outputDirectory)/\(baseName).json")

    do {
        let lines = try performOCRSync(from: imageURL)
        let ocrResult = OCRResult(lines: lines)
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.outputFormatting = .prettyPrinted
        let jsonData = try encoder.encode(ocrResult)
        try jsonData.write(to: outJsonURL)

        print("✅ OCR completed for \(imagePath).")
        print("   → Wrote JSON to: \(outJsonURL.path)")
    } catch {
        print("❌ OCR failed for \(imagePath): \(error.localizedDescription)")
    }
}
