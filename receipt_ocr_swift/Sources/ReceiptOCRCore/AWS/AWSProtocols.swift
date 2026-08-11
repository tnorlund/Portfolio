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
    /// than raising").
    ///
    /// Not called anywhere yet. Two reasons the worker does not write
    /// sections today: at single-pass time a section write precedes the
    /// receipt's words and would fire the stream's canonical-ITEMS
    /// trigger against a word-less receipt; at summary-refine time the
    /// sections already exist and may carry VALID status or verifier
    /// provenance, which this blind put would erase. Any future caller
    /// needs read-modify-write semantics — read the existing section,
    /// carry its validation metadata forward, then write.
    func addReceiptSections(
        imageId: String, receiptId: Int,
        sections: [ReceiptSectionPayload], createdAt: Date
    ) async throws

    /// Batch-put worker-decoded line items (upsert semantics). Line-item
    /// writes fire no stream trigger, so the single-pass worker calls
    /// this directly; the cloud ingest's delete-then-add over the same
    /// payload remains the staleness reconciler of record.
    func addReceiptLineItems(
        imageId: String, receiptId: Int,
        items: [ReceiptLineItemPayload], extractedAt: Date,
        baselineFiguresAgreeing: Int?
    ) async throws
}


