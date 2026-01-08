import Foundation

#if os(macOS)

/// WordPiece tokenizer for BERT/LayoutLM models.
///
/// This tokenizer matches the behavior of HuggingFace's LayoutLMTokenizerFast
/// and tracks word IDs for subtoken aggregation during inference.
public struct BertTokenizer {

    /// Special tokens
    public static let clsToken = "[CLS]"
    public static let sepToken = "[SEP]"
    public static let padToken = "[PAD]"
    public static let unkToken = "[UNK]"
    public static let maskToken = "[MASK]"

    /// Token to ID mapping
    private let vocab: [String: Int]

    /// ID to token mapping
    private let invVocab: [Int: String]

    /// Special token IDs
    public let clsTokenId: Int
    public let sepTokenId: Int
    public let padTokenId: Int
    public let unkTokenId: Int

    /// Maximum sequence length
    public let maxLength: Int

    /// Whether to lowercase text
    public let doLowerCase: Bool

    // MARK: - Initialization

    /// Initialize from a vocab.txt file.
    ///
    /// - Parameters:
    ///   - vocabURL: URL to vocab.txt file
    ///   - maxLength: Maximum sequence length (default 512)
    ///   - doLowerCase: Whether to lowercase text (default true for uncased models)
    public init(vocabURL: URL, maxLength: Int = 512, doLowerCase: Bool = true) throws {
        let content = try String(contentsOf: vocabURL, encoding: .utf8)
        let tokens = content.components(separatedBy: .newlines).filter { !$0.isEmpty }

        var vocab: [String: Int] = [:]
        var invVocab: [Int: String] = [:]

        for (idx, token) in tokens.enumerated() {
            vocab[token] = idx
            invVocab[idx] = token
        }

        self.vocab = vocab
        self.invVocab = invVocab
        self.maxLength = maxLength
        self.doLowerCase = doLowerCase

        guard let cls = vocab[Self.clsToken],
              let sep = vocab[Self.sepToken],
              let pad = vocab[Self.padToken],
              let unk = vocab[Self.unkToken] else {
            throw TokenizerError.missingSpecialTokens
        }

        self.clsTokenId = cls
        self.sepTokenId = sep
        self.padTokenId = pad
        self.unkTokenId = unk
    }

    // MARK: - Tokenization

    /// Result of tokenization including word ID tracking.
    public struct TokenizationResult {
        /// Token IDs
        public let inputIds: [Int]

        /// Attention mask (1 for real tokens, 0 for padding)
        public let attentionMask: [Int]

        /// Token type IDs (all 0 for single sequence)
        public let tokenTypeIds: [Int]

        /// Word ID for each token (nil for special tokens like [CLS], [SEP], [PAD])
        /// Used for aggregating subtoken predictions back to words.
        public let wordIds: [Int?]

        /// Original words that were tokenized
        public let words: [String]
    }

    /// Tokenize a list of words (pre-tokenized input).
    ///
    /// This matches HuggingFace's `is_split_into_words=True` mode where input
    /// is already split into words. Each word may be split into subword tokens.
    ///
    /// - Parameters:
    ///   - words: List of words to tokenize
    ///   - padding: Whether to pad to maxLength
    ///   - truncation: Whether to truncate to maxLength
    /// - Returns: TokenizationResult with IDs and word mapping
    public func tokenize(
        words: [String],
        padding: Bool = true,
        truncation: Bool = true
    ) -> TokenizationResult {
        var allTokenIds: [Int] = [clsTokenId]
        var allWordIds: [Int?] = [nil]  // [CLS] has no word ID

        for (wordIdx, word) in words.enumerated() {
            let wordTokenIds = tokenizeWord(word)

            // Check if adding this word would exceed max length (accounting for [SEP])
            if truncation && allTokenIds.count + wordTokenIds.count + 1 > maxLength {
                break
            }

            allTokenIds.append(contentsOf: wordTokenIds)
            allWordIds.append(contentsOf: Array(repeating: wordIdx, count: wordTokenIds.count))
        }

        // Add [SEP]
        allTokenIds.append(sepTokenId)
        allWordIds.append(nil)

        // Create attention mask
        var attentionMask = Array(repeating: 1, count: allTokenIds.count)

        // Pad if needed
        if padding && allTokenIds.count < maxLength {
            let paddingCount = maxLength - allTokenIds.count
            allTokenIds.append(contentsOf: Array(repeating: padTokenId, count: paddingCount))
            allWordIds.append(contentsOf: Array(repeating: nil as Int?, count: paddingCount))
            attentionMask.append(contentsOf: Array(repeating: 0, count: paddingCount))
        }

        // Token type IDs are all 0 for single sequence
        let tokenTypeIds = Array(repeating: 0, count: allTokenIds.count)

        return TokenizationResult(
            inputIds: allTokenIds,
            attentionMask: attentionMask,
            tokenTypeIds: tokenTypeIds,
            wordIds: allWordIds,
            words: words
        )
    }

    /// Tokenize a single word using WordPiece algorithm.
    private func tokenizeWord(_ word: String) -> [Int] {
        let text = doLowerCase ? word.lowercased() : word

        // Clean and normalize
        let cleanedText = cleanText(text)
        guard !cleanedText.isEmpty else {
            return [unkTokenId]
        }

        // Split on punctuation
        let tokens = tokenizeOnPunctuation(cleanedText)

        var result: [Int] = []

        for token in tokens {
            let wordPieceIds = wordPieceTokenize(token)
            result.append(contentsOf: wordPieceIds)
        }

        return result.isEmpty ? [unkTokenId] : result
    }

    /// Apply WordPiece tokenization to a single token.
    private func wordPieceTokenize(_ token: String) -> [Int] {
        guard !token.isEmpty else { return [] }

        var tokenIds: [Int] = []
        var start = token.startIndex

        while start < token.endIndex {
            var end = token.endIndex
            var foundMatch = false

            while start < end {
                let substr: String
                if start == token.startIndex {
                    substr = String(token[start..<end])
                } else {
                    substr = "##" + String(token[start..<end])
                }

                if let tokenId = vocab[substr] {
                    tokenIds.append(tokenId)
                    foundMatch = true
                    start = end
                    break
                }

                // Try shorter substring
                end = token.index(before: end)
            }

            if !foundMatch {
                // Character not in vocab, use [UNK]
                tokenIds.append(unkTokenId)
                start = token.index(after: start)
            }
        }

        return tokenIds
    }

    /// Clean text by removing control characters and normalizing whitespace.
    private func cleanText(_ text: String) -> String {
        var result = ""
        for scalar in text.unicodeScalars {
            // Skip control characters (except whitespace)
            if scalar.value == 0 || scalar.value == 0xfffd {
                continue
            }
            if CharacterSet.controlCharacters.contains(scalar) && !CharacterSet.whitespaces.contains(scalar) {
                continue
            }
            // Normalize whitespace to space
            if CharacterSet.whitespaces.contains(scalar) {
                result.append(" ")
            } else {
                result.append(Character(scalar))
            }
        }
        return result.trimmingCharacters(in: .whitespaces)
    }

    /// Split on punctuation, keeping punctuation as separate tokens.
    private func tokenizeOnPunctuation(_ text: String) -> [String] {
        var tokens: [String] = []
        var currentToken = ""

        for char in text {
            if isPunctuation(char) {
                if !currentToken.isEmpty {
                    tokens.append(currentToken)
                    currentToken = ""
                }
                tokens.append(String(char))
            } else if char.isWhitespace {
                if !currentToken.isEmpty {
                    tokens.append(currentToken)
                    currentToken = ""
                }
            } else {
                currentToken.append(char)
            }
        }

        if !currentToken.isEmpty {
            tokens.append(currentToken)
        }

        return tokens
    }

    /// Check if character is punctuation.
    private func isPunctuation(_ char: Character) -> Bool {
        guard let scalar = char.unicodeScalars.first else { return false }

        // ASCII punctuation
        if (scalar.value >= 33 && scalar.value <= 47) ||
           (scalar.value >= 58 && scalar.value <= 64) ||
           (scalar.value >= 91 && scalar.value <= 96) ||
           (scalar.value >= 123 && scalar.value <= 126) {
            return true
        }

        // Unicode punctuation category
        return CharacterSet.punctuationCharacters.contains(scalar)
    }

    // MARK: - Decoding

    /// Convert token IDs back to tokens.
    public func decode(tokenIds: [Int]) -> [String] {
        return tokenIds.compactMap { invVocab[$0] }
    }

    /// Convert token IDs to a string, removing special tokens and ## prefixes.
    public func decodeToString(tokenIds: [Int]) -> String {
        let tokens = decode(tokenIds: tokenIds)
        var result = ""

        for token in tokens {
            // Skip special tokens
            if token == Self.clsToken || token == Self.sepToken ||
               token == Self.padToken || token == Self.unkToken {
                continue
            }

            if token.hasPrefix("##") {
                result.append(String(token.dropFirst(2)))
            } else {
                if !result.isEmpty {
                    result.append(" ")
                }
                result.append(token)
            }
        }

        return result
    }

    /// Get vocab size
    public var vocabSize: Int {
        return vocab.count
    }
}

// MARK: - Errors

public enum TokenizerError: Error, LocalizedError {
    case missingSpecialTokens
    case vocabFileNotFound

    public var errorDescription: String? {
        switch self {
        case .missingSpecialTokens:
            return "Vocabulary file is missing required special tokens ([CLS], [SEP], [PAD], [UNK])"
        case .vocabFileNotFound:
            return "Vocabulary file not found"
        }
    }
}

#endif
