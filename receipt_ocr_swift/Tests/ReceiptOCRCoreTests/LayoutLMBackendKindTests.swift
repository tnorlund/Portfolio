import XCTest
@testable import ReceiptOCRCore

#if os(macOS)
final class LayoutLMBackendKindTests: XCTestCase {
    func test_default_is_coreml() throws {
        let kind = try LayoutLMBackendKind.resolve(
            explicit: nil,
            environment: [:]
        )
        XCTAssertEqual(kind, .coreml)
    }

    func test_empty_explicit_falls_back_to_coreml() throws {
        let kind = try LayoutLMBackendKind.resolve(
            explicit: "  ",
            environment: ["LAYOUTLM_BACKEND": "coreai"]
        )
        XCTAssertEqual(kind, .coreml)
    }

    func test_env_selects_coreai() throws {
        let kind = try LayoutLMBackendKind.resolve(
            explicit: nil,
            environment: ["LAYOUTLM_BACKEND": "coreai"]
        )
        XCTAssertEqual(kind, .coreai)
    }

    func test_explicit_overrides_env() throws {
        let kind = try LayoutLMBackendKind.resolve(
            explicit: "coreml",
            environment: ["LAYOUTLM_BACKEND": "coreai"]
        )
        XCTAssertEqual(kind, .coreml)
    }

    func test_invalid_backend_throws() {
        XCTAssertThrowsError(
            try LayoutLMBackendKind.resolve(
                explicit: "onnx",
                environment: [:]
            )
        ) { error in
            guard case LayoutLMError.invalidBackend(let value) = error else {
                return XCTFail("expected invalidBackend, got \(error)")
            }
            XCTAssertEqual(value, "onnx")
        }
    }

    func test_config_defaults_to_coreml_backend() throws {
        let config = try Config.load(
            env: nil,
            ocrJobQueueURL: "q1",
            ocrResultsQueueURL: "q2",
            dynamoTableName: "table",
            rawBucketName: "raw",
            region: "us-east-1",
            localstackEndpoint: nil
        )
        XCTAssertEqual(config.layoutLMBackend, .coreml)
    }

    func test_config_accepts_explicit_coreai_backend() throws {
        let config = try Config.load(
            env: nil,
            ocrJobQueueURL: "q1",
            ocrResultsQueueURL: "q2",
            dynamoTableName: "table",
            rawBucketName: "raw",
            region: "us-east-1",
            localstackEndpoint: nil,
            layoutLMBackend: "coreai"
        )
        XCTAssertEqual(config.layoutLMBackend, .coreai)
    }
}
#endif
