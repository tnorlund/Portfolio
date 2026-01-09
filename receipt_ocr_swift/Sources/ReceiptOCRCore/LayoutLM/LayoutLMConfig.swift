import Foundation

#if os(macOS)

/// Configuration for LayoutLM model loaded from config.json.
public struct LayoutLMConfig: Codable {

    /// Mapping from label ID (as string) to label name
    /// e.g., {"0": "O", "1": "B-MERCHANT_NAME", ...}
    public let id2label: [String: String]

    /// Mapping from label name to label ID
    /// e.g., {"O": 0, "B-MERCHANT_NAME": 1, ...}
    public let label2id: [String: Int]

    /// Number of labels the model was trained on
    public let numLabels: Int

    /// Maximum position embeddings (typically 512 for LayoutLM)
    public let maxPositionEmbeddings: Int?

    /// Vocabulary size
    public let vocabSize: Int?

    private enum CodingKeys: String, CodingKey {
        case id2label
        case label2id
        case numLabels = "num_labels"
        case maxPositionEmbeddings = "max_position_embeddings"
        case vocabSize = "vocab_size"
    }

    /// Initialize from a config.json file.
    ///
    /// - Parameter url: URL to config.json file
    /// - Returns: Parsed configuration
    public static func load(from url: URL) throws -> LayoutLMConfig {
        let data = try Data(contentsOf: url)
        let decoder = JSONDecoder()
        return try decoder.decode(LayoutLMConfig.self, from: data)
    }

    /// Get label name for a given ID.
    ///
    /// - Parameter id: Label ID (integer)
    /// - Returns: Label name or "O" if not found
    public func labelName(for id: Int) -> String {
        return id2label[String(id)] ?? "O"
    }

    /// Get label ID for a given name.
    ///
    /// - Parameter name: Label name (e.g., "B-MERCHANT_NAME")
    /// - Returns: Label ID or nil if not found
    public func labelId(for name: String) -> Int? {
        return label2id[name]
    }

    /// Get list of all label names in order.
    public var labels: [String] {
        return (0..<numLabels).map { labelName(for: $0) }
    }

    /// Get list of unique entity types (without B-/I- prefixes).
    public var entityTypes: [String] {
        var types = Set<String>()
        for label in labels {
            if label == "O" {
                continue
            }
            // Remove B- or I- prefix
            let entityType: String
            if label.hasPrefix("B-") || label.hasPrefix("I-") {
                entityType = String(label.dropFirst(2))
            } else {
                entityType = label
            }
            types.insert(entityType)
        }
        return types.sorted()
    }
}

/// Separate label map structure for label_map.json.
public struct LabelMap: Codable {

    /// Mapping from label ID (as string) to label name
    public let id2label: [String: String]

    /// Mapping from label name to label ID
    public let label2id: [String: Int]

    /// Ordered list of label names
    public let labels: [String]

    /// Initialize from a label_map.json file.
    public static func load(from url: URL) throws -> LabelMap {
        let data = try Data(contentsOf: url)
        let decoder = JSONDecoder()
        return try decoder.decode(LabelMap.self, from: data)
    }
}

#endif
