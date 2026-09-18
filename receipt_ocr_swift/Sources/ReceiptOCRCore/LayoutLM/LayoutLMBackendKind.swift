import Foundation

#if os(macOS)

/// Selects which on-device LayoutLM execution backend to use.
///
/// Core ML remains the default production path. Core AI is opt-in until
/// conversion + numerical + Swift runtime parity are proven.
public enum LayoutLMBackendKind: String, CaseIterable, Sendable {
    case coreml
    case coreai

    /// Resolve backend from an explicit value, falling back to
    /// ``LAYOUTLM_BACKEND`` and finally ``coreml``.
    public static func resolve(
        explicit: String? = nil,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) throws -> LayoutLMBackendKind {
        let raw = (explicit ?? environment["LAYOUTLM_BACKEND"] ?? "coreml")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        if raw.isEmpty {
            return .coreml
        }
        guard let kind = LayoutLMBackendKind(rawValue: raw) else {
            throw LayoutLMError.invalidBackend(raw)
        }
        return kind
    }

    /// Construct the concrete backend for a local model bundle.
    public func makeBackend(bundlePath: URL) throws -> LayoutLMBackend {
        switch self {
        case .coreml:
            return try CoreMLLayoutLMBackend(bundlePath: bundlePath)
        case .coreai:
            return try CoreAILayoutLMBackend(bundlePath: bundlePath)
        }
    }
}

#endif
