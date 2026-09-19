import Foundation

#if os(macOS)
#if RECEIPT_OCR_COREAI && canImport(CoreAI)
import CoreAI
#endif

/// Opt-in Core AI LayoutLM backend for macOS 27+ / Xcode 27.
///
/// Uses Apple's Core AI Swift API (`AIModel`, `InferenceFunction`, `NDArray`
/// from the `CoreAIRuntime` module that `CoreAI` re-exports). The runtime code
/// compiles only when the package is built with `RECEIPT_OCR_COREAI=1` (see
/// `Package.swift`): the API is still beta and CI's macOS runner has no
/// macOS 27 SDK, so the default build must not depend on it. Without the flag,
/// or on an older OS, initialization fails with
/// ``LayoutLMError/backendUnavailable(_:)`` so Core ML builds keep working.
public final class CoreAILayoutLMBackend: LayoutLMBackend {
    public let requiresImageInput: Bool = false

    /// Loaded Core AI function, boxed so this class stays available on the
    /// package's macOS 13 deployment target. Always a ``Runtime`` on macOS 27+.
    private let runtime: AnyObject?

    public init(bundlePath: URL) throws {
        let fileManager = FileManager.default
        let contents = try fileManager.contentsOfDirectory(
            at: bundlePath,
            includingPropertiesForKeys: nil
        )
        guard let aimodelURL = contents.first(where: { $0.pathExtension == "aimodel" }) else {
            throw LayoutLMError.modelNotFound(path: bundlePath.path)
        }

        #if RECEIPT_OCR_COREAI && canImport(CoreAI)
        if #available(macOS 27.0, *) {
            self.runtime = try Runtime(aimodelURL: aimodelURL)
            return
        }
        #endif

        throw LayoutLMError.backendUnavailable(
            "Core AI backend requires macOS 27+ and a worker built with "
                + "RECEIPT_OCR_COREAI=1. Found \(aimodelURL.lastPathComponent) "
                + "but this build cannot run it. Use LAYOUTLM_BACKEND=coreml, "
                + "or rebuild with RECEIPT_OCR_COREAI=1 on macOS 27."
        )
    }

    public func predictLogits(_ inputs: LayoutLMForwardInputs) throws -> LayoutLMLogits {
        #if RECEIPT_OCR_COREAI && canImport(CoreAI)
        if #available(macOS 27.0, *), let runtime = runtime as? Runtime {
            return try runtime.predictLogits(inputs)
        }
        #endif
        throw LayoutLMError.backendUnavailable(
            "Core AI runtime is not available in this build/OS"
        )
    }

    #if RECEIPT_OCR_COREAI && canImport(CoreAI)
    @available(macOS 27.0, *)
    private final class Runtime {
        private let function: InferenceFunction

        init(aimodelURL: URL) throws {
            self.function = try CoreAILayoutLMBackend.runBlocking {
                let model = try await AIModel(contentsOf: aimodelURL)
                guard let function = try model.loadFunction(named: "main") else {
                    throw LayoutLMError.predictionFailed(
                        "Core AI model at \(aimodelURL.path) has no 'main' function"
                    )
                }
                return function
            }
        }

        func predictLogits(_ inputs: LayoutLMForwardInputs) throws -> LayoutLMLogits {
            let function = self.function
            return try CoreAILayoutLMBackend.runBlocking {
                try await Self.run(function: function, inputs: inputs)
            }
        }

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

            var flatBbox: [Int32] = []
            flatBbox.reserveCapacity(seqLength * 4)
            for box in inputs.bbox {
                guard box.count == 4 else {
                    throw LayoutLMError.predictionFailed("bbox must have 4 coords")
                }
                flatBbox.append(contentsOf: box)
            }

            let descriptor = function.descriptor
            let ndInputs: [String: NDArray] = [
                "input_ids": try makeIntArray(
                    name: "input_ids",
                    shape: [1, seqLength],
                    values: inputs.inputIds,
                    descriptor: descriptor
                ),
                "attention_mask": try makeIntArray(
                    name: "attention_mask",
                    shape: [1, seqLength],
                    values: inputs.attentionMask,
                    descriptor: descriptor
                ),
                "bbox": try makeIntArray(
                    name: "bbox",
                    shape: [1, seqLength, 4],
                    values: flatBbox,
                    descriptor: descriptor
                ),
                "token_type_ids": try makeIntArray(
                    name: "token_type_ids",
                    shape: [1, seqLength],
                    values: inputs.tokenTypeIds,
                    descriptor: descriptor
                ),
            ]

            var outputs = try await function.run(inputs: ndInputs)
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

        /// Build an integer input tensor in the scalar type the exported
        /// function declares for `name` (torch.export emits int64 for token
        /// ids; coreai-torch may narrow to int32), falling back to int32.
        private static func makeIntArray(
            name: String,
            shape: [Int],
            values: [Int32],
            descriptor: InferenceFunctionDescriptor
        ) throws -> NDArray {
            let expected = shape.reduce(1, *)
            guard expected == values.count else {
                throw LayoutLMError.predictionFailed(
                    "NDArray \(name) shape \(shape) expects \(expected) values, got \(values.count)"
                )
            }
            var scalarType: NDArray.ScalarType = .int32
            if case .ndArray(let arrayDescriptor)? = descriptor.inputDescriptor(of: name) {
                scalarType = arrayDescriptor.scalarType
            }
            var array = NDArray(shape: shape, scalarType: scalarType)
            switch scalarType {
            case .int64:
                var view = array.mutableView(as: Int64.self)
                view.copyElements(fromContentsOf: values.map { Int64($0) })
            case .int32:
                var view = array.mutableView(as: Int32.self)
                view.copyElements(fromContentsOf: values)
            default:
                throw LayoutLMError.predictionFailed(
                    "Core AI input \(name) has unsupported scalar type \(scalarType)"
                )
            }
            return array
        }

        /// Copy a float32 (or float16) output tensor into a flat array.
        private static func readFloats(from array: NDArray) throws -> [Float] {
            let count = array.shape.reduce(1, *)
            switch array.scalarType {
            case .float32:
                let view = array.view(as: Float.self)
                return view.withUnsafePointer { pointer, _, _ in
                    Array(UnsafeBufferPointer(start: pointer, count: count))
                }
            case .float16:
                let view = array.view(as: Float16.self)
                return view.withUnsafePointer { pointer, _, _ in
                    UnsafeBufferPointer(start: pointer, count: count).map { Float($0) }
                }
            default:
                throw LayoutLMError.predictionFailed(
                    "Core AI logits have unsupported scalar type \(array.scalarType)"
                )
            }
        }
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
