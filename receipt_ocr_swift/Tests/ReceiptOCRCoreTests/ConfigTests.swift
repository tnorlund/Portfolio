import XCTest
@testable import ReceiptOCRCore

final class ConfigTests: XCTestCase {
    struct StubPulumi: PulumiLoading {
        let outputs: [String: Any]
        func loadOutputs(env: String) throws -> [String : Any] { outputs }
    }

    func test_load_from_explicit_values() throws {
        let cfg = try Config.load(
            env: nil,
            ocrJobQueueURL: "q1",
            ocrResultsQueueURL: "q2",
            dynamoTableName: "tbl",
            rawBucketName: "my-bucket",
            region: "us-west-2",
            localstackEndpoint: nil
        )
        XCTAssertEqual(cfg.ocrJobQueueURL, "q1")
        XCTAssertEqual(cfg.ocrResultsQueueURL, "q2")
        XCTAssertEqual(cfg.dynamoTableName, "tbl")
        XCTAssertEqual(cfg.rawBucketName, "my-bucket")
        XCTAssertEqual(cfg.region, "us-west-2")
        XCTAssertNil(cfg.localstackEndpoint)
    }

    func test_load_from_pulumi() throws {
        let pulumi = StubPulumi(outputs: [
            "ocr_job_queue_url": "jq",
            "ocr_results_queue_url": "rq",
            "dynamodb_table_name": "dt",
            "raw_bucket_name": "rb"
        ])
        let cfg = try Config.load(
            env: "dev",
            ocrJobQueueURL: nil,
            ocrResultsQueueURL: nil,
            dynamoTableName: nil,
            region: "us-west-2",
            localstackEndpoint: nil,
            pulumi: pulumi
        )
        XCTAssertEqual(cfg.ocrJobQueueURL, "jq")
        XCTAssertEqual(cfg.ocrResultsQueueURL, "rq")
        XCTAssertEqual(cfg.dynamoTableName, "dt")
        XCTAssertEqual(cfg.rawBucketName, "rb")
    }

    func test_missing_fields_throw() {
        XCTAssertThrowsError(
            try Config.load(
                env: nil,
                ocrJobQueueURL: nil,
                ocrResultsQueueURL: nil,
                dynamoTableName: nil,
                region: "us-west-2",
                localstackEndpoint: nil
            )
        )
    }
}


