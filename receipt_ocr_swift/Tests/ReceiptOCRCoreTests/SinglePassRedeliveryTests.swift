import Foundation
import Testing

@testable import ReceiptOCRCore

#if os(macOS)
import CoreGraphics

/// The worker's direct line-item write must not run when the job it is
/// processing had already COMPLETED — that delivery is a duplicate of work the
/// cloud pipeline already ingested and may have enriched, and the worker's
/// sparse payload would replace the enrichment.
/// Uses swift-testing so the suite also runs under CommandLineTools.
@Suite struct SinglePassRedeliveryTests {

    final class SQSMock: SQSClientProtocol {
        var messages: [SQSMessage] = []
        var sentMessages: [String] = []
        var deleted: [SQSDeleteEntry] = []
        func receiveMessages(queueURL: String, maxNumber: Int, visibilityTimeout: Int) async throws -> [SQSMessage] { messages }
        func deleteMessages(queueURL: String, entries: [SQSDeleteEntry]) async throws {
            deleted.append(contentsOf: entries)
        }
        func sendMessage(queueURL: String, body: String) async throws { sentMessages.append(body) }
    }

    final class S3Mock: S3ClientProtocol {
        var objects: [String: Data] = [:] // "bucket:key" -> Data
        func getObject(bucket: String, key: String) async throws -> Data {
            guard let data = objects["\(bucket):\(key)"] else {
                throw ObjectNotFoundError(bucket: bucket, key: key)
            }
            return data
        }
        func uploadFile(url: URL, bucket: String, key: String) async throws {}
    }

    final class DynamoMock: DynamoClientProtocol {
        var jobs: [String: OCRJob] = [:] // "imageId:jobId"
        var lineItemCalls: [(imageId: String, receiptId: Int, count: Int)] = []
        var sectionCalls: [(imageId: String, receiptId: Int)] = []
        func getOCRJob(imageId: String, jobId: String) async throws -> OCRJob {
            guard let job = jobs["\(imageId):\(jobId)"] else {
                throw DynamoMapError.missing("item")
            }
            return job
        }
        func updateOCRJob(_ job: OCRJob) async throws { jobs["\(job.imageId):\(job.jobId)"] = job }
        func updateOCRJobStage(imageId: String, jobId: String, stage: String) async throws {}
        func addOCRRoutingDecision(_ decision: OCRRoutingDecision) async throws {}
        func addReceiptWordLabels(_ labels: [ReceiptWordLabel]) async throws {}
        func addReceiptSections(
            imageId: String, receiptId: Int,
            sections: [ReceiptSectionPayload], createdAt: Date
        ) async throws {
            sectionCalls.append((imageId, receiptId))
        }
        func addReceiptLineItems(
            imageId: String, receiptId: Int,
            items: [ReceiptLineItemPayload], extractedAt: Date,
            baselineFiguresAgreeing: Int?
        ) async throws {
            lineItemCalls.append((imageId, receiptId, items.count))
        }
        func replaceReceiptLineItems(
            imageId: String, receiptId: Int,
            items: [ReceiptLineItemPayload], extractedAt: Date,
            baselineFiguresAgreeing: Int?, merchantName: String?
        ) async throws {
            lineItemCalls.append((imageId, receiptId, items.count))
        }
    }

    /// Emits the pinned single-pass contract fixture, whose `receipts[0]`
    /// carries a non-empty `line_items` array — the write path under test.
    struct FixtureOCREngine: OCREngineProtocol {
        static var fixtureData: Data {
            let url = URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent()
                .appendingPathComponent("Fixtures/swift_single_pass_contract.json")
            return (try? Data(contentsOf: url)) ?? Data()
        }

        func process(images: [URL], outputDirectory: URL, includeClassification: Bool) throws -> [URL] {
            try images.map { url in
                let out = outputDirectory.appendingPathComponent(
                    url.deletingPathExtension().lastPathComponent + ".json"
                )
                try Self.fixtureData.write(to: out)
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

    /// Queues one single-pass job in the given status and returns the mocks.
    private func makeFixture(status: OCRStatus) -> (SQSMock, S3Mock, DynamoMock, OCRWorker) {
        let sqs = SQSMock()
        let s3 = S3Mock()
        let dynamo = DynamoMock()
        let imageId = "img-1"
        let jobId = "job-1"
        sqs.messages = [
            SQSMessage(
                messageId: "m1",
                receiptHandle: "rh-m1",
                body: "{\"image_id\":\"\(imageId)\",\"job_id\":\"\(jobId)\"}"
            )
        ]
        let now = Date()
        dynamo.jobs["\(imageId):\(jobId)"] = OCRJob(
            imageId: imageId,
            jobId: jobId,
            s3Bucket: "b",
            s3Key: "raw/\(imageId).png",
            createdAt: now,
            updatedAt: now,
            status: status
        )
        s3.objects["b:raw/\(imageId).png"] = ReOCRTestImages.pngData(
            from: ReOCRTestImages.makeImage(width: 10, height: 10) { ctx in
                ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
                ctx.fill(CGRect(x: 0, y: 0, width: 10, height: 10))
            }
        )
        let worker = OCRWorker(
            config: makeConfig(),
            ocr: FixtureOCREngine(),
            sqs: sqs,
            s3: s3,
            dynamo: dynamo
        )
        return (sqs, s3, dynamo, worker)
    }

    @Test func pendingJobWritesLineItems() async throws {
        let (sqs, _, dynamo, worker) = makeFixture(status: .pending)

        let hadMessages = try await worker.processBatch()

        #expect(hadMessages)
        #expect(dynamo.lineItemCalls.count == 1)
        #expect(dynamo.lineItemCalls.first?.imageId == "img-1")
        #expect((dynamo.lineItemCalls.first?.count ?? 0) > 0)
        #expect(sqs.deleted.map(\.id) == ["m1"])
    }

    @Test func redeliveredCompletedJobSkipsLineItemWrite() async throws {
        let (sqs, _, dynamo, worker) = makeFixture(status: .completed)

        let hadMessages = try await worker.processBatch()

        // No write over rows the cloud pipeline may already have enriched,
        // but the batch still finishes and the duplicate leaves the queue.
        #expect(hadMessages)
        #expect(dynamo.lineItemCalls.isEmpty)
        #expect(sqs.deleted.map(\.id) == ["m1"])
        #expect(sqs.sentMessages.count == 1)
    }
}
#endif
