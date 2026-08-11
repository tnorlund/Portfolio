import Foundation

public protocol SQSClientProtocol {
    func receiveMessages(queueURL: String, maxNumber: Int, visibilityTimeout: Int) async throws -> [SQSMessage]
    func deleteMessages(queueURL: String, entries: [SQSDeleteEntry]) async throws
    func sendMessage(queueURL: String, body: String) async throws
}

public struct SQSMessage {
    public let messageId: String
    public let receiptHandle: String
    public let body: String
}

public struct SQSDeleteEntry {
    public let id: String
    public let receiptHandle: String
}

/// Thrown by `S3ClientProtocol.getObject` when the object does not exist.
/// A typed error so the worker can tell "source image was deleted" (permanent,
/// fail the job and drop the message) from transient S3 failures (leave the
/// message for redelivery) without importing Soto types.
public struct ObjectNotFoundError: Error {
    public let bucket: String
    public let key: String
    public init(bucket: String, key: String) {
        self.bucket = bucket
        self.key = key
    }
}

public protocol S3ClientProtocol {
    func getObject(bucket: String, key: String) async throws -> Data
    func uploadFile(url: URL, bucket: String, key: String) async throws
}

public protocol DynamoClientProtocol {
    func getOCRJob(imageId: String, jobId: String) async throws -> OCRJob
    func updateOCRJob(_ job: OCRJob) async throws
    func updateOCRJobStage(imageId: String, jobId: String, stage: String) async throws
    func addOCRRoutingDecision(_ decision: OCRRoutingDecision) async throws
    func addReceiptWordLabels(_ labels: [ReceiptWordLabel]) async throws

    /// Batch-put worker-decoded sections (upsert semantics, matching the
    /// cloud ingest's batch put "so an SQS redelivery rewrites rather
    /// than raising"). NOT called at single-pass time — a section write
    /// before the receipt's words exist would fire the stream's canonical
    /// ITEMS trigger and cause a premature cloud recompute against a
    /// word-less receipt. The summary-refine pass is the caller.
    func addReceiptSections(
        imageId: String, receiptId: Int,
        sections: [ReceiptSectionPayload], createdAt: Date
    ) async throws

    /// Batch-put worker-decoded line items (upsert semantics, unconditional).
    /// Line-item writes fire no stream trigger, so the worker can call this
    /// directly; the cloud ingest's delete-then-add over the same payload
    /// remains the staleness reconciler of record. The summary-refine pass
    /// is the caller — it runs after the cloud pipeline and is meant to
    /// overwrite whatever is there. The single-pass write must NOT use this;
    /// see `addReceiptLineItemsIfWorkerOwned`.
    func addReceiptLineItems(
        imageId: String, receiptId: Int,
        items: [ReceiptLineItemPayload], extractedAt: Date,
        baselineFiguresAgreeing: Int?
    ) async throws

    /// Per-item conditional put of worker-decoded line items: a row is
    /// written only when it does not exist yet or the worker itself wrote
    /// it (`extractor_version` begins with
    /// `swiftWorkerExtractorVersionPrefix`).
    ///
    /// Ownership rule: rows the cloud pipeline produced carry the bare
    /// decoder version (`line-items-blocks-v2`) with no `swift-worker`
    /// prefix, and may have been enriched since (merchant_name plus the
    /// GSI1 rollup keys it unlocks, VALID section provenance,
    /// reconciliation against the real summary). The worker's single-pass
    /// payload is sparse by design, so replacing such a row can only
    /// destroy information — the condition makes that impossible for every
    /// ordering of crash, redelivery and cloud enrichment, not just the
    /// ones the job-status check happens to catch.
    ///
    /// Conditional writes cannot ride a DynamoDB batch, so this issues one
    /// `PutItem` per row. A `ConditionalCheckFailedException` is not an
    /// error: it means the row is cloud-owned and is skipped.
    ///
    /// - Returns: how many rows were skipped as cloud-owned.
    @discardableResult
    func addReceiptLineItemsIfWorkerOwned(
        imageId: String, receiptId: Int,
        items: [ReceiptLineItemPayload], extractedAt: Date,
        baselineFiguresAgreeing: Int?
    ) async throws -> Int
}


