import Foundation

#if os(macOS)
import CoreML

/// Result of LayoutLM inference for a single line.
public struct LinePrediction: Codable {
    /// Original word tokens
    public let tokens: [String]

    /// Predicted labels for each token (e.g., "B-MERCHANT_NAME", "O")
    public let labels: [String]

    /// Confidence scores for each prediction
    public let confidences: [Float]

    /// All class probabilities per word (optional, for debugging)
    public let allProbabilities: [[String: Float]]?

    private enum CodingKeys: String, CodingKey {
        case tokens
        case labels
        case confidences
        case allProbabilities = "all_probabilities"
    }
}

/// LayoutLM inference engine using CoreML.
///
/// This class loads a CoreML model bundle and runs token classification
/// inference on receipt OCR output.
public class LayoutLMInference {

    /// The CoreML model
    private let model: MLModel

    /// BERT tokenizer for WordPiece tokenization
    private let tokenizer: BertTokenizer

    /// Model configuration (labels)
    private let config: LayoutLMConfig

    /// Maximum sequence length
    public let maxSeqLength: Int

    // MARK: - Initialization

    /// Initialize from a model bundle directory.
    ///
    /// The bundle should contain:
    /// - *.mlpackage/ (CoreML model - any name)
    /// - vocab.txt (tokenizer vocabulary)
    /// - config.json (model configuration with labels)
    ///
    /// On first init the .mlpackage is compiled and the resulting .mlmodelc
    /// is persisted next to the .mlpackage so subsequent launches skip the
    /// expensive compilation step.
    ///
    /// - Parameter bundlePath: URL to the model bundle directory
    public init(bundlePath: URL) throws {
        let fileManager = FileManager.default

        // Find CoreML model by scanning for .mlpackage in bundle
        let contents = try fileManager.contentsOfDirectory(
            at: bundlePath,
            includingPropertiesForKeys: nil
        )
        guard let modelURL = contents.first(where: { $0.pathExtension == "mlpackage" }) else {
            throw LayoutLMError.modelNotFound(path: bundlePath.path)
        }

        // Persistent path for the compiled model (.mlmodelc) next to the .mlpackage
        let compiledName = modelURL.deletingPathExtension().lastPathComponent + ".mlmodelc"
        let persistentCompiledURL = bundlePath.appendingPathComponent(compiledName)

        if fileManager.fileExists(atPath: persistentCompiledURL.path) {
            // Reuse previously compiled model — instant load
            self.model = try MLModel(contentsOf: persistentCompiledURL)
        } else {
            // First run: compile .mlpackage → temp .mlmodelc, then persist it
            let tempCompiledURL = try MLModel.compileModel(at: modelURL)
            do {
                try fileManager.moveItem(at: tempCompiledURL, to: persistentCompiledURL)
            } catch {
                // Another process may have already placed the compiled model,
                // or the move failed for another reason (e.g. cross-volume).
                if !fileManager.fileExists(atPath: persistentCompiledURL.path) {
                    try fileManager.copyItem(at: tempCompiledURL, to: persistentCompiledURL)
                }
                try? fileManager.removeItem(at: tempCompiledURL)
            }
            self.model = try MLModel(contentsOf: persistentCompiledURL)
        }

        // Load tokenizer
        let vocabURL = bundlePath.appendingPathComponent("vocab.txt")
        guard fileManager.fileExists(atPath: vocabURL.path) else {
            throw LayoutLMError.vocabNotFound(path: vocabURL.path)
        }
        self.tokenizer = try BertTokenizer(vocabURL: vocabURL)

        // Load config
        let configURL = bundlePath.appendingPathComponent("config.json")
        guard fileManager.fileExists(atPath: configURL.path) else {
            throw LayoutLMError.configNotFound(path: configURL.path)
        }
        self.config = try LayoutLMConfig.load(from: configURL)

        self.maxSeqLength = config.maxPositionEmbeddings ?? 512
    }

    // MARK: - Prediction

    /// Predict labels for words in OCR lines using batched inference.
    ///
    /// This optimized version packs multiple lines into single CoreML calls
    /// to reduce inference overhead. Lines are greedily packed into sequences
    /// that fit within the 512 token limit.
    ///
    /// - Parameter lines: Array of OCR lines from receipt
    /// - Returns: Array of LinePrediction results
    public func predict(lines: [Line]) throws -> [LinePrediction] {
        guard !lines.isEmpty else { return [] }

        // Compute bbox extents for the entire receipt
        let (maxX, maxY) = BboxNormalizer.computeExtents(lines: lines)

        // Pre-process all lines: extract words, normalize bboxes, pre-tokenize
        var lineData: [(words: [String], bboxes: [[Int32]], tokenCount: Int)] = []

        for line in lines {
            if line.words.isEmpty {
                lineData.append((words: [], bboxes: [], tokenCount: 0))
                continue
            }

            let (texts, wordBboxes) = BboxNormalizer.normalizeWords(line.words, maxX: maxX, maxY: maxY)
            // Pre-tokenize to get token count (without [CLS], [SEP], padding)
            let tokenCount = countTokens(words: texts)
            lineData.append((words: texts, bboxes: wordBboxes, tokenCount: tokenCount))
        }

        // Pack lines into batches that fit within maxSeqLength
        // Reserve 2 tokens for [CLS] and [SEP]
        let maxTokensPerBatch = maxSeqLength - 2
        var batches: [[(lineIndex: Int, words: [String], bboxes: [[Int32]])]] = []
        var currentBatch: [(lineIndex: Int, words: [String], bboxes: [[Int32]])] = []
        var currentTokenCount = 0

        for (lineIndex, data) in lineData.enumerated() {
            // Skip empty lines - they'll get empty predictions
            if data.words.isEmpty {
                continue
            }

            // If this line alone exceeds limit, it needs its own batch (will be truncated)
            if data.tokenCount > maxTokensPerBatch {
                // Flush current batch if non-empty
                if !currentBatch.isEmpty {
                    batches.append(currentBatch)
                    currentBatch = []
                    currentTokenCount = 0
                }
                // Add oversized line as its own batch
                batches.append([(lineIndex: lineIndex, words: data.words, bboxes: data.bboxes)])
                continue
            }

            // Check if line fits in current batch
            if currentTokenCount + data.tokenCount <= maxTokensPerBatch {
                currentBatch.append((lineIndex: lineIndex, words: data.words, bboxes: data.bboxes))
                currentTokenCount += data.tokenCount
            } else {
                // Start new batch
                batches.append(currentBatch)
                currentBatch = [(lineIndex: lineIndex, words: data.words, bboxes: data.bboxes)]
                currentTokenCount = data.tokenCount
            }
        }

        // Don't forget the last batch
        if !currentBatch.isEmpty {
            batches.append(currentBatch)
        }

        // Run inference on each batch and collect results
        var results: [LinePrediction?] = Array(repeating: nil, count: lines.count)

        for batch in batches {
            let batchPredictions = try predictBatch(batch)
            for (i, item) in batch.enumerated() {
                results[item.lineIndex] = batchPredictions[i]
            }
        }

        // Fill in empty predictions for lines that were skipped
        return results.enumerated().map { (index, prediction) in
            prediction ?? LinePrediction(
                tokens: [],
                labels: [],
                confidences: [],
                allProbabilities: nil
            )
        }
    }

    /// Count tokens for a list of words (without special tokens or padding).
    private func countTokens(words: [String]) -> Int {
        var count = 0
        for word in words {
            // Use tokenizer to count subtokens
            let result = tokenizer.tokenize(words: [word], padding: false, truncation: false)
            // Subtract 2 for [CLS] and [SEP] that tokenize() adds
            count += max(0, result.inputIds.count - 2)
        }
        return count
    }

    /// Predict labels for a batch of lines packed into a single sequence.
    private func predictBatch(
        _ batch: [(lineIndex: Int, words: [String], bboxes: [[Int32]])]
    ) throws -> [LinePrediction] {
        // Concatenate all words and bboxes, tracking line boundaries
        var allWords: [String] = []
        var allBboxes: [[Int32]] = []
        var lineWordRanges: [(start: Int, count: Int)] = []

        for item in batch {
            let start = allWords.count
            allWords.append(contentsOf: item.words)
            allBboxes.append(contentsOf: item.bboxes)
            lineWordRanges.append((start: start, count: item.words.count))
        }

        // Tokenize the combined words
        let tokenResult = tokenizer.tokenize(words: allWords, padding: true, truncation: true)

        // Build bbox tensor for subtokens
        var bboxTensor: [[Int32]] = []
        for wordId in tokenResult.wordIds {
            if let wid = wordId, wid < allBboxes.count {
                bboxTensor.append(allBboxes[wid])
            } else {
                bboxTensor.append([0, 0, 0, 0])
            }
        }

        // Create MLMultiArray inputs
        let seqLength = tokenResult.inputIds.count
        let inputIds = try createMultiArray(from: tokenResult.inputIds, shape: [1, seqLength])
        let attentionMask = try createMultiArray(from: tokenResult.attentionMask, shape: [1, seqLength])
        let tokenTypeIds = try createMultiArray(from: tokenResult.tokenTypeIds, shape: [1, seqLength])
        let bbox = try createBboxMultiArray(from: bboxTensor, shape: [1, seqLength, 4])

        // Create feature provider and run prediction
        let inputFeatures = LayoutLMInput(
            input_ids: inputIds,
            attention_mask: attentionMask,
            bbox: bbox,
            token_type_ids: tokenTypeIds
        )
        let output = try model.prediction(from: inputFeatures)

        guard let logitsArray = output.featureValue(for: "logits")?.multiArrayValue else {
            throw LayoutLMError.outputNotFound
        }

        // Split predictions by line
        var predictions: [LinePrediction] = []

        for (batchIdx, item) in batch.enumerated() {
            let range = lineWordRanges[batchIdx]

            // Find word IDs that belong to this line
            // Word IDs in tokenResult.wordIds are global (0 to allWords.count-1)
            // We need words in range [range.start, range.start + range.count)
            let lineWordIds = (range.start..<(range.start + range.count))

            let prediction = aggregatePredictionsForLine(
                logits: logitsArray,
                wordIds: tokenResult.wordIds,
                targetWordIds: Set(lineWordIds),
                words: Array(allWords[range.start..<(range.start + range.count)]),
                wordIdOffset: range.start
            )
            predictions.append(prediction)
        }

        return predictions
    }

    /// Aggregate predictions for a specific line within a batched sequence.
    private func aggregatePredictionsForLine(
        logits: MLMultiArray,
        wordIds: [Int?],
        targetWordIds: Set<Int>,
        words: [String],
        wordIdOffset: Int
    ) -> LinePrediction {
        let numLabels = logits.shape[2].intValue
        let numWords = words.count

        // Group token indices by word ID (only for words in this line)
        var wordToTokens: [Int: [Int]] = [:]
        for (tokenIdx, wordId) in wordIds.enumerated() {
            if let wid = wordId, targetWordIds.contains(wid) {
                let localWordId = wid - wordIdOffset
                wordToTokens[localWordId, default: []].append(tokenIdx)
            }
        }

        var labels: [String] = []
        var confidences: [Float] = []
        var allProbs: [[String: Float]] = []

        for wordIdx in 0..<numWords {
            let tokenIndices = wordToTokens[wordIdx] ?? []

            if tokenIndices.isEmpty {
                labels.append("O")
                confidences.append(0.0)
                allProbs.append([:])
                continue
            }

            // Average logits across subtokens
            var avgLogits = [Float](repeating: 0, count: numLabels)
            for tokenIdx in tokenIndices {
                for labelIdx in 0..<numLabels {
                    let index = tokenIdx * numLabels + labelIdx
                    avgLogits[labelIdx] += logits[index].floatValue
                }
            }
            for labelIdx in 0..<numLabels {
                avgLogits[labelIdx] /= Float(tokenIndices.count)
            }

            // Softmax and argmax
            let probs = softmax(avgLogits)
            var maxProb: Float = 0
            var maxIdx = 0
            for (idx, prob) in probs.enumerated() {
                if prob > maxProb {
                    maxProb = prob
                    maxIdx = idx
                }
            }

            labels.append(config.labelName(for: maxIdx))
            confidences.append(maxProb.isNaN ? 0.0 : maxProb)

            var probDict: [String: Float] = [:]
            for labelIdx in 0..<numLabels {
                let prob = probs[labelIdx]
                probDict[config.labelName(for: labelIdx)] = prob.isNaN ? 0.0 : prob
            }
            allProbs.append(probDict)
        }

        return LinePrediction(
            tokens: words,
            labels: labels,
            confidences: confidences,
            allProbabilities: allProbs
        )
    }

    // MARK: - Legacy Single-Line Prediction (kept for reference)

    /// Predict labels for a single line of words.
    private func predictLine(
        words: [Word],
        maxX: CGFloat,
        maxY: CGFloat
    ) throws -> LinePrediction {
        guard !words.isEmpty else {
            return LinePrediction(
                tokens: [],
                labels: [],
                confidences: [],
                allProbabilities: nil
            )
        }

        // Extract texts and normalize bboxes
        let (texts, wordBboxes) = BboxNormalizer.normalizeWords(words, maxX: maxX, maxY: maxY)

        // Tokenize
        let tokenResult = tokenizer.tokenize(words: texts, padding: true, truncation: true)

        // Build bbox tensor for subtokens (repeat word bbox for each subtoken)
        var bboxTensor: [[Int32]] = []
        for wordId in tokenResult.wordIds {
            if let wid = wordId, wid < wordBboxes.count {
                bboxTensor.append(wordBboxes[wid])
            } else {
                // Special token ([CLS], [SEP], [PAD]) - use [0, 0, 0, 0]
                bboxTensor.append([0, 0, 0, 0])
            }
        }

        // Create MLMultiArray inputs
        let seqLength = tokenResult.inputIds.count
        let inputIds = try createMultiArray(from: tokenResult.inputIds, shape: [1, seqLength])
        let attentionMask = try createMultiArray(from: tokenResult.attentionMask, shape: [1, seqLength])
        let tokenTypeIds = try createMultiArray(from: tokenResult.tokenTypeIds, shape: [1, seqLength])
        let bbox = try createBboxMultiArray(from: bboxTensor, shape: [1, seqLength, 4])

        // Create feature provider
        let inputFeatures = LayoutLMInput(
            input_ids: inputIds,
            attention_mask: attentionMask,
            bbox: bbox,
            token_type_ids: tokenTypeIds
        )

        // Run prediction
        let output = try model.prediction(from: inputFeatures)

        // Get logits output
        guard let logitsArray = output.featureValue(for: "logits")?.multiArrayValue else {
            throw LayoutLMError.outputNotFound
        }

        // Aggregate logits by word ID and compute predictions
        return aggregatePredictions(
            logits: logitsArray,
            wordIds: tokenResult.wordIds,
            words: texts,
            numWords: texts.count
        )
    }

    /// Aggregate subtoken logits by word and compute final predictions.
    private func aggregatePredictions(
        logits: MLMultiArray,
        wordIds: [Int?],
        words: [String],
        numWords: Int
    ) -> LinePrediction {
        let numLabels = logits.shape[2].intValue

        // Group token indices by word ID
        var wordToTokens: [Int: [Int]] = [:]
        for (tokenIdx, wordId) in wordIds.enumerated() {
            if let wid = wordId {
                wordToTokens[wid, default: []].append(tokenIdx)
            }
        }

        var labels: [String] = []
        var confidences: [Float] = []
        var allProbs: [[String: Float]] = []

        for wordIdx in 0..<numWords {
            let tokenIndices = wordToTokens[wordIdx] ?? []

            if tokenIndices.isEmpty {
                // Word was dropped during tokenization
                labels.append("O")
                confidences.append(0.0)
                allProbs.append([:])
                continue
            }

            // Average logits across subtokens
            var avgLogits = [Float](repeating: 0, count: numLabels)
            for tokenIdx in tokenIndices {
                for labelIdx in 0..<numLabels {
                    let index = tokenIdx * numLabels + labelIdx
                    avgLogits[labelIdx] += logits[index].floatValue
                }
            }
            for labelIdx in 0..<numLabels {
                avgLogits[labelIdx] /= Float(tokenIndices.count)
            }

            // Softmax
            let probs = softmax(avgLogits)

            // Argmax
            var maxProb: Float = 0
            var maxIdx = 0
            for (idx, prob) in probs.enumerated() {
                if prob > maxProb {
                    maxProb = prob
                    maxIdx = idx
                }
            }

            labels.append(config.labelName(for: maxIdx))
            confidences.append(maxProb.isNaN ? 0.0 : maxProb)

            // Build probability dict (sanitize NaN values for JSON encoding)
            var probDict: [String: Float] = [:]
            for labelIdx in 0..<numLabels {
                let prob = probs[labelIdx]
                probDict[config.labelName(for: labelIdx)] = prob.isNaN ? 0.0 : prob
            }
            allProbs.append(probDict)
        }

        return LinePrediction(
            tokens: words,
            labels: labels,
            confidences: confidences,
            allProbabilities: allProbs
        )
    }

    /// Compute softmax of logits with NaN protection.
    private func softmax(_ logits: [Float]) -> [Float] {
        // Sanitize input: replace NaN/Inf with 0
        let sanitized = logits.map { val -> Float in
            if val.isNaN || val.isInfinite {
                return 0
            }
            return val
        }
        let maxLogit = sanitized.max() ?? 0
        let expLogits = sanitized.map { exp($0 - maxLogit) }
        let sumExp = expLogits.reduce(0, +)
        // Guard against division by zero
        if sumExp == 0 || sumExp.isNaN {
            // Return uniform distribution
            let uniform = 1.0 / Float(logits.count)
            return [Float](repeating: uniform, count: logits.count)
        }
        return expLogits.map { $0 / sumExp }
    }

    // MARK: - Helper Methods

    /// Create MLMultiArray from Int array.
    private func createMultiArray(from array: [Int], shape: [Int]) throws -> MLMultiArray {
        let expectedCount = shape.reduce(1, *)
        guard expectedCount == array.count else {
            throw LayoutLMError.predictionFailed(
                "Shape \(shape) expects \(expectedCount) elements, got \(array.count)"
            )
        }
        let mlArray = try MLMultiArray(shape: shape.map { NSNumber(value: $0) }, dataType: .int32)
        for (idx, value) in array.enumerated() {
            mlArray[idx] = NSNumber(value: Int32(value))
        }
        return mlArray
    }

    /// Create MLMultiArray for bbox tensor.
    private func createBboxMultiArray(from bboxes: [[Int32]], shape: [Int]) throws -> MLMultiArray {
        guard shape.count == 3, shape[0] == 1, shape[1] == bboxes.count, shape[2] == 4 else {
            throw LayoutLMError.predictionFailed(
                "Invalid bbox shape: \(shape) for \(bboxes.count) bboxes"
            )
        }
        let mlArray = try MLMultiArray(shape: shape.map { NSNumber(value: $0) }, dataType: .int32)
        for (seqIdx, bbox) in bboxes.enumerated() {
            guard bbox.count == 4 else {
                throw LayoutLMError.predictionFailed(
                    "Bbox must have 4 coordinates, got \(bbox.count)"
                )
            }
            for (coordIdx, value) in bbox.enumerated() {
                let index = seqIdx * 4 + coordIdx
                mlArray[index] = NSNumber(value: value)
            }
        }
        return mlArray
    }
}

// MARK: - CoreML Input Provider

/// Feature provider for LayoutLM model inputs.
private class LayoutLMInput: MLFeatureProvider {
    let input_ids: MLMultiArray
    let attention_mask: MLMultiArray
    let bbox: MLMultiArray
    let token_type_ids: MLMultiArray

    init(input_ids: MLMultiArray, attention_mask: MLMultiArray, bbox: MLMultiArray, token_type_ids: MLMultiArray) {
        self.input_ids = input_ids
        self.attention_mask = attention_mask
        self.bbox = bbox
        self.token_type_ids = token_type_ids
    }

    var featureNames: Set<String> {
        return ["input_ids", "attention_mask", "bbox", "token_type_ids"]
    }

    func featureValue(for featureName: String) -> MLFeatureValue? {
        switch featureName {
        case "input_ids":
            return MLFeatureValue(multiArray: input_ids)
        case "attention_mask":
            return MLFeatureValue(multiArray: attention_mask)
        case "bbox":
            return MLFeatureValue(multiArray: bbox)
        case "token_type_ids":
            return MLFeatureValue(multiArray: token_type_ids)
        default:
            return nil
        }
    }
}

// MARK: - Errors

public enum LayoutLMError: Error, LocalizedError {
    case modelNotFound(path: String)
    case vocabNotFound(path: String)
    case configNotFound(path: String)
    case outputNotFound
    case predictionFailed(String)

    public var errorDescription: String? {
        switch self {
        case .modelNotFound(let path):
            return "CoreML model not found at: \(path)"
        case .vocabNotFound(let path):
            return "Vocabulary file not found at: \(path)"
        case .configNotFound(let path):
            return "Config file not found at: \(path)"
        case .outputNotFound:
            return "Model output 'logits' not found"
        case .predictionFailed(let message):
            return "Prediction failed: \(message)"
        }
    }
}

#endif
