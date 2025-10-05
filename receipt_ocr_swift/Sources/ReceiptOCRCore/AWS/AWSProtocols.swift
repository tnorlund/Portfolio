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

public protocol S3ClientProtocol {
    func getObject(bucket: String, key: String) async throws -> Data
    func uploadFile(url: URL, bucket: String, key: String) async throws
}

public protocol DynamoClientProtocol {
    func getOCRJob(imageId: String, jobId: String) async throws -> OCRJob
    func updateOCRJob(_ job: OCRJob) async throws
    func addOCRRoutingDecision(_ decision: OCRRoutingDecision) async throws
}


