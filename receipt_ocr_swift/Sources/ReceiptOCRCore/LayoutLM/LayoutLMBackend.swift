import Foundation

#if os(macOS)

/// Flat logits returned by a LayoutLM execution backend.
public struct LayoutLMLogits: Sendable {
    /// Row-major `[sequenceLength * numLabels]` logits.
    public let values: [Float]
    public let sequenceLength: Int
    public let numLabels: Int

    public init(values: [Float], sequenceLength: Int, numLabels: Int) {
        self.values = values
        self.sequenceLength = sequenceLength
        self.numLabels = numLabels
    }

    public func logit(token: Int, label: Int) -> Float {
        values[token * numLabels + label]
    }
}

/// Backend-agnostic inputs for one LayoutLM forward pass.
public struct LayoutLMForwardInputs: Sendable {
    public let inputIds: [Int32]
    public let attentionMask: [Int32]
    public let tokenTypeIds: [Int32]
    /// Per-token boxes as `[x0, y0, x1, y1]` in LayoutLM `[0, 1000]` space.
    public let bbox: [[Int32]]
    /// Optional CHW float32 image (`3 * 224 * 224`) for LayoutLMv3.
    public let pixelValuesCHW: [Float]?

    public init(
        inputIds: [Int32],
        attentionMask: [Int32],
        tokenTypeIds: [Int32],
        bbox: [[Int32]],
        pixelValuesCHW: [Float]? = nil
    ) {
        self.inputIds = inputIds
        self.attentionMask = attentionMask
        self.tokenTypeIds = tokenTypeIds
        self.bbox = bbox
        self.pixelValuesCHW = pixelValuesCHW
    }
}

/// Forward-only LayoutLM model execution. Tokenization / windowing stay in
/// ``LayoutLMInference``.
public protocol LayoutLMBackend: AnyObject {
    var requiresImageInput: Bool { get }
    func predictLogits(_ inputs: LayoutLMForwardInputs) throws -> LayoutLMLogits
}

#endif
