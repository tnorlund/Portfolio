import Foundation

#if os(macOS)
import AppKit
import Vision

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
        let wordStrings = lineText.split(separator: " ").map { String($0) }
        var words: [Word] = []
        for wordStr in wordStrings {
            let word = Word(
                text: wordStr,
                boundingBox: normalizedRect(from: obs.boundingBox),
                topLeft: codablePoint(from: obs.topLeft),
                topRight: codablePoint(from: obs.topRight),
                bottomLeft: codablePoint(from: obs.bottomLeft),
                bottomRight: codablePoint(from: obs.bottomRight),
                angleDegrees: 0.0,
                angleRadians: 0.0,
                confidence: candidate.confidence,
                letters: wordStr.map { ch in
                    Letter(
                        text: String(ch),
                        boundingBox: normalizedRect(from: obs.boundingBox),
                        topLeft: codablePoint(from: obs.topLeft),
                        topRight: codablePoint(from: obs.topRight),
                        bottomLeft: codablePoint(from: obs.bottomLeft),
                        bottomRight: codablePoint(from: obs.bottomRight),
                        angleDegrees: 0.0,
                        angleRadians: 0.0,
                        confidence: candidate.confidence
                    )
                },
                extractedData: nil
            )
            words.append(word)
        }

        let line = Line(
            text: lineText,
            boundingBox: normalizedRect(from: obs.boundingBox),
            topLeft: codablePoint(from: obs.topLeft),
            topRight: codablePoint(from: obs.topRight),
            bottomLeft: codablePoint(from: obs.bottomLeft),
            bottomRight: codablePoint(from: obs.bottomRight),
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


