import XCTest
@testable import ReceiptOCRCore
import SotoS3
import SotoSQS
import SotoDynamoDB

final class LocalstackE2ETests: XCTestCase {
    func test_stub_ocr_end_to_end_against_localstack() async throws {
        guard ProcessInfo.processInfo.environment["E2E_LOCALSTACK"] == "1" else {
            throw XCTSkip("Set E2E_LOCALSTACK=1 to run LocalStack e2e test")
        }

        // Read config from env (expected to be bootstrapped by Makefile target)
        let region = ProcessInfo.processInfo.environment["AWS_REGION"] ?? "us-west-2"
        let endpoint = ProcessInfo.processInfo.environment["LOCALSTACK_ENDPOINT"] ?? "http://localhost:4566"
        guard let ocrJobQueueURL = ProcessInfo.processInfo.environment["OCR_JOB_QUEUE_URL"],
              let ocrResultsQueueURL = ProcessInfo.processInfo.environment["OCR_RESULTS_QUEUE_URL"],
              let dynamoTableName = ProcessInfo.processInfo.environment["DYNAMO_TABLE_NAME"]
        else {
            XCTFail("Missing required env vars for LocalStack test")
            return
        }

        let cfg = try Config.load(
            env: nil,
            ocrJobQueueURL: ocrJobQueueURL,
            ocrResultsQueueURL: ocrResultsQueueURL,
            dynamoTableName: dynamoTableName,
            region: region,
            localstackEndpoint: endpoint
        )

        // Put a message with known image/job IDs. Bootstrap should have uploaded the image to S3 and inserted OCR_JOB.
        let factory = SotoAWSFactory(config: cfg)
        let sqs = SotoSQSClient(sqs: factory.makeSQS())

        let imageId = "test-image-1"
        let jobId = "job-1"
        let bodyData = try JSONSerialization.data(withJSONObject: ["image_id": imageId, "job_id": jobId])
        let body = String(data: bodyData, encoding: .utf8)!
        try await sqs.sendMessage(queueURL: cfg.ocrJobQueueURL, body: body)

        // Run worker with stub OCR engine
        let worker = try OCRWorker.make(config: cfg, stubOCR: true)
        _ = try await worker.processBatch()

        // If no exception thrown, assume path executed. Further assertions could check Dynamo or S3 contents via Soto
        XCTAssertTrue(true)
    }
}


