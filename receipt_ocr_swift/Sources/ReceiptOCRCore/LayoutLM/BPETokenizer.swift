import Foundation

#if os(macOS)

/// BPE (Byte-Pair Encoding) tokenizer for LayoutLMv3 / RoBERTa models.
/// Reads from HuggingFace tokenizer.json format.
public class BPETokenizer {

    private let vocab: [String: Int]
    private let inverseVocab: [Int: String]
    private let mergeRanks: [String: Int]
    private let unkTokenId: Int

    public let clsTokenId: Int
    public let sepTokenId: Int
    public let padTokenId: Int
    public let vocabSize: Int
    public let maxLength: Int

    public init(tokenizerJsonURL: URL, maxLength: Int = 512) throws {
        let data = try Data(contentsOf: tokenizerJsonURL)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        let model = json["model"] as! [String: Any]
        let vocabDict = model["vocab"] as! [String: Int]
        self.vocab = vocabDict
        self.vocabSize = vocabDict.count

        var inverse: [Int: String] = [:]
        for (token, id) in vocabDict {
            inverse[id] = token
        }
        self.inverseVocab = inverse

        let mergesList = model["merges"] as! [[String]]
        var ranks: [String: Int] = [:]
        for (i, pair) in mergesList.enumerated() {
            ranks["\(pair[0]) \(pair[1])"] = i
        }
        self.mergeRanks = ranks

        self.clsTokenId = vocabDict["<s>"] ?? 0
        self.sepTokenId = vocabDict["</s>"] ?? 2
        self.padTokenId = vocabDict["<pad>"] ?? 1
        self.unkTokenId = vocabDict["<unk>"] ?? 3
        self.maxLength = maxLength
    }

    /// Tokenize pre-split words into BertTokenizer.TokenizationResult format.
    public func tokenize(
        words: [String],
        padding: Bool = true,
        truncation: Bool = true
    ) -> BertTokenizer.TokenizationResult {
        var allTokenIds: [Int] = [clsTokenId]
        var allWordIds: [Int?] = [nil]

        for (wordIdx, word) in words.enumerated() {
            let tokens = bpeEncode(word: word, isFirst: wordIdx == 0)
            let ids = tokens.map { vocab[$0] ?? unkTokenId }

            if truncation && allTokenIds.count + ids.count >= maxLength - 1 {
                let remaining = maxLength - 1 - allTokenIds.count
                if remaining > 0 {
                    allTokenIds.append(contentsOf: ids.prefix(remaining))
                    allWordIds.append(contentsOf: Array(repeating: wordIdx as Int?, count: remaining))
                }
                break
            }

            allTokenIds.append(contentsOf: ids)
            allWordIds.append(contentsOf: Array(repeating: wordIdx as Int?, count: ids.count))
        }

        allTokenIds.append(sepTokenId)
        allWordIds.append(nil)

        let realCount = allTokenIds.count
        if padding {
            let padCount = max(0, maxLength - allTokenIds.count)
            allTokenIds.append(contentsOf: Array(repeating: padTokenId, count: padCount))
            allWordIds.append(contentsOf: Array(repeating: nil as Int?, count: padCount))
        }

        let attentionMask = (0..<allTokenIds.count).map { $0 < realCount ? 1 : 0 }
        let tokenTypeIds = Array(repeating: 0, count: allTokenIds.count)

        return BertTokenizer.TokenizationResult(
            inputIds: allTokenIds,
            attentionMask: attentionMask,
            tokenTypeIds: tokenTypeIds,
            wordIds: allWordIds,
            words: words
        )
    }

    private func bpeEncode(word: String, isFirst: Bool) -> [String] {
        let byteTokens = byteLevelEncode(word: word, isFirst: isFirst)
        if byteTokens.count <= 1 {
            return byteTokens
        }

        var symbols = byteTokens
        while symbols.count > 1 {
            var bestRank = Int.max
            var bestIdx = -1
            for i in 0..<(symbols.count - 1) {
                let key = "\(symbols[i]) \(symbols[i + 1])"
                if let rank = mergeRanks[key], rank < bestRank {
                    bestRank = rank
                    bestIdx = i
                }
            }
            if bestIdx < 0 { break }

            let merged = symbols[bestIdx] + symbols[bestIdx + 1]
            symbols[bestIdx] = merged
            symbols.remove(at: bestIdx + 1)
        }

        return symbols
    }

    private func byteLevelEncode(word: String, isFirst: Bool) -> [String] {
        var text = word
        if !isFirst {
            text = "\u{0120}" + text
        }
        return text.map { String($0) }
    }
}

#endif
