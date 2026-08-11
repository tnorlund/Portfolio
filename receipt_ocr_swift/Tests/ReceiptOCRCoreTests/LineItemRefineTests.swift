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
        var replacedItems: [(receiptId: Int, items: [ReceiptLineItemPayload], merchantName: String?)] = []
        /// Fails every `updateOCRJob` — used to prove a job whose
        /// terminal status will not persist leaves its message queued.
        var updatesFail = false
        func getOCRJob(imageId: String, jobId: String) async throws -> OCRJob {
            guard let job = jobs["\(imageId):\(jobId)"] else {
                throw DynamoMapError.missing("item")
            }
            return job
        }
        func updateOCRJob(_ job: OCRJob) async throws {
            if updatesFail { throw DynamoMapError.missing("update") }
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
        func addReceiptLineItemsIfWorkerOwned(
            imageId: String, receiptId: Int,
            items: [ReceiptLineItemPayload], extractedAt: Date,
            baselineFiguresAgreeing: Int?
        ) async throws -> Int { 0 }
        func replaceReceiptLineItems(
            imageId: String, receiptId: Int,
            items: [ReceiptLineItemPayload], extractedAt: Date,
            baselineFiguresAgreeing: Int?, merchantName: String?
        ) async throws {
            replacedItems.append((receiptId, items, merchantName))
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

        // Line items REPLACED via the Tier-2 surface (a put-only write
        // would leave stale higher indices behind when the
        // summary-aware decode yields fewer items), message deleted,
        // job completed with the summary-aware decode.
        #expect(dynamo.replacedItems.count == 1)
        #expect(dynamo.writtenItems.isEmpty)
        // Items ONLY: a section put would clobber existing VALID/verifier
        // metadata and fire the stream's canonical-ITEMS trigger, which
        // would re-invoke the updater that enqueued this job.
        #expect(dynamo.writtenSections.isEmpty)
        #expect(dynamo.replacedItems[0].receiptId == clusterId)
        #expect(!dynamo.replacedItems[0].items.isEmpty)
        // The merchant the cloud resolved rides back onto the rows, so
        // the refined put keeps the merchant rollup keys the cloud
        // writer put there.
        #expect(
            dynamo.replacedItems[0].merchantName
                == "SPROUTS FARMERS MARKET"
        )
        #expect(sqs.deleted.map(\.id) == ["m1"])
        let job = dynamo.jobs["\(Self.imageId):\(Self.jobId)"]
        #expect(job?.status == .completed)
        // The contract receipt's single 3.99 item matches the carried
        // subtotal: the graded verdict must ride on the rows.
        #expect(
            dynamo.replacedItems[0].items.allSatisfy {
                $0.reconciliationStatus == "match"
            }
        )
    }

    /// A REFINEMENT source job stores its warped-crop OCR under a
    /// top-level `lines` array with no `receipts` envelope (the worker
    /// runs those with includeClassification: false). The refine pass
    /// must accept that shape under the job's own receipt id, not fail
    /// the job for want of a cluster_id match.
    @Test func refineAcceptsTopLevelLinesEnvelope() async throws {
        let sqs = SQSMock()
        let s3 = S3Mock()
        let dynamo = DynamoMock()
        let envelope = try JSONSerialization.jsonObject(
            with: contractJSON()
        ) as! [String: Any]
        let receipts = envelope["receipts"] as! [[String: Any]]
        let linesArray = receipts[0]["lines"] as! [[String: Any]]
        let refinementJSON = try JSONSerialization.data(
            withJSONObject: ["lines": linesArray]
        )

        sqs.messages = [message()]
        // Deliberately NOT the contract's cluster_id: the job's own
        // receipt id is what identifies a single-receipt envelope.
        dynamo.jobs["\(Self.imageId):\(Self.jobId)"] = refineJob(receiptId: 7)
        s3.objects["b:ocr_results/contract.json"] = refinementJSON

        _ = try await makeWorker(sqs, s3, dynamo).processBatch()

        #expect(dynamo.replacedItems.count == 1)
        #expect(dynamo.replacedItems[0].receiptId == 7)
        #expect(!dynamo.replacedItems[0].items.isEmpty)
        #expect(dynamo.writtenSections.isEmpty)
        #expect(dynamo.jobs["\(Self.imageId):\(Self.jobId)"]?.status == .completed)
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

        #expect(dynamo.replacedItems.isEmpty)
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

        #expect(dynamo.replacedItems.isEmpty)
        #expect(sqs.deleted.map(\.id) == ["m1"])
        #expect(dynamo.jobs["\(Self.imageId):\(Self.jobId)"]?.status == .failed)
    }

    /// A permanent failure whose FAILED status will not persist must
    /// NOT drop its message: acknowledging it would leave the row
    /// PENDING, which the enqueuer reads as "a refine is already
    /// running" and never retries for this receipt again.
    @Test func refineLeavesMessageWhenFailedStatusCannotPersist()
        async throws
    {
        let sqs = SQSMock()
        let s3 = S3Mock()
        let dynamo = DynamoMock()
        sqs.messages = [message()]
        dynamo.jobs["\(Self.imageId):\(Self.jobId)"] = refineJob(
            receiptId: 999
        )
        s3.objects["b:ocr_results/contract.json"] = try contractJSON()
        dynamo.updatesFail = true

        _ = try await makeWorker(sqs, s3, dynamo).processBatch()

        #expect(dynamo.replacedItems.isEmpty)
        #expect(sqs.deleted.isEmpty)
        #expect(dynamo.jobs["\(Self.imageId):\(Self.jobId)"]?.status == .pending)
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
