import Foundation
import Testing

@testable import ReceiptOCRCore

#if os(macOS)
import CoreGraphics

/// A message whose job row or source image was deleted must be dropped from
/// the queue — not thrown out of `processBatch`, which aborted the whole
/// drain and (with no DLQ) let the message come back to abort the next one.
/// Uses swift-testing so the suite also runs under CommandLineTools.
@Suite struct OCRWorkerPoisonMessageTests {

    final class SQSMock: SQSClientProtocol {
        var messages: [SQSMessage] = []
        var sentMessages: [String] = []
        var deleted: [SQSDeleteEntry] = []
        var emptyDeleteBatches = 0
        func receiveMessages(queueURL: String, maxNumber: Int, visibilityTimeout: Int) async throws -> [SQSMessage] { messages }
        func deleteMessages(queueURL: String, entries: [SQSDeleteEntry]) async throws {
            if entries.isEmpty { emptyDeleteBatches += 1 }
            deleted.append(contentsOf: entries)
        }
        func sendMessage(queueURL: String, body: String) async throws { sentMessages.append(body) }
    }

    final class S3Mock: S3ClientProtocol {
        var objects: [String: Data] = [:] // "bucket:key" -> Data
        var uploads: [(bucket: String, key: String, data: Data)] = []
        func getObject(bucket: String, key: String) async throws -> Data {
            guard let data = objects["\(bucket):\(key)"] else {
                throw ObjectNotFoundError(bucket: bucket, key: key)
            }
            return data
        }
        func uploadFile(url: URL, bucket: String, key: String) async throws {
            let data = try Data(contentsOf: url)
            uploads.append((bucket, key, data))
        }
    }

    final class DynamoMock: DynamoClientProtocol {
        var jobs: [String: OCRJob] = [:] // "imageId:jobId"
        var stages: [String: String] = [:]
        func getOCRJob(imageId: String, jobId: String) async throws -> OCRJob {
            guard let job = jobs["\(imageId):\(jobId)"] else {
                throw DynamoMapError.missing("item")
            }
            return job
        }
        func updateOCRJob(_ job: OCRJob) async throws { jobs["\(job.imageId):\(job.jobId)"] = job }
        func updateOCRJobStage(imageId: String, jobId: String, stage: String) async throws {
            stages["\(imageId):\(jobId)"] = stage
        }
        func addOCRRoutingDecision(_ decision: OCRRoutingDecision) async throws {}
        func addReceiptWordLabels(_ labels: [ReceiptWordLabel]) async throws {}
        func addReceiptSections(
            imageId: String, receiptId: Int,
            sections: [ReceiptSectionPayload], createdAt: Date
        ) async throws {}
        func addReceiptLineItems(
            imageId: String, receiptId: Int,
            items: [ReceiptLineItemPayload], extractedAt: Date,
            baselineFiguresAgreeing: Int?
        ) async throws {}
    }

    struct StubOCREngine: OCREngineProtocol {
        func process(images: [URL], outputDirectory: URL, includeClassification: Bool) throws -> [URL] {
            try images.map { url in
                let out = outputDirectory.appendingPathComponent(url.deletingPathExtension().lastPathComponent + ".json")
                try Data("{\"lines\": []}".utf8).write(to: out)
                return out
            }
        }
    }

    private func makeConfig() -> Config {
        Config(
            ocrJobQueueURL: "q1",
            ocrResultsQueueURL: "q2",
            dynamoTableName: "tbl",
            region: "us-west-2",
            localstackEndpoint: nil,
            logLevel: "error",
            rawBucketName: "test-bucket"
        )
    }

    private func message(_ id: String, imageId: String, jobId: String) -> SQSMessage {
        SQSMessage(
            messageId: id,
            receiptHandle: "rh-\(id)",
            body: "{\"image_id\":\"\(imageId)\",\"job_id\":\"\(jobId)\"}"
        )
    }

    private func addHealthyJob(_ dynamo: DynamoMock, _ s3: S3Mock, imageId: String, jobId: String) {
        let now = Date()
        dynamo.jobs["\(imageId):\(jobId)"] = OCRJob(
            imageId: imageId,
            jobId: jobId,
            s3Bucket: "b",
            s3Key: "raw/\(imageId).png",
            createdAt: now,
            updatedAt: now,
            status: .pending
        )
        s3.objects["b:raw/\(imageId).png"] = ReOCRTestImages.pngData(
            from: ReOCRTestImages.makeImage(width: 10, height: 10) { ctx in
                ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
                ctx.fill(CGRect(x: 0, y: 0, width: 10, height: 10))
            }
        )
    }

    private func makeWorker(_ sqs: SQSMock, _ s3: S3Mock, _ dynamo: DynamoMock) -> OCRWorker {
        OCRWorker(
            config: makeConfig(),
            ocr: StubOCREngine(),
            sqs: sqs,
            s3: s3,
            dynamo: dynamo
        )
    }

    @Test func missingJobRowIsDroppedAndBatchContinues() async throws {
        let sqs = SQSMock()
        let s3 = S3Mock()
        let dynamo = DynamoMock()
        // m1 has no job row (image deleted after the message was queued);
        // m2 is healthy and must still complete.
        sqs.messages = [
            message("m1", imageId: "gone", jobId: "job-gone"),
            message("m2", imageId: "ok", jobId: "job-ok"),
        ]
        addHealthyJob(dynamo, s3, imageId: "ok", jobId: "job-ok")

        let hadMessages = try await makeWorker(sqs, s3, dynamo).processBatch()

        #expect(hadMessages)
        #expect(sqs.deleted.map(\.id).sorted() == ["m1", "m2"])
        #expect(dynamo.jobs["ok:job-ok"]?.status == .completed)
    }

    @Test func missingSourceImageFailsJobAndDropsMessage() async throws {
        let sqs = SQSMock()
        let s3 = S3Mock()
        let dynamo = DynamoMock()
        sqs.messages = [
            message("m1", imageId: "img-404", jobId: "job-404"),
            message("m2", imageId: "ok", jobId: "job-ok"),
        ]
        // Job row exists but the raw object was deleted from S3.
        let now = Date()
        dynamo.jobs["img-404:job-404"] = OCRJob(
            imageId: "img-404",
            jobId: "job-404",
            s3Bucket: "b",
            s3Key: "raw/img-404.png",
            createdAt: now,
            updatedAt: now,
            status: .pending
        )
        addHealthyJob(dynamo, s3, imageId: "ok", jobId: "job-ok")

        _ = try await makeWorker(sqs, s3, dynamo).processBatch()

        #expect(sqs.deleted.map(\.id).sorted() == ["m1", "m2"])
        #expect(dynamo.jobs["img-404:job-404"]?.status == .failed)
        #expect(dynamo.jobs["ok:job-ok"]?.status == .completed)
    }

    @Test func malformedBodyIsDropped() async throws {
        let sqs = SQSMock()
        let s3 = S3Mock()
        let dynamo = DynamoMock()
        sqs.messages = [
            SQSMessage(messageId: "m1", receiptHandle: "rh-m1", body: "not json"),
            message("m2", imageId: "ok", jobId: "job-ok"),
        ]
        addHealthyJob(dynamo, s3, imageId: "ok", jobId: "job-ok")

        _ = try await makeWorker(sqs, s3, dynamo).processBatch()

        #expect(sqs.deleted.map(\.id).sorted() == ["m1", "m2"])
    }

    @Test func allPoisonBatchSendsNoEmptyDeleteAndReportsMessages() async throws {
        let sqs = SQSMock()
        let s3 = S3Mock()
        let dynamo = DynamoMock()
        sqs.messages = [
            message("m1", imageId: "gone-1", jobId: "j1"),
            message("m2", imageId: "gone-2", jobId: "j2"),
        ]

        let hadMessages = try await makeWorker(sqs, s3, dynamo).processBatch()

        // Still reports messages were seen (the drain loop keeps polling),
        // deletes exactly the poison pair, and never issues an empty
        // delete batch (SQS rejects those).
        #expect(hadMessages)
        #expect(sqs.deleted.map(\.id).sorted() == ["m1", "m2"])
        #expect(sqs.emptyDeleteBatches == 0)
    }
}
#endif
