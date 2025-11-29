#!/usr/bin/env swift

import Foundation
import AppKit
import Vision

// MARK: - Logging
func log(_ message: String) {
    print(message)
}

// MARK: - Data Models

struct CodablePoint: Codable {
    let x: CGFloat
    let y: CGFloat
}

struct NormalizedRect: Codable {
    let x: CGFloat
    let y: CGFloat
    let width: CGFloat
    let height: CGFloat
}

struct ExtractedData: Codable {
    let type: String  // e.g., "address", "phone", "date", "url"
    let value: String
}

struct Letter: Codable {
    let text: String
    let boundingBox: NormalizedRect
    let topLeft: CodablePoint
    let topRight: CodablePoint
    let bottomLeft: CodablePoint
    let bottomRight: CodablePoint
    let angleDegrees: CGFloat
    let angleRadians: CGFloat
    let confidence: VNConfidence
}

struct Word: Codable {
    let text: String
    let boundingBox: NormalizedRect
    let topLeft: CodablePoint
    let topRight: CodablePoint
    let bottomLeft: CodablePoint
    let bottomRight: CodablePoint
    let angleDegrees: CGFloat
    let angleRadians: CGFloat
    let confidence: VNConfidence
    let letters: [Letter]
    var extractedData: ExtractedData? // New property for structured data
}

struct Line: Codable {
    let text: String
    let boundingBox: NormalizedRect
    let topLeft: CodablePoint
    let topRight: CodablePoint
    let bottomLeft: CodablePoint
    let bottomRight: CodablePoint
    let angleDegrees: CGFloat
    let angleRadians: CGFloat
    let confidence: VNConfidence
    var words: [Word]
}

/// This struct holds the OCR and extraction result for a single image.
struct ImageResult: Codable {
    let imagePath: String
    let lines: [Line]

    private enum CodingKeys: String, CodingKey {
        case lines
    }

    // Use a custom initializer for decoding that ignores imagePath.
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.lines = try container.decode([Line].self, forKey: .lines)
        self.imagePath = "" // Set to an empty string or a default value.
    }

    // Use the default initializer for creating an instance.
    init(imagePath: String, lines: [Line]) {
        self.imagePath = imagePath
        self.lines = lines
    }

    // Custom encoding: only encode the "lines" property.
    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(lines, forKey: .lines)
    }
}

// Used for mapping a word to its range in the aggregated text.
struct WordMapping {
    var lineIndex: Int
    var wordIndex: Int
    var range: NSRange
}

// MARK: - Helper Functions

func codablePoint(from point: CGPoint) -> CodablePoint {
    return CodablePoint(x: point.x, y: point.y)
}

func normalizedRect(from rect: CGRect) -> NormalizedRect {
    return NormalizedRect(x: rect.origin.x,
                          y: rect.origin.y,
                          width: rect.size.width,
                          height: rect.size.height)
}

/// Calculate bounding box from multiple character boxes
func boundingBox(from characterBoxes: [CGRect]) -> CGRect {
    guard !characterBoxes.isEmpty else {
        return CGRect.zero
    }
    let minX = characterBoxes.map { $0.minX }.min() ?? 0
    let minY = characterBoxes.map { $0.minY }.min() ?? 0
    let maxX = characterBoxes.map { $0.maxX }.max() ?? 0
    let maxY = characterBoxes.map { $0.maxY }.max() ?? 0
    return CGRect(x: minX, y: minY, width: maxX - minX, height: maxY - minY)
}

/// Calculate corner points from a bounding box
func cornerPoints(from rect: CGRect) -> (topLeft: CGPoint, topRight: CGPoint, bottomLeft: CGPoint, bottomRight: CGPoint) {
    return (
        topLeft: CGPoint(x: rect.minX, y: rect.maxY),
        topRight: CGPoint(x: rect.maxX, y: rect.maxY),
        bottomLeft: CGPoint(x: rect.minX, y: rect.minY),
        bottomRight: CGPoint(x: rect.maxX, y: rect.minY)
    )
}

// MARK: - OCR Processing

func performOCRSync(from imageURL: URL) throws -> [Line] {
    log("Loading image from \(imageURL.path)")

    // Load the image as NSImage.
    guard let nsImage = NSImage(contentsOf: imageURL) else {
        log("❌ Could not load image")
        return []
    }
    guard let cgImage = nsImage.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        log("❌ Could not get CGImage")
        return []
    }

    // Set up the Vision request.
    let requestHandler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    let request = VNRecognizeTextRequest()
    request.recognitionLanguages = ["en_US"]
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    try requestHandler.perform([request])

    guard let observations = request.results else {
        log("❌ No text observations found.")
        return []
    }

    var lines: [Line] = []

    for obs in observations {
        guard let candidate = obs.topCandidates(1).first else { continue }
        let lineText = candidate.string

        // Get character boxes from the observation
        // characterBoxes is an array of CGRect that corresponds to each character in the recognized text
        let characterBoxes = obs.characterBoxes ?? []

        // Split line text into words
        let wordStrings = lineText.split(separator: " ").map { String($0) }

        // Map characters to words based on text content
        // characterBoxes array corresponds to characters in lineText (including spaces)
        var charIndexInLine = 0
        var words: [Word] = []

        for wordStr in wordStrings {
            // Find character boxes for this word
            // Skip spaces between words
            while charIndexInLine < lineText.count &&
                  charIndexInLine < characterBoxes.count &&
                  lineText[lineText.index(lineText.startIndex, offsetBy: charIndexInLine)] == " " {
                charIndexInLine += 1
            }

            let wordLength = wordStr.count
            let wordCharBoxes: [CGRect]
            if charIndexInLine + wordLength <= characterBoxes.count {
                wordCharBoxes = Array(characterBoxes[charIndexInLine..<charIndexInLine + wordLength])
            } else {
                // Fallback: use line bounding box if character boxes are not available
                wordCharBoxes = []
            }
            charIndexInLine += wordLength

            // Calculate word bounding box from character boxes
            let wordBoundingBox = wordCharBoxes.isEmpty ? obs.boundingBox : boundingBox(from: wordCharBoxes)
            let wordCorners = cornerPoints(from: wordBoundingBox)

            // Create letters with individual character bounding boxes
            var letters: [Letter] = []
            for (letterIndex, char) in wordStr.enumerated() {
                let letterBox: CGRect
                if letterIndex < wordCharBoxes.count {
                    letterBox = wordCharBoxes[letterIndex]
                } else {
                    // Fallback: estimate letter box from word box
                    let letterWidth = wordBoundingBox.width / CGFloat(wordStr.count)
                    letterBox = CGRect(
                        x: wordBoundingBox.minX + CGFloat(letterIndex) * letterWidth,
                        y: wordBoundingBox.minY,
                        width: letterWidth,
                        height: wordBoundingBox.height
                    )
                }
                let letterCorners = cornerPoints(from: letterBox)

                let letter = Letter(
                    text: String(char),
                    boundingBox: normalizedRect(from: letterBox),
                    topLeft: codablePoint(from: letterCorners.topLeft),
                    topRight: codablePoint(from: letterCorners.topRight),
                    bottomLeft: codablePoint(from: letterCorners.bottomLeft),
                    bottomRight: codablePoint(from: letterCorners.bottomRight),
                    angleDegrees: 0.0,
                    angleRadians: 0.0,
                    confidence: candidate.confidence
                )
                letters.append(letter)
            }

            // Create word with proper bounding box
            let word = Word(
                text: wordStr,
                boundingBox: normalizedRect(from: wordBoundingBox),
                topLeft: codablePoint(from: wordCorners.topLeft),
                topRight: codablePoint(from: wordCorners.topRight),
                bottomLeft: codablePoint(from: wordCorners.bottomLeft),
                bottomRight: codablePoint(from: wordCorners.bottomRight),
                angleDegrees: 0.0,
                angleRadians: 0.0,
                confidence: candidate.confidence,
                letters: letters,
                extractedData: nil
            )
            words.append(word)
        }

        // Create line with line-level bounding box
        let lineCorners = cornerPoints(from: obs.boundingBox)
        let line = Line(
            text: lineText,
            boundingBox: normalizedRect(from: obs.boundingBox),
            topLeft: codablePoint(from: lineCorners.topLeft),
            topRight: codablePoint(from: lineCorners.topRight),
            bottomLeft: codablePoint(from: lineCorners.bottomLeft),
            bottomRight: codablePoint(from: lineCorners.bottomRight),
            angleDegrees: 0.0,
            angleRadians: 0.0,
            confidence: candidate.confidence,
            words: words
        )
        lines.append(line)
    }

    log("✅ OCR processing complete. Found \(lines.count) lines.")
    return lines
}

// MARK: - Natural Language Extraction

func performNLExtraction(on aggregatedText: String, mutableLines: inout [Line], wordMappings: [WordMapping]) {
    let types: NSTextCheckingResult.CheckingType = [.date, .phoneNumber, .address, .link]
    guard let detector = try? NSDataDetector(types: types.rawValue) else {
        log("❌ Could not create NSDataDetector")
        return
    }

    let nsAggregatedText = aggregatedText as NSString
    let matches = detector.matches(in: aggregatedText, options: [], range: NSRange(location: 0, length: nsAggregatedText.length))

    for match in matches {
        var extracted: ExtractedData?
        switch match.resultType {
        case .address:
            if let range = Range(match.range, in: aggregatedText) {
                let address = String(aggregatedText[range])
                extracted = ExtractedData(type: "address", value: address)
            }
        case .phoneNumber:
            if let phone = match.phoneNumber {
                extracted = ExtractedData(type: "phone", value: phone)
            }
        case .date:
            if let date = match.date {
                extracted = ExtractedData(type: "date", value: "\(date)")
            }
        case .link:
            if let url = match.url {
                extracted = ExtractedData(type: "url", value: url.absoluteString)
            }
        default:
            break
        }
        guard let extractedData = extracted else { continue }

        // Map the extracted data back to words whose ranges overlap with the match's range.
        for mapping in wordMappings {
            let intersection = NSIntersectionRange(match.range, mapping.range)
            if intersection.length > 0 {
                // Only assign if there's no extracted data already (or use your own rule to override)
                if mutableLines[mapping.lineIndex].words[mapping.wordIndex].extractedData == nil {
                    mutableLines[mapping.lineIndex].words[mapping.wordIndex].extractedData = extractedData
                }
            }
        }
    }
}

// MARK: - Main Execution

// Expect at least three arguments: the script name, output directory, and at least one image path.
if CommandLine.arguments.count > 2 {
    // First argument is the directory where JSON files will be dumped.
    let outputDirectoryPath = CommandLine.arguments[1]
    let outputDirURL = URL(fileURLWithPath: outputDirectoryPath, isDirectory: true)

    // Ensure the output directory exists.
    try? FileManager.default.createDirectory(at: outputDirURL, withIntermediateDirectories: true, attributes: nil)

    // Process each image (starting from argument index 2).
    for i in 2..<CommandLine.arguments.count {
        let imagePath = CommandLine.arguments[i]
        let imageURL = URL(fileURLWithPath: imagePath)

        do {
            // Perform OCR for the image.
            let ocrLines = try performOCRSync(from: imageURL)
            var mutableLines = ocrLines  // Create a mutable copy.

            // Build aggregated text and record the global ranges of each word.
            var aggregatedText = ""
            var wordMappings: [WordMapping] = []
            var currentLocation = 0

            for (lineIndex, line) in mutableLines.enumerated() {
                for (wordIndex, word) in line.words.enumerated() {
                    let nsWord = word.text as NSString
                    let wordRange = NSRange(location: currentLocation, length: nsWord.length)
                    wordMappings.append(WordMapping(lineIndex: lineIndex, wordIndex: wordIndex, range: wordRange))
                    aggregatedText.append(word.text)
                    aggregatedText.append(" ") // Use a space as a separator.
                    currentLocation += nsWord.length + 1  // Account for the space.
                }
            }

            // Run natural language extraction on the aggregated text.
            performNLExtraction(on: aggregatedText, mutableLines: &mutableLines, wordMappings: wordMappings)

            // Prepare the result for this image.
            let result = ImageResult(imagePath: imagePath, lines: mutableLines)

            // Encode the result to JSON.
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted]
            encoder.keyEncodingStrategy = .convertToSnakeCase
            let jsonData = try encoder.encode(result)

            // Derive an output file name from the image's name.
            let imageFileName = imageURL.deletingPathExtension().lastPathComponent
            let outputFileName = imageFileName + ".json"
            let outputFileURL = outputDirURL.appendingPathComponent(outputFileName)

            try jsonData.write(to: outputFileURL)
            print("Results for \(imagePath) written to \(outputFileURL.path)")
        } catch {
            log("Error processing image at \(imagePath): \(error)")
        }
    }
} else {
    print("Usage: \(CommandLine.arguments[0]) /path/to/output_directory /path/to/image1.png /path/to/image2.png ...")
}
