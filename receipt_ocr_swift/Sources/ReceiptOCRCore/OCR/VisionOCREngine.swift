import Foundation

#if os(macOS)
import AppKit
import Vision

// NOTE: Code Duplication with OCRSwift.swift
//
// This file contains duplicated code (data models, helper functions, and OCR logic)
// that also exists in receipt_upload/receipt_upload/OCRSwift.swift.
//
// This duplication is intentional due to different deployment targets:
// - VisionOCREngine.swift: Part of Swift Package with AWS SDK dependencies (Soto, etc.)
// - OCRSwift.swift: Standalone script executed directly by Python (no package dependencies)
//
// Future refactoring could extract shared code into a common Swift Package module if
// deployment targets converge. For now, changes must be manually synchronized between files.

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
    let type: String
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
    var letters: [Letter]
    var extractedData: ExtractedData?
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

private struct ImageResult: Codable {
    let imagePath: String
    let lines: [Line]

    private enum CodingKeys: String, CodingKey {
        case lines
    }

    init(imagePath: String, lines: [Line]) {
        self.imagePath = imagePath
        self.lines = lines
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.lines = try container.decode([Line].self, forKey: .lines)
        self.imagePath = ""
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(lines, forKey: .lines)
    }
}

private struct WordMapping {
    var lineIndex: Int
    var wordIndex: Int
    var range: NSRange
}

private func codablePoint(from point: CGPoint) -> CodablePoint {
    return CodablePoint(x: point.x, y: point.y)
}

private func normalizedRect(from rect: CGRect) -> NormalizedRect {
    return NormalizedRect(x: rect.origin.x, y: rect.origin.y, width: rect.size.width, height: rect.size.height)
}

/// Calculate bounding box from multiple character boxes
private func boundingBox(from characterBoxes: [CGRect]) -> CGRect {
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
private func cornerPoints(from rect: CGRect) -> (topLeft: CGPoint, topRight: CGPoint, bottomLeft: CGPoint, bottomRight: CGPoint) {
    return (
        topLeft: CGPoint(x: rect.minX, y: rect.maxY),
        topRight: CGPoint(x: rect.maxX, y: rect.maxY),
        bottomLeft: CGPoint(x: rect.minX, y: rect.minY),
        bottomRight: CGPoint(x: rect.maxX, y: rect.minY)
    )
}

private func performOCRSync(from imageURL: URL) throws -> [Line] {
    guard let nsImage = NSImage(contentsOf: imageURL) else { return [] }
    guard let cgImage = nsImage.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return [] }

    let requestHandler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    let request = VNRecognizeTextRequest()
    request.recognitionLanguages = ["en_US"]
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    try requestHandler.perform([request])

    guard let observations = request.results else { return [] }

    var lines: [Line] = []
    for obs in observations {
        guard let candidate = obs.topCandidates(1).first else { continue }
        let lineText = candidate.string

        // Get character boxes from the observation
        // Note: characterBoxes only includes boxes for visible glyphs (no spaces)
        let characterBoxes = obs.characterBoxes ?? []

        // Split line text into words
        let wordStrings = lineText.split(separator: " ").map { String($0) }

        // Map characters to words using separate indices
        // lineTextIndex: position in lineText (includes spaces)
        // boxIndex: position in characterBoxes (no spaces, only visible glyphs)
        var lineTextIndex = 0
        var boxIndex = 0
        var words: [Word] = []

        for wordStr in wordStrings {
            // Skip spaces in lineText (advance lineTextIndex but not boxIndex)
            while lineTextIndex < lineText.count {
                let char = lineText[lineText.index(lineText.startIndex, offsetBy: lineTextIndex)]
                if char == " " {
                    lineTextIndex += 1
                } else {
                    break
                }
            }

            // Consume wordLength boxes from characterBoxes (starting at boxIndex)
            let wordLength = wordStr.count
            let wordCharBoxes: [CGRect]
            if boxIndex + wordLength <= characterBoxes.count {
                wordCharBoxes = Array(characterBoxes[boxIndex..<boxIndex + wordLength])
                boxIndex += wordLength
            } else {
                // Fallback: use line bounding box if character boxes are not available
                wordCharBoxes = []
            }

            // Advance lineTextIndex past the word (spaces will be skipped in next iteration)
            lineTextIndex += wordLength

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
    return lines
}

private func performNLExtraction(on aggregatedText: String, mutableLines: inout [Line], wordMappings: [WordMapping]) {
    let types: NSTextCheckingResult.CheckingType = [.date, .phoneNumber, .address, .link]
    guard let detector = try? NSDataDetector(types: types.rawValue) else { return }
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
            if let phone = match.phoneNumber { extracted = ExtractedData(type: "phone", value: phone) }
        case .date:
            if let date = match.date { extracted = ExtractedData(type: "date", value: "\(date)") }
        case .link:
            if let url = match.url { extracted = ExtractedData(type: "url", value: url.absoluteString) }
        default:
            break
        }
        guard let extractedData = extracted else { continue }
        for mapping in wordMappings {
            let intersection = NSIntersectionRange(match.range, mapping.range)
            if intersection.length > 0 {
                if mutableLines[mapping.lineIndex].words[mapping.wordIndex].extractedData == nil {
                    mutableLines[mapping.lineIndex].words[mapping.wordIndex].extractedData = extractedData
                }
            }
        }
    }
}

public struct VisionOCREngine: OCREngineProtocol {
    public init() {}

    public func process(images: [URL], outputDirectory: URL) throws -> [URL] {
        try FileManager.default.createDirectory(at: outputDirectory, withIntermediateDirectories: true)
        var outputs: [URL] = []
        for imageURL in images {
            let ocrLines = try performOCRSync(from: imageURL)
            var mutableLines = ocrLines
            var aggregatedText = ""
            var wordMappings: [WordMapping] = []
            var currentLocation = 0
            for (lineIndex, line) in mutableLines.enumerated() {
                for (wordIndex, word) in line.words.enumerated() {
                    let nsWord = word.text as NSString
                    let range = NSRange(location: currentLocation, length: nsWord.length)
                    wordMappings.append(WordMapping(lineIndex: lineIndex, wordIndex: wordIndex, range: range))
                    aggregatedText.append(word.text)
                    aggregatedText.append(" ")
                    currentLocation += nsWord.length + 1
                }
            }
            performNLExtraction(on: aggregatedText, mutableLines: &mutableLines, wordMappings: wordMappings)

            let result = ImageResult(imagePath: imageURL.path, lines: mutableLines)
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted]
            encoder.keyEncodingStrategy = .convertToSnakeCase
            let jsonData = try encoder.encode(result)
            let outName = imageURL.deletingPathExtension().lastPathComponent + ".json"
            let outURL = outputDirectory.appendingPathComponent(outName)
            try jsonData.write(to: outURL)
            outputs.append(outURL)
        }
        return outputs
    }
}
#endif


