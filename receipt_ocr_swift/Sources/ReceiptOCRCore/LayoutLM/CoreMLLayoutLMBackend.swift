import Foundation

#if os(macOS)
import CoreML

/// Existing Core ML LayoutLM execution backend (default).
public final class CoreMLLayoutLMBackend: LayoutLMBackend {
    private let model: MLModel
    public let requiresImageInput: Bool

    public init(bundlePath: URL) throws {
        let fileManager = FileManager.default
        let contents = try fileManager.contentsOfDirectory(
            at: bundlePath,
            includingPropertiesForKeys: nil
        )
        guard let modelURL = contents.first(where: { $0.pathExtension == "mlpackage" }) else {
            throw LayoutLMError.modelNotFound(path: bundlePath.path)
        }

        let compiledName =
            modelURL.deletingPathExtension().lastPathComponent + ".mlmodelc"
        let persistentCompiledURL = bundlePath.appendingPathComponent(compiledName)

        if fileManager.fileExists(atPath: persistentCompiledURL.path) {
            self.model = try MLModel(contentsOf: persistentCompiledURL)
        } else {
            let tempCompiledURL = try MLModel.compileModel(at: modelURL)
            do {
                try fileManager.moveItem(at: tempCompiledURL, to: persistentCompiledURL)
            } catch {
                try? fileManager.copyItem(at: tempCompiledURL, to: persistentCompiledURL)
                try? fileManager.removeItem(at: tempCompiledURL)
            }
            self.model = try MLModel(contentsOf: persistentCompiledURL)
        }

        self.requiresImageInput = model.modelDescription.inputDescriptionsByName
            .keys.contains("pixel_values")
    }

    public func predictLogits(_ inputs: LayoutLMForwardInputs) throws -> LayoutLMLogits {
        let seqLength = inputs.inputIds.count
        let inputIds = try Self.createMultiArray(
            from: inputs.inputIds.map { Int($0) },
            shape: [1, seqLength]
        )
        let attentionMask = try Self.createMultiArray(
            from: inputs.attentionMask.map { Int($0) },
            shape: [1, seqLength]
        )
        let tokenTypeIds = try Self.createMultiArray(
            from: inputs.tokenTypeIds.map { Int($0) },
            shape: [1, seqLength]
        )
        let bbox = try Self.createBboxMultiArray(
            from: inputs.bbox,
            shape: [1, seqLength, 4]
        )
        let pixelValues = try inputs.pixelValuesCHW.map {
            try Self.createPixelValuesMultiArray(from: $0)
        }

        let featureProvider = CoreMLLayoutLMInput(
            input_ids: inputIds,
            attention_mask: attentionMask,
            bbox: bbox,
            token_type_ids: tokenTypeIds,
            pixel_values: pixelValues
        )
        let output = try model.prediction(from: featureProvider)
        guard let logitsArray = output.featureValue(for: "logits")?.multiArrayValue else {
            throw LayoutLMError.outputNotFound
        }

        let numLabels = logitsArray.shape[2].intValue
        var values = [Float](repeating: 0, count: seqLength * numLabels)
        for tokenIdx in 0..<seqLength {
            for labelIdx in 0..<numLabels {
                values[tokenIdx * numLabels + labelIdx] =
                    logitsArray[tokenIdx * numLabels + labelIdx].floatValue
            }
        }
        return LayoutLMLogits(
            values: values,
            sequenceLength: seqLength,
            numLabels: numLabels
        )
    }

    private static func createMultiArray(from array: [Int], shape: [Int]) throws -> MLMultiArray {
        let expectedCount = shape.reduce(1, *)
        guard expectedCount == array.count else {
            throw LayoutLMError.predictionFailed(
                "Shape \(shape) expects \(expectedCount) elements, got \(array.count)"
            )
        }
        let mlArray = try MLMultiArray(
            shape: shape.map { NSNumber(value: $0) },
            dataType: .int32
        )
        for (idx, value) in array.enumerated() {
            mlArray[idx] = NSNumber(value: Int32(value))
        }
        return mlArray
    }

    private static func createBboxMultiArray(
        from bboxes: [[Int32]],
        shape: [Int]
    ) throws -> MLMultiArray {
        guard shape.count == 3, shape[0] == 1, shape[1] == bboxes.count, shape[2] == 4 else {
            throw LayoutLMError.predictionFailed(
                "Invalid bbox shape: \(shape) for \(bboxes.count) bboxes"
            )
        }
        let mlArray = try MLMultiArray(
            shape: shape.map { NSNumber(value: $0) },
            dataType: .int32
        )
        for (seqIdx, bbox) in bboxes.enumerated() {
            guard bbox.count == 4 else {
                throw LayoutLMError.predictionFailed(
                    "Bbox must have 4 coordinates, got \(bbox.count)"
                )
            }
            for (coordIdx, value) in bbox.enumerated() {
                mlArray[seqIdx * 4 + coordIdx] = NSNumber(value: value)
            }
        }
        return mlArray
    }

    private static func createPixelValuesMultiArray(from chw: [Float]) throws -> MLMultiArray {
        let expected = 3 * 224 * 224
        guard chw.count == expected else {
            throw LayoutLMError.predictionFailed(
                "pixel_values expects \(expected) floats, got \(chw.count)"
            )
        }
        let array = try MLMultiArray(
            shape: [1, 3, 224, 224],
            dataType: .float32
        )
        for i in 0..<chw.count {
            array[i] = NSNumber(value: chw[i])
        }
        return array
    }
}

/// Feature provider for LayoutLM Core ML inputs (v1 and v3).
private final class CoreMLLayoutLMInput: MLFeatureProvider {
    let input_ids: MLMultiArray
    let attention_mask: MLMultiArray
    let bbox: MLMultiArray
    let token_type_ids: MLMultiArray
    let pixel_values: MLMultiArray?

    init(
        input_ids: MLMultiArray,
        attention_mask: MLMultiArray,
        bbox: MLMultiArray,
        token_type_ids: MLMultiArray,
        pixel_values: MLMultiArray? = nil
    ) {
        self.input_ids = input_ids
        self.attention_mask = attention_mask
        self.bbox = bbox
        self.token_type_ids = token_type_ids
        self.pixel_values = pixel_values
    }

    var featureNames: Set<String> {
        var names: Set<String> = [
            "input_ids", "attention_mask", "bbox", "token_type_ids",
        ]
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

#endif
