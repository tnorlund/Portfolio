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

public struct CodablePoint: Codable {
    public let x: CGFloat
    public let y: CGFloat

    public init(x: CGFloat, y: CGFloat) {
        self.x = x
        self.y = y
    }
}

public struct NormalizedRect: Codable {
    public let x: CGFloat
    public let y: CGFloat
    public let width: CGFloat
    public let height: CGFloat

    public init(x: CGFloat, y: CGFloat, width: CGFloat, height: CGFloat) {
        self.x = x
        self.y = y
        self.width = width
        self.height = height
    }
}

public struct ExtractedData: Codable {
    public let type: String
    public let value: String

    public init(type: String, value: String) {
        self.type = type
        self.value = value
    }
}

public struct Letter: Codable {
    public let text: String
    public let boundingBox: NormalizedRect
    public let topLeft: CodablePoint
    public let topRight: CodablePoint
    public let bottomLeft: CodablePoint
    public let bottomRight: CodablePoint
    public let angleDegrees: CGFloat
    public let angleRadians: CGFloat
    public let confidence: VNConfidence

    public init(text: String, boundingBox: NormalizedRect, topLeft: CodablePoint, topRight: CodablePoint, bottomLeft: CodablePoint, bottomRight: CodablePoint, angleDegrees: CGFloat, angleRadians: CGFloat, confidence: VNConfidence) {
        self.text = text
        self.boundingBox = boundingBox
        self.topLeft = topLeft
        self.topRight = topRight
        self.bottomLeft = bottomLeft
        self.bottomRight = bottomRight
        self.angleDegrees = angleDegrees
        self.angleRadians = angleRadians
        self.confidence = confidence
    }
}

public struct Word: Codable {
    public let text: String
    public let boundingBox: NormalizedRect
    public let topLeft: CodablePoint
    public let topRight: CodablePoint
    public let bottomLeft: CodablePoint
    public let bottomRight: CodablePoint
    public let angleDegrees: CGFloat
    public let angleRadians: CGFloat
    public let confidence: VNConfidence
    public var letters: [Letter]
    public var extractedData: ExtractedData?

    public init(text: String, boundingBox: NormalizedRect, topLeft: CodablePoint, topRight: CodablePoint, bottomLeft: CodablePoint, bottomRight: CodablePoint, angleDegrees: CGFloat, angleRadians: CGFloat, confidence: VNConfidence, letters: [Letter], extractedData: ExtractedData?) {
        self.text = text
        self.boundingBox = boundingBox
        self.topLeft = topLeft
        self.topRight = topRight
        self.bottomLeft = bottomLeft
        self.bottomRight = bottomRight
        self.angleDegrees = angleDegrees
        self.angleRadians = angleRadians
        self.confidence = confidence
        self.letters = letters
        self.extractedData = extractedData
    }
}

public struct Line: Codable {
    public let text: String
    public let boundingBox: NormalizedRect
    public let topLeft: CodablePoint
    public let topRight: CodablePoint
    public let bottomLeft: CodablePoint
    public let bottomRight: CodablePoint
    public let angleDegrees: CGFloat
    public let angleRadians: CGFloat
    public let confidence: VNConfidence
    public var words: [Word]

    public init(text: String, boundingBox: NormalizedRect, topLeft: CodablePoint, topRight: CodablePoint, bottomLeft: CodablePoint, bottomRight: CodablePoint, angleDegrees: CGFloat, angleRadians: CGFloat, confidence: VNConfidence, words: [Word]) {
        self.text = text
        self.boundingBox = boundingBox
        self.topLeft = topLeft
        self.topRight = topRight
        self.bottomLeft = bottomLeft
        self.bottomRight = bottomRight
        self.angleDegrees = angleDegrees
        self.angleRadians = angleRadians
        self.confidence = confidence
        self.words = words
    }
}

/// Enhanced OCR result including classification and clustering metadata
private struct ImageResult: Codable {
    let imagePath: String
    let lines: [Line]
    let classification: ClassificationResult?
    let clustering: ClusteringResult?

    private enum CodingKeys: String, CodingKey {
        case lines
        case classification
        case clustering
    }

    init(imagePath: String, lines: [Line], classification: ClassificationResult? = nil, clustering: ClusteringResult? = nil) {
        self.imagePath = imagePath
        self.lines = lines
        self.classification = classification
        self.clustering = clustering
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.lines = try container.decode([Line].self, forKey: .lines)
        self.classification = try container.decodeIfPresent(ClassificationResult.self, forKey: .classification)
        self.clustering = try container.decodeIfPresent(ClusteringResult.self, forKey: .clustering)
        self.imagePath = ""
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(lines, forKey: .lines)
        try container.encodeIfPresent(classification, forKey: .classification)
        try container.encodeIfPresent(clustering, forKey: .clustering)
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

    // Set up the Vision request.
    // NOTE: We use .accurate mode for better text recognition accuracy.
    // Word-level bounding boxes work fine with .accurate mode using boundingBox(for: wordRange).
    // Character-level boxes may have issues in .accurate mode, so we estimate them from word boxes.
    let requestHandler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    let request = VNRecognizeTextRequest()
    request.recognitionLanguages = ["en_US"]
    request.recognitionLevel = .accurate  // Use .accurate for better text recognition
    request.usesLanguageCorrection = true
    try requestHandler.perform([request])

    guard let observations = request.results else { return [] }

    var lines: [Line] = []
    for obs in observations {
        guard let candidate = obs.topCandidates(1).first else { continue }
        let lineText = candidate.string
        let lineTextStartIndex = lineText.startIndex

        // Split line text into words (preserving word positions in the line)
        let wordStrings = lineText.split(separator: " ", omittingEmptySubsequences: false)
        var words: [Word] = []
        var currentIndex = lineTextStartIndex

        for wordStr in wordStrings {
            // Skip spaces before this word
            while currentIndex < lineText.endIndex && lineText[currentIndex] == " " {
                currentIndex = lineText.index(after: currentIndex)
            }

            // If we've reached the end or this is an empty word, skip
            if currentIndex >= lineText.endIndex || wordStr.isEmpty {
                continue
            }

            // Get the range for this word in the line text
            let wordStartIndex = currentIndex
            let wordEndIndex = lineText.index(wordStartIndex, offsetBy: wordStr.count)
            let wordRange = wordStartIndex..<wordEndIndex

            // Get word bounding box using Vision API
            // This works in both .accurate and .fast modes
            let wordBoundingBox: CGRect
            do {
                wordBoundingBox = try candidate.boundingBox(for: wordRange)?.boundingBox ?? obs.boundingBox
            } catch {
                // Fallback to observation bounding box if range lookup fails
                wordBoundingBox = obs.boundingBox
            }

            let wordCorners = cornerPoints(from: wordBoundingBox)

            // Get character bounding boxes for each letter in the word
            // NOTE: In .accurate mode, character-level boundingBox(for:) may return identical boxes.
            // We estimate letter boxes from the word box, which is more reliable.
            var letters: [Letter] = []
            for (letterIndex, char) in wordStr.enumerated() {
                // Estimate letter box from word box (proportional distribution)
                // This is more reliable than trying to get individual character boxes
                let letterWidth = wordBoundingBox.width / CGFloat(wordStr.count)
                let letterBox = CGRect(
                    x: wordBoundingBox.minX + CGFloat(letterIndex) * letterWidth,
                    y: wordBoundingBox.minY,
                    width: letterWidth,
                    height: wordBoundingBox.height
                )

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

            // Advance past this word
            currentIndex = wordEndIndex

            // Create word with proper bounding box
            let word = Word(
                text: String(wordStr),
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
    private let classifier = ImageClassifier()
    private let clusterer = LineClusterer()

    /// Whether to include classification and clustering in output
    public var includeClassification: Bool = true

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

            // Perform classification and clustering if enabled
            var classification: ClassificationResult? = nil
            var clustering: ClusteringResult? = nil

            if includeClassification, let imageDimensions = getImageDimensions(from: imageURL) {
                // Classify the image
                classification = classifier.classify(
                    lines: mutableLines,
                    imageWidth: imageDimensions.width,
                    imageHeight: imageDimensions.height
                )

                // Cluster based on image type
                if let classResult = classification {
                    switch classResult.imageType {
                    case .native:
                        // Single receipt, no clustering needed
                        clustering = ClusteringResult(clusters: [1: Array(0..<mutableLines.count)])
                    case .photo:
                        // Use 2D DBSCAN for photos
                        let avgDiagonal = clusterer.averageDiagonalLength(lines: mutableLines)
                        let eps = avgDiagonal * 2  // Dynamic eps based on line size
                        clustering = clusterer.dbscanLines(lines: mutableLines, eps: eps, minSamples: 10)
                    case .scan:
                        // Use X-axis clustering for scans
                        clustering = clusterer.dbscanLinesXAxis(lines: mutableLines, eps: 0.08, minSamples: 2)
                    }
                }
            }

            let result = ImageResult(
                imagePath: imageURL.path,
                lines: mutableLines,
                classification: classification,
                clustering: clustering
            )
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

    /// Get image dimensions from a file URL
    private func getImageDimensions(from url: URL) -> (width: Int, height: Int)? {
        guard let nsImage = NSImage(contentsOf: url) else { return nil }
        guard let cgImage = nsImage.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return nil }
        return (width: cgImage.width, height: cgImage.height)
    }
}
#endif


