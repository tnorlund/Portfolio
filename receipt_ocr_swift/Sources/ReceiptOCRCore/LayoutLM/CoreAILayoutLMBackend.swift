import Foundation

#if os(macOS)

/// Opt-in Core AI LayoutLM backend for macOS 27+ / Xcode 27.
///
/// Uses Apple's Core AI Swift API (`AIModel`, `InferenceFunction`, `NDArray`).
/// When the CoreAI framework is absent from the SDK, initialization fails with
/// ``LayoutLMError/backendUnavailable(_:)`` so Core ML builds keep working.
public final class CoreAILayoutLMBackend: LayoutLMBackend {
    public let requiresImageInput: Bool = false

    #if canImport(CoreAI)
    private let inferenceFunction: InferenceFunction
    #endif

    public init(bundlePath: URL) throws {
        let fileManager = FileManager.default
        let contents = try fileManager.contentsOfDirectory(
            at: bundlePath,
            includingPropertiesForKeys: nil
        )
        guard let aimodelURL = contents.first(where: { $0.pathExtension == "aimodel" }) else {
            throw LayoutLMError.modelNotFound(path: bundlePath.path)
        }

        #if canImport(CoreAI)
        if #available(macOS 27.0, *) {
            self.inferenceFunction = try Self.loadFunctionSync(at: aimodelURL)
            return
        }
        #endif

        throw LayoutLMError.backendUnavailable(
            "Core AI backend requires macOS 27+ with the CoreAI framework "
                + "(Xcode 27). Found \(aimodelURL.lastPathComponent) but this "
                + "SDK/OS cannot run it. Use LAYOUTLM_BACKEND=coreml, or build "
                + "with Xcode 27 on macOS 27."
        )
    }

    public func predictLogits(_ inputs: LayoutLMForwardInputs) throws -> LayoutLMLogits {
        #if canImport(CoreAI)
        if #available(macOS 27.0, *) {
            return try Self.runSync(function: inferenceFunction, inputs: inputs)
        }
        #endif
        throw LayoutLMError.backendUnavailable(
            "Core AI runtime is not available on this OS/SDK"
        )
    }

    #if canImport(CoreAI)
    @available(macOS 27.0, *)
    private static func loadFunctionSync(at url: URL) throws -> InferenceFunction {
        try runBlocking {
            let model = try await AIModel(contentsOf: url)
            guard let function = try model.loadFunction(named: "main") else {
                throw LayoutLMError.predictionFailed(
                    "Core AI model at \(url.path) has no 'main' function"
                )
            }
            return function
        }
    }

    @available(macOS 27.0, *)
    private static func runSync(
        function: InferenceFunction,
        inputs: LayoutLMForwardInputs
    ) throws -> LayoutLMLogits {
        try runBlocking {
            try await run(function: function, inputs: inputs)
        }
    }

    @available(macOS 27.0, *)
    private static func run(
        function: InferenceFunction,
        inputs: LayoutLMForwardInputs
    ) async throws -> LayoutLMLogits {
        let seqLength = inputs.inputIds.count
        guard inputs.attentionMask.count == seqLength,
              inputs.tokenTypeIds.count == seqLength,
              inputs.bbox.count == seqLength
        else {
            throw LayoutLMError.predictionFailed("Core AI input length mismatch")
        }

        // Prefer int32 tensors for token/bbox ids when the SDK exposes them;
        // fall back to float32 filled from the integer values.
        let inputIds = try makeIntArray(
            shape: [1, seqLength],
            values: inputs.inputIds
        )
        let attentionMask = try makeIntArray(
            shape: [1, seqLength],
            values: inputs.attentionMask
        )
        let tokenTypeIds = try makeIntArray(
            shape: [1, seqLength],
            values: inputs.tokenTypeIds
        )
        var flatBbox: [Int32] = []
        flatBbox.reserveCapacity(seqLength * 4)
        for box in inputs.bbox {
            guard box.count == 4 else {
                throw LayoutLMError.predictionFailed("bbox must have 4 coords")
            }
            flatBbox.append(contentsOf: box)
        }
        let bbox = try makeIntArray(shape: [1, seqLength, 4], values: flatBbox)

        var outputs = try await function.run(inputs: [
            "input_ids": inputIds,
            "attention_mask": attentionMask,
            "bbox": bbox,
            "token_type_ids": tokenTypeIds,
        ])

        guard let logitsValue = outputs.remove("logits"),
              let logitsND = logitsValue.ndArray
        else {
            throw LayoutLMError.outputNotFound
        }

        let flat = try readFloats(from: logitsND)
        guard flat.count % seqLength == 0 else {
            throw LayoutLMError.predictionFailed(
                "Core AI logits count \(flat.count) not divisible by seq \(seqLength)"
            )
        }
        return LayoutLMLogits(
            values: flat,
            sequenceLength: seqLength,
            numLabels: flat.count / seqLength
        )
    }

    @available(macOS 27.0, *)
    private static func makeIntArray(shape: [Int], values: [Int32]) throws -> NDArray {
        let expected = shape.reduce(1, *)
        guard expected == values.count else {
            throw LayoutLMError.predictionFailed(
                "NDArray shape \(shape) expects \(expected) values, got \(values.count)"
            )
        }
        // Integer scalar types when available; otherwise float32 carrying ints.
        var array = NDArray(shape: shape, scalarType: .int32)
        var view = array.mutableView(as: Int32.self)
        for (i, v) in values.enumerated() {
            view[i] = v
        }
        return array
    }

    @available(macOS 27.0, *)
    private static func readFloats(from array: NDArray) throws -> [Float] {
        let view = array.view(as: Float.self)
        var out = [Float](repeating: 0, count: view.count)
        for i in 0..<view.count {
            out[i] = view[i]
        }
        return out
    }

    /// Bridge Core AI's async API into the existing sync inference path.
    private static func runBlocking<T: Sendable>(
        _ work: @escaping @Sendable () async throws -> T
    ) throws -> T {
        let semaphore = DispatchSemaphore(value: 0)
        var result: Result<T, Error>?
        Task {
            do {
                result = .success(try await work())
            } catch {
                result = .failure(error)
            }
            semaphore.signal()
        }
        semaphore.wait()
        switch result {
        case .success(let value):
            return value
        case .failure(let error):
            throw error
        case .none:
            throw LayoutLMError.predictionFailed("Core AI async bridge produced no result")
        }
    }
    #endif
}

#endif
