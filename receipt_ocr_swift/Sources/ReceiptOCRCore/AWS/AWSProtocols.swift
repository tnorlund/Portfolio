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
}


