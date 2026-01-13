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

    /// URL to the compiled model (cleaned up in deinit)
    private let compiledModelURL: URL

    // MARK: - Initialization

    /// Initialize from a model bundle directory.
    ///
    /// The bundle should contain:
    /// - *.mlpackage/ (CoreML model - any name)
    /// - vocab.txt (tokenizer vocabulary)
    /// - config.json (model configuration with labels)
    ///
    /// - Parameter bundlePath: URL to the model bundle directory
    public init(bundlePath: URL) throws {
        // Find CoreML model by scanning for .mlpackage in bundle
        let contents = try FileManager.default.contentsOfDirectory(
            at: bundlePath,
            includingPropertiesForKeys: nil
        )
        guard let modelURL = contents.first(where: { $0.pathExtension == "mlpackage" }) else {
            throw LayoutLMError.modelNotFound(path: bundlePath.path)
        }

        // Compile model (creates temp file) and load it
        // Store compiledURL for cleanup in deinit - MLModel may reference on-disk artifacts
        let compiledURL = try MLModel.compileModel(at: modelURL)
        self.compiledModelURL = compiledURL
        self.model = try MLModel(contentsOf: compiledURL)

        // Load tokenizer
        let vocabURL = bundlePath.appendingPathComponent("vocab.txt")
        guard FileManager.default.fileExists(atPath: vocabURL.path) else {
            throw LayoutLMError.vocabNotFound(path: vocabURL.path)
        }
        self.tokenizer = try BertTokenizer(vocabURL: vocabURL)

        // Load config
        let configURL = bundlePath.appendingPathComponent("config.json")
        guard FileManager.default.fileExists(atPath: configURL.path) else {
            throw LayoutLMError.configNotFound(path: configURL.path)
        }
        self.config = try LayoutLMConfig.load(from: configURL)

        self.maxSeqLength = config.maxPositionEmbeddings ?? 512
    }

    deinit {
        // Clean up compiled model temp file
        try? FileManager.default.removeItem(at: compiledModelURL)
    }

    // MARK: - Prediction

    /// Predict labels for words in OCR lines.
    ///
    /// This matches the Python inference behavior:
    /// 1. Extract words from lines
    /// 2. Compute per-receipt bbox extents
    /// 3. Tokenize with word ID tracking
    /// 4. Normalize bboxes to [0, 1000]
    /// 5. Run CoreML prediction
    /// 6. Aggregate subtoken logits by word (average)
    /// 7. Apply softmax and argmax
    ///
    /// - Parameter lines: Array of OCR lines from receipt
    /// - Returns: Array of LinePrediction results
    public func predict(lines: [Line]) throws -> [LinePrediction] {
        var results: [LinePrediction] = []

        // Compute bbox extents for the entire receipt
        let (maxX, maxY) = BboxNormalizer.computeExtents(lines: lines)

        for line in lines {
            let prediction = try predictLine(
                words: line.words,
                maxX: maxX,
                maxY: maxY
            )
            results.append(prediction)
        }

        return results
    }

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
