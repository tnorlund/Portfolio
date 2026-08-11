import Foundation
import Testing

@testable import ReceiptOCRCore

#if os(macOS)

/// LINE_ITEM_REFINE (Tier 3): the second worker pass re-decodes the
/// receipt's STORED OCR JSON with the real summary carried on the job and
/// writes sections + line items straight to DynamoDB. Uses swift-testing
/// so the suite also runs under CommandLineTools.
@Suite struct LineItemRefineTests {

    final class SQSMock: SQSClientProtocol {
        var messages: [SQSMessage] = []
        var deleted: [SQSDeleteEntry] = []
        func receiveMessages(queueURL: String, maxNumber: Int, visibilityTimeout: Int) async throws -> [SQSMessage] { messages }
        func deleteMessages(queueURL: String, entries: [SQSDeleteEntry]) async throws {
            deleted.append(contentsOf: entries)
        }
        func sendMessage(queueURL: String, body: String) async throws {}
    }

    final class S3Mock: S3ClientProtocol {
        var objects: [String: Data] = [:]
        func getObject(bucket: String, key: String) async throws -> Data {
            guard let data = objects["\(bucket):\(key)"] else {
                throw ObjectNotFoundError(bucket: bucket, key: key)
            }
            return data
        }
        func uploadFile(url: URL, bucket: String, key: String) async throws {}
    }

    final class DynamoMock: DynamoClientProtocol {
        var jobs: [String: OCRJob] = [:]
        var writtenSections: [(receiptId: Int, sections: [ReceiptSectionPayload])] = []
        var writtenItems: [(receiptId: Int, items: [ReceiptLineItemPayload], grade: Int?)] = []
        func getOCRJob(imageId: String, jobId: String) async throws -> OCRJob {
            guard let job = jobs["\(imageId):\(jobId)"] else {
                throw DynamoMapError.missing("item")
            }
            return job
        }
        func updateOCRJob(_ job: OCRJob) async throws {
            jobs["\(job.imageId):\(job.jobId)"] = job
        }
        func updateOCRJobStage(imageId: String, jobId: String, stage: String) async throws {}
        func addOCRRoutingDecision(_ decision: OCRRoutingDecision) async throws {}
        func addReceiptWordLabels(_ labels: [ReceiptWordLabel]) async throws {}
        func addReceiptSections(
            imageId: String, receiptId: Int,
            sections: [ReceiptSectionPayload], createdAt: Date
        ) async throws {
            writtenSections.append((receiptId, sections))
        }
        func addReceiptLineItems(
            imageId: String, receiptId: Int,
            items: [ReceiptLineItemPayload], extractedAt: Date,
            baselineFiguresAgreeing: Int?
        ) async throws {
            writtenItems.append((receiptId, items, baselineFiguresAgreeing))
        }
    }

    struct StubOCREngine: OCREngineProtocol {
        func process(images: [URL], outputDirectory: URL, includeClassification: Bool) throws -> [URL] { [] }
    }

    static let imageId = "12345678-1234-4123-8123-123456789012"
    static let jobId = "87654321-4321-4321-8321-210987654321"

    private func makeWorker(_ sqs: SQSMock, _ s3: S3Mock, _ dynamo: DynamoMock) -> OCRWorker {
        OCRWorker(
            config: Config(
                ocrJobQueueURL: "q1",
                ocrResultsQueueURL: "q2",
                dynamoTableName: "tbl",
                region: "us-west-2",
                localstackEndpoint: nil,
                logLevel: "error",
                rawBucketName: "test-bucket"
            ),
            ocr: StubOCREngine(),
            sqs: sqs,
            s3: s3,
            dynamo: dynamo
        )
    }

    private func contractJSON() throws -> Data {
        let url = try #require(
            Bundle.module.url(
                forResource: "swift_single_pass_contract",
                withExtension: "json", subdirectory: "Fixtures"
            )
        )
        return try Data(contentsOf: url)
    }

    private func refineJob(
        receiptId: Int?, s3Key: String = "ocr_results/contract.json"
    ) -> OCRJob {
        let now = Date()
        return OCRJob(
            imageId: Self.imageId,
            jobId: Self.jobId,
            s3Bucket: "b",
            s3Key: s3Key,
            createdAt: now,
            updatedAt: now,
            status: .pending,
            jobType: .lineItemRefine,
            receiptId: receiptId,
            refineSummary: LineItemSummary(
                subtotal: 3.99, tax: nil, grandTotal: nil
            ),
            refineMerchantName: "SPROUTS FARMERS MARKET"
        )
    }

    private func message() -> SQSMessage {
        SQSMessage(
            messageId: "m1",
            receiptHandle: "rh-m1",
            body: "{\"image_id\":\"\(Self.imageId)\",\"job_id\":\"\(Self.jobId)\"}"
        )
    }

    @Test func refineDecodesStoredJSONAndWritesStructure() async throws {
        let sqs = SQSMock()
        let s3 = S3Mock()
        let dynamo = DynamoMock()
        let envelope = try JSONSerialization.jsonObject(
            with: contractJSON()
        ) as! [String: Any]
        let receipts = envelope["receipts"] as! [[String: Any]]
        let clusterId = receipts[0]["cluster_id"] as! Int

        sqs.messages = [message()]
        dynamo.jobs["\(Self.imageId):\(Self.jobId)"] = refineJob(
            receiptId: clusterId
        )
        s3.objects["b:ocr_results/contract.json"] = try contractJSON()

        _ = try await makeWorker(sqs, s3, dynamo).processBatch()

        // Structure written via the Tier-2 surface, message deleted, job
        // completed with the summary-aware decode.
        #expect(dynamo.writtenSections.count == 1)
        #expect(dynamo.writtenItems.count == 1)
        #expect(dynamo.writtenItems[0].receiptId == clusterId)
        #expect(!dynamo.writtenItems[0].items.isEmpty)
        #expect(sqs.deleted.map(\.id) == ["m1"])
        let job = dynamo.jobs["\(Self.imageId):\(Self.jobId)"]
        #expect(job?.status == .completed)
        // The contract receipt's single 3.99 item matches the carried
        // subtotal: the graded verdict must ride on the rows.
        #expect(
            dynamo.writtenItems[0].items.allSatisfy {
                $0.reconciliationStatus == "match"
            }
        )
    }

    @Test func refineWithMissingReceiptFailsJobAndDropsMessage() async throws {
        let sqs = SQSMock()
        let s3 = S3Mock()
        let dynamo = DynamoMock()
        sqs.messages = [message()]
        dynamo.jobs["\(Self.imageId):\(Self.jobId)"] = refineJob(
            receiptId: 999
        )
        s3.objects["b:ocr_results/contract.json"] = try contractJSON()

        _ = try await makeWorker(sqs, s3, dynamo).processBatch()

        #expect(dynamo.writtenItems.isEmpty)
        #expect(sqs.deleted.map(\.id) == ["m1"])
        #expect(dynamo.jobs["\(Self.imageId):\(Self.jobId)"]?.status == .failed)
    }

    @Test func refineWithDeletedJSONFailsJobAndDropsMessage() async throws {
        let sqs = SQSMock()
        let s3 = S3Mock()
        let dynamo = DynamoMock()
        sqs.messages = [message()]
        dynamo.jobs["\(Self.imageId):\(Self.jobId)"] = refineJob(receiptId: 1)
        // No S3 object: the stored JSON was deleted.

        _ = try await makeWorker(sqs, s3, dynamo).processBatch()

        #expect(dynamo.writtenItems.isEmpty)
        #expect(sqs.deleted.map(\.id) == ["m1"])
        #expect(dynamo.jobs["\(Self.imageId):\(Self.jobId)"]?.status == .failed)
    }

    @Test func refineJobRoundTripsThroughDynamoItemMapping() throws {
        let job = refineJob(receiptId: 2)
        let restored = try OCRJob.fromItem(job.toItem())
        #expect(restored.jobType == .lineItemRefine)
        #expect(restored.receiptId == 2)
        #expect(restored.refineSummary?.subtotal == 3.99)
        #expect(restored.refineSummary?.tax == nil)
        #expect(restored.refineMerchantName == "SPROUTS FARMERS MARKET")
    }
}

#endif
