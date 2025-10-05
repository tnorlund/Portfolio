import XCTest
@testable import ReceiptOCRCore

final class WorkerTests: XCTestCase {
    final class SQSMock: SQSClientProtocol {
        var messages: [SQSMessage] = []
        var sentMessages: [String] = []
        var deleted: [SQSDeleteEntry] = []
        func receiveMessages(queueURL: String, maxNumber: Int, visibilityTimeout: Int) async throws -> [SQSMessage] { messages }
        func deleteMessages(queueURL: String, entries: [SQSDeleteEntry]) async throws { deleted.append(contentsOf: entries) }
        func sendMessage(queueURL: String, body: String) async throws { sentMessages.append(body) }
    }

    final class S3Mock: S3ClientProtocol {
        var objects: [String: Data] = [:] // "bucket:key" -> Data
        var uploads: [(bucket: String, key: String, data: Data)] = []
        func getObject(bucket: String, key: String) async throws -> Data {
            let k = "\(bucket):\(key)"
            return objects[k] ?? Data()
        }
        func uploadFile(url: URL, bucket: String, key: String) async throws {
            let data = try Data(contentsOf: url)
            uploads.append((bucket, key, data))
        }
    }

    final class DynamoMock: DynamoClientProtocol {
        var jobs: [String: OCRJob] = [:] // "imageId:jobId"
        var routing: [OCRRoutingDecision] = []
        func getOCRJob(imageId: String, jobId: String) async throws -> OCRJob {
            return jobs["\(imageId):\(jobId)"]!
        }
        func updateOCRJob(_ job: OCRJob) async throws { jobs["\(job.imageId):\(job.jobId)"] = job }
        func addOCRRoutingDecision(_ decision: OCRRoutingDecision) async throws { routing.append(decision) }
    }

    struct TestOCREngine: OCREngineProtocol {
        func process(images: [URL], outputDirectory: URL) throws -> [URL] {
            return try images.map { url in
                let out = outputDirectory.appendingPathComponent(url.deletingPathExtension().lastPathComponent + ".json")
                try Data("{\"ok\":true}".utf8).write(to: out)
                return out
            }
        }
    }

    func test_worker_processes_messages_and_updates_state() async throws {
        let cfg = Config(
            ocrJobQueueURL: "q1",
            ocrResultsQueueURL: "q2",
            dynamoTableName: "tbl",
            region: "us-west-2",
            localstackEndpoint: nil,
            logLevel: "debug"
        )

        var sqs = SQSMock()
        let s3 = S3Mock()
        let dynamo = DynamoMock()

        let imageId = "img-1"
        let jobId = "job-1"
        let body = "{\"image_id\":\"\(imageId)\",\"job_id\":\"\(jobId)\"}"
        sqs.messages = [SQSMessage(messageId: "m1", receiptHandle: "rh1", body: body)]

        let now = Date()
        dynamo.jobs["\(imageId):\(jobId)"] = OCRJob(
            imageId: imageId,
            jobId: jobId,
            s3Bucket: "b",
            s3Key: "receipts/abc.png",
            createdAt: now,
            updatedAt: now,
            status: .pending
        )
        var s3mut = s3
        s3mut.objects["b:receipts/abc.png"] = Data([0xFF, 0xD8, 0xFF])

        let worker = OCRWorker(
            config: cfg,
            ocr: TestOCREngine(),
            sqs: sqs,
            s3: s3mut,
            dynamo: dynamo
        )

        let hadMessages = try await worker.processBatch()
        
        XCTAssertTrue(hadMessages)
        XCTAssertEqual(dynamo.routing.count, 1)
        XCTAssertEqual(dynamo.jobs["\(imageId):\(jobId)"]?.status, .completed)
    }
}


