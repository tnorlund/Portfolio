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

    /// Tokenizer function — wraps either BertTokenizer (v1) or BPETokenizer (v3)
    private let tokenizer: (_ words: [String], _ padding: Bool, _ truncation: Bool) -> BertTokenizer.TokenizationResult

    /// Model configuration (labels)
    private let config: LayoutLMConfig

    /// Maximum sequence length
    public let maxSeqLength: Int

    /// Whether this model requires image input (v3)
    public let requiresImageInput: Bool

    /// Sliding-window config for inference. Must match how the model was
    /// trained (data_loader._build_receipt_window_examples): words are sorted
    /// into reading order and cut into `windowSize`-word windows stepped by
    /// `windowStride`, so the model sees the multi-line context it trained on
    /// instead of one packed line at a time. Overridable via
    /// LAYOUTLM_WINDOW_SIZE / LAYOUTLM_WINDOW_STRIDE.
    public let windowSize: Int
    public let windowStride: Int

    /// When false (LAYOUTLM_INFERENCE_MODE=line_packed) falls back to the
    /// legacy greedy line-packing path. Default is windowed.
    public let useWindowedInference: Bool

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
                // Move failed — another process may have won the race, or
                // move isn't supported (cross-volume).  Best-effort copy;
                // if the destination already exists we just use it.
                try? fileManager.copyItem(at: tempCompiledURL, to: persistentCompiledURL)
                try? fileManager.removeItem(at: tempCompiledURL)
            }
            self.model = try MLModel(contentsOf: persistentCompiledURL)
        }

        // Load config
        let configURL = bundlePath.appendingPathComponent("config.json")
        guard fileManager.fileExists(atPath: configURL.path) else {
            throw LayoutLMError.configNotFound(path: configURL.path)
        }
        self.config = try LayoutLMConfig.load(from: configURL)
        self.maxSeqLength = config.maxPositionEmbeddings ?? 512
        self.requiresImageInput = model.modelDescription.inputDescriptionsByName.keys.contains("pixel_values")

        let env = ProcessInfo.processInfo.environment
        self.windowSize = env["LAYOUTLM_WINDOW_SIZE"].flatMap { Int($0) } ?? 250
        self.windowStride = env["LAYOUTLM_WINDOW_STRIDE"].flatMap { Int($0) } ?? 200
        self.useWindowedInference = (env["LAYOUTLM_INFERENCE_MODE"] ?? "windowed") != "line_packed"

        // Load tokenizer — use BPE for v3 (tokenizer.json), WordPiece for v1 (vocab.txt)
        let tokenizerJsonURL = bundlePath.appendingPathComponent("tokenizer.json")
        if fileManager.fileExists(atPath: tokenizerJsonURL.path) {
            let bpe = try BPETokenizer(tokenizerJsonURL: tokenizerJsonURL, maxLength: self.maxSeqLength)
            self.tokenizer = { words, padding, truncation in
                bpe.tokenize(words: words, padding: padding, truncation: truncation)
            }
        } else {
            let vocabURL = bundlePath.appendingPathComponent("vocab.txt")
            guard fileManager.fileExists(atPath: vocabURL.path) else {
                throw LayoutLMError.vocabNotFound(path: vocabURL.path)
            }
            let bert = try BertTokenizer(vocabURL: vocabURL)
            self.tokenizer = { words, padding, truncation in
                bert.tokenize(words: words, padding: padding, truncation: truncation)
            }
        }
    }

    // MARK: - Prediction

    /// Create pixel_values MLMultiArray from a receipt image (v3 only).
    /// Resizes to 224x224 and normalizes with mean/std = 0.5.
    private func createPixelValues(from imageData: Data) throws -> MLMultiArray? {
        guard requiresImageInput else { return nil }
        guard let imageSource = CGImageSourceCreateWithData(imageData as CFData, nil),
              let cgImage = CGImageSourceCreateImageAtIndex(imageSource, 0, nil) else {
            return createGrayPixelValues()
        }

        let size = 224
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        let bytesPerPixel = 4
        let bytesPerRow = bytesPerPixel * size
        var pixelData = [UInt8](repeating: 0, count: size * size * bytesPerPixel)

        guard let context = CGContext(
            data: &pixelData, width: size, height: size,
            bitsPerComponent: 8, bytesPerRow: bytesPerRow,
            space: colorSpace, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            return createGrayPixelValues()
        }
        context.interpolationQuality = .high
        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: size, height: size))

        let array = try MLMultiArray(shape: [1, 3, NSNumber(value: size), NSNumber(value: size)], dataType: .float32)
        for y in 0..<size {
            for x in 0..<size {
                let offset = (y * size + x) * bytesPerPixel
                let r = Float(pixelData[offset]) / 255.0
                let g = Float(pixelData[offset + 1]) / 255.0
                let b = Float(pixelData[offset + 2]) / 255.0
                // Normalize: (pixel / 255 - 0.5) / 0.5 = pixel / 127.5 - 1.0
                array[[0, 0, y, x] as [NSNumber]] = NSNumber(value: (r - 0.5) / 0.5)
                array[[0, 1, y, x] as [NSNumber]] = NSNumber(value: (g - 0.5) / 0.5)
                array[[0, 2, y, x] as [NSNumber]] = NSNumber(value: (b - 0.5) / 0.5)
            }
        }
        return array
    }

    private func createGrayPixelValues() -> MLMultiArray? {
        guard let array = try? MLMultiArray(shape: [1, 3, 224, 224], dataType: .float32) else { return nil }
        for i in 0..<array.count { array[i] = 0 }
        return array
    }

    /// Predict labels for words in OCR lines using batched inference.
    ///
    /// This optimized version packs multiple lines into single CoreML calls
    /// to reduce inference overhead. Lines are greedily packed into sequences
    /// that fit within the 512 token limit.
    ///
    /// - Parameters:
    ///   - lines: Array of OCR lines from receipt
    ///   - receiptImageData: Optional warped receipt image data (required for v3 models)
    /// - Returns: Array of LinePrediction results
    public func predict(lines: [Line], receiptImageData: Data? = nil) throws -> [LinePrediction] {
        guard !lines.isEmpty else { return [] }

        // Default: window like training (multi-line reading-order context).
        if useWindowedInference {
            return try predictWindowed(lines: lines, receiptImageData: receiptImageData)
        }

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

        // Prepare pixel_values once for all batches (v3 only, same image for all lines in a receipt)
        let pixelValues: MLMultiArray? = try {
            guard let imageData = receiptImageData else { return createGrayPixelValues() }
            return try createPixelValues(from: imageData)
        }()

        for batch in batches {
            let batchPredictions = try predictBatch(batch, pixelValues: pixelValues)
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

    /// Windowed prediction that mirrors training.
    ///
    /// Flattens every word across all OCR lines, sorts them into reading order
    /// by ascending normalized (y, x) — the EXACT key the trainer uses
    /// (data_loader._build_receipt_window_examples; note Apple Vision boxes are
    /// bottom-left origin, so this is intentionally not human top-to-bottom) —
    /// cuts `windowSize`-word windows stepped by `windowStride`, runs each
    /// window through CoreML, averages per-label probabilities for words that
    /// fall in overlapping windows, and regroups the result back into per-line
    /// `LinePrediction`s so the worker is unchanged.
    private func predictWindowed(lines: [Line], receiptImageData: Data?) throws -> [LinePrediction] {
        let empty = LinePrediction(tokens: [], labels: [], confidences: [], allProbabilities: nil)

        // Flatten words, tracking each word's (line, position-in-line).
        var allWords: [Word] = []
        var flatLine: [Int] = []
        var flatPos: [Int] = []
        for (li, line) in lines.enumerated() {
            for (pi, w) in line.words.enumerated() {
                allWords.append(w)
                flatLine.append(li)
                flatPos.append(pi)
            }
        }
        if allWords.isEmpty { return lines.map { _ in empty } }

        // Per-receipt extents → normalized boxes + texts (same as the per-line path).
        let (maxX, maxY) = BboxNormalizer.computeExtents(words: allWords)
        let (texts, nboxes) = BboxNormalizer.normalizeWords(allWords, maxX: maxX, maxY: maxY)

        // Reading order: ascending normalized y0 (box[1]) then x0 (box[0]).
        let order = Array(0..<allWords.count).sorted { a, b in
            if nboxes[a][1] != nboxes[b][1] { return nboxes[a][1] < nboxes[b][1] }
            return nboxes[a][0] < nboxes[b][0]
        }
        let n = order.count
        let oTexts = order.map { texts[$0] }
        let oBoxes = order.map { nboxes[$0] }

        // Window starts — replicate the trainer's loop exactly: step by stride,
        // stop as soon as a window reaches the end (no extra short trailing window).
        var starts: [Int] = []
        if n <= windowSize {
            starts = [0]
        } else {
            var s = 0
            while s < n {
                starts.append(s)
                if s + windowSize >= n { break }
                s += windowStride
            }
        }

        let pixelValues: MLMultiArray? = try {
            guard requiresImageInput else { return nil }
            guard let imageData = receiptImageData else { return createGrayPixelValues() }
            return try createPixelValues(from: imageData)
        }()

        // Accumulate per-(ordered-index) probability dicts across the windows
        // covering each word, then average — matches predict_receipt_windowed.
        var probSums = Array(repeating: [String: Float](), count: n)
        var counts = [Int](repeating: 0, count: n)
        for s in starts {
            let end = min(s + windowSize, n)
            let winProbs = try runWindowProbs(
                words: Array(oTexts[s..<end]),
                bboxes: Array(oBoxes[s..<end]),
                pixelValues: pixelValues
            )
            for (j, dict) in winProbs.enumerated() where !dict.isEmpty {
                let oi = s + j
                for (k, v) in dict { probSums[oi][k, default: 0] += v }
                counts[oi] += 1
            }
        }

        // Finalize each word and scatter back to its (line, position).
        var lineTokens = lines.map { [String](repeating: "", count: $0.words.count) }
        var lineLabels = lines.map { [String](repeating: "O", count: $0.words.count) }
        var lineConf = lines.map { [Float](repeating: 0, count: $0.words.count) }
        var lineProbs = lines.map { [[String: Float]](repeating: [:], count: $0.words.count) }
        for flatIdx in 0..<allWords.count {
            lineTokens[flatLine[flatIdx]][flatPos[flatIdx]] = texts[flatIdx]
        }
        for oi in 0..<n {
            let flatIdx = order[oi]
            let li = flatLine[flatIdx], pi = flatPos[flatIdx]
            if counts[oi] == 0 { continue }
            let c = Float(counts[oi])
            var bestLabel = "O"
            var bestProb: Float = -1
            var dict: [String: Float] = [:]
            for (k, v) in probSums[oi] {
                let avg = v / c
                dict[k] = avg
                if avg > bestProb { bestProb = avg; bestLabel = k }
            }
            lineLabels[li][pi] = bestLabel
            lineConf[li][pi] = bestProb < 0 ? 0 : bestProb
            lineProbs[li][pi] = dict
        }

        return lines.enumerated().map { (li, line) in
            line.words.isEmpty ? empty : LinePrediction(
                tokens: lineTokens[li],
                labels: lineLabels[li],
                confidences: lineConf[li],
                allProbabilities: lineProbs[li]
            )
        }
    }

    /// Run one window of words through CoreML and return per-word softmax
    /// probability dicts (using the first subtoken per word, matching the
    /// trainer's first-subtoken supervision). Empty dict = word dropped by the
    /// tokenizer.
    private func runWindowProbs(
        words: [String],
        bboxes: [[Int32]],
        pixelValues: MLMultiArray?
    ) throws -> [[String: Float]] {
        guard !words.isEmpty else { return [] }

        let tokenResult = tokenizer(words, true, true)
        var bboxTensor: [[Int32]] = []
        for wordId in tokenResult.wordIds {
            if let wid = wordId, wid < bboxes.count {
                bboxTensor.append(bboxes[wid])
            } else {
                bboxTensor.append([0, 0, 0, 0])
            }
        }

        let seqLength = tokenResult.inputIds.count
        let inputIds = try createMultiArray(from: tokenResult.inputIds, shape: [1, seqLength])
        let attentionMask = try createMultiArray(from: tokenResult.attentionMask, shape: [1, seqLength])
        let tokenTypeIds = try createMultiArray(from: tokenResult.tokenTypeIds, shape: [1, seqLength])
        let bbox = try createBboxMultiArray(from: bboxTensor, shape: [1, seqLength, 4])
        let inputFeatures = LayoutLMInput(
            input_ids: inputIds,
            attention_mask: attentionMask,
            bbox: bbox,
            token_type_ids: tokenTypeIds,
            pixel_values: pixelValues
        )
        let output = try model.prediction(from: inputFeatures)
        guard let logits = output.featureValue(for: "logits")?.multiArrayValue else {
            throw LayoutLMError.outputNotFound
        }
        let numLabels = logits.shape[2].intValue

        var wordToFirstToken: [Int: Int] = [:]
        for (tokenIdx, wordId) in tokenResult.wordIds.enumerated() {
            if let wid = wordId, wordToFirstToken[wid] == nil {
                wordToFirstToken[wid] = tokenIdx
            }
        }

        var result: [[String: Float]] = []
        for wordIdx in 0..<words.count {
            guard let tokenIdx = wordToFirstToken[wordIdx] else {
                result.append([:])
                continue
            }
            var wordLogits = [Float](repeating: 0, count: numLabels)
            for labelIdx in 0..<numLabels {
                wordLogits[labelIdx] = logits[tokenIdx * numLabels + labelIdx].floatValue
            }
            let probs = softmax(wordLogits)
            var dict: [String: Float] = [:]
            for labelIdx in 0..<numLabels {
                let p = probs[labelIdx]
                dict[config.labelName(for: labelIdx)] = p.isNaN ? 0 : p
            }
            result.append(dict)
        }
        return result
    }

    /// Count tokens for a list of words (without special tokens or padding).
    private func countTokens(words: [String]) -> Int {
        var count = 0
        for word in words {
            // Use tokenizer to count subtokens
            let result = tokenizer([word], false, false)
            // Subtract 2 for [CLS] and [SEP] that tokenize() adds
            count += max(0, result.inputIds.count - 2)
        }
        return count
    }

    /// Predict labels for a batch of lines packed into a single sequence.
    private func predictBatch(
        _ batch: [(lineIndex: Int, words: [String], bboxes: [[Int32]])],
        pixelValues: MLMultiArray? = nil
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
        let tokenResult = tokenizer(allWords, true, true)

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
            token_type_ids: tokenTypeIds,
            pixel_values: pixelValues
        )
        let output = try model.prediction(from: inputFeatures)

        guard let logitsArray = output.featureValue(for: "logits")?.multiArrayValue else {
            throw LayoutLMError.outputNotFound
        }

        // Split predictions by line
        var predictions: [LinePrediction] = []

        for (batchIdx, _) in batch.enumerated() {
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
    ///
    /// Uses ONLY the first subtoken's logits per word to match training-time
    /// supervision (trainer.py labels first subtoken, sets rest to -100).
    /// Averaging across subtokens — what this code used to do — mixed in
    /// logits the model never optimized for, which broke BIO continuity:
    /// a word's first occurrence could land as `I-MERCHANT_NAME` with no
    /// preceding `B-` because the averaged distribution drifted off the
    /// first-subtoken decision boundary.
    private func aggregatePredictionsForLine(
        logits: MLMultiArray,
        wordIds: [Int?],
        targetWordIds: Set<Int>,
        words: [String],
        wordIdOffset: Int
    ) -> LinePrediction {
        let numLabels = logits.shape[2].intValue
        let numWords = words.count

        // First subtoken index per word (only for words in this line).
        var wordToFirstToken: [Int: Int] = [:]
        for (tokenIdx, wordId) in wordIds.enumerated() {
            if let wid = wordId, targetWordIds.contains(wid) {
                let localWordId = wid - wordIdOffset
                if wordToFirstToken[localWordId] == nil {
                    wordToFirstToken[localWordId] = tokenIdx
                }
            }
        }

        var labels: [String] = []
        var confidences: [Float] = []
        var allProbs: [[String: Float]] = []

        for wordIdx in 0..<numWords {
            guard let tokenIdx = wordToFirstToken[wordIdx] else {
                labels.append("O")
                confidences.append(0.0)
                allProbs.append([:])
                continue
            }

            var wordLogits = [Float](repeating: 0, count: numLabels)
            for labelIdx in 0..<numLabels {
                let index = tokenIdx * numLabels + labelIdx
                wordLogits[labelIdx] = logits[index].floatValue
            }

            let probs = softmax(wordLogits)
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
        let tokenResult = tokenizer(texts, true, true)

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

        // First subtoken index per word (matches training-time supervision —
        // trainer.py labels first subtoken only, sets the rest to -100).
        var wordToFirstToken: [Int: Int] = [:]
        for (tokenIdx, wordId) in wordIds.enumerated() {
            if let wid = wordId, wordToFirstToken[wid] == nil {
                wordToFirstToken[wid] = tokenIdx
            }
        }

        var labels: [String] = []
        var confidences: [Float] = []
        var allProbs: [[String: Float]] = []

        for wordIdx in 0..<numWords {
            guard let tokenIdx = wordToFirstToken[wordIdx] else {
                // Word was dropped during tokenization
                labels.append("O")
                confidences.append(0.0)
                allProbs.append([:])
                continue
            }

            var wordLogits = [Float](repeating: 0, count: numLabels)
            for labelIdx in 0..<numLabels {
                let index = tokenIdx * numLabels + labelIdx
                wordLogits[labelIdx] = logits[index].floatValue
            }

            let probs = softmax(wordLogits)
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

/// Feature provider for LayoutLM model inputs (v1 and v3).
private class LayoutLMInput: MLFeatureProvider {
    let input_ids: MLMultiArray
    let attention_mask: MLMultiArray
    let bbox: MLMultiArray
    let token_type_ids: MLMultiArray
    let pixel_values: MLMultiArray?

    init(input_ids: MLMultiArray, attention_mask: MLMultiArray, bbox: MLMultiArray, token_type_ids: MLMultiArray, pixel_values: MLMultiArray? = nil) {
        self.input_ids = input_ids
        self.attention_mask = attention_mask
        self.bbox = bbox
        self.token_type_ids = token_type_ids
        self.pixel_values = pixel_values
    }

    var featureNames: Set<String> {
        var names: Set<String> = ["input_ids", "attention_mask", "bbox", "token_type_ids"]
        if pixel_values != nil { names.insert("pixel_values") }
        return names
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
        case "pixel_values":
            return pixel_values.map { MLFeatureValue(multiArray: $0) }
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
