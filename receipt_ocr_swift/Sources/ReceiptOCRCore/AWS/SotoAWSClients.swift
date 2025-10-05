import Foundation
import SotoCore
import NIOCore
import SotoS3
import SotoSQS
import SotoDynamoDB

public final class SotoAWSFactory {
    public let awsClient: AWSClient
    public let region: SotoCore.Region?
    public let endpoint: String?

    public init(config: Config) {
        self.awsClient = AWSClient(httpClientProvider: .createNew)
        self.region = SotoCore.Region(rawValue: config.region)
        self.endpoint = config.localstackEndpoint?.absoluteString
    }

    deinit {
        try? awsClient.syncShutdown()
    }

    func makeS3() -> S3 { S3(client: awsClient, region: region, endpoint: endpoint) }
    func makeSQS() -> SQS { SQS(client: awsClient, region: region, endpoint: endpoint) }
    func makeDynamo() -> DynamoDB { DynamoDB(client: awsClient, region: region, endpoint: endpoint) }
}

public final class SotoSQSClient: SQSClientProtocol {
    private let sqs: SQS

    public init(sqs: SQS) { self.sqs = sqs }

    public func receiveMessages(queueURL: String, maxNumber: Int, visibilityTimeout: Int) async throws -> [SQSMessage] {
        let req = SQS.ReceiveMessageRequest(
            maxNumberOfMessages: maxNumber,
            queueUrl: queueURL,
            visibilityTimeout: visibilityTimeout
        )
        let resp = try await sqs.receiveMessage(req)
        let messages = (resp.messages ?? []).compactMap { m -> SQSMessage? in
            guard let id = m.messageId, let rh = m.receiptHandle, let body = m.body else { return nil }
            return SQSMessage(messageId: id, receiptHandle: rh, body: body)
        }
        return messages
    }

    public func deleteMessages(queueURL: String, entries: [SQSDeleteEntry]) async throws {
        guard !entries.isEmpty else { return }
        let batch = entries.map { e in SQS.DeleteMessageBatchRequestEntry(id: e.id, receiptHandle: e.receiptHandle) }
        let req = SQS.DeleteMessageBatchRequest(entries: batch, queueUrl: queueURL)
        _ = try await sqs.deleteMessageBatch(req)
    }

    public func sendMessage(queueURL: String, body: String) async throws {
        let req = SQS.SendMessageRequest(messageBody: body, queueUrl: queueURL)
        _ = try await sqs.sendMessage(req)
    }
}

public final class SotoS3Client: S3ClientProtocol {
    private let s3: S3

    public init(s3: S3) { self.s3 = s3 }

    public func getObject(bucket: String, key: String) async throws -> Data {
        let req = S3.GetObjectRequest(bucket: bucket, key: key)
        var data = Data()
        _ = try await s3.multipartDownload(
            req,
            logger: AWSClient.loggingDisabled,
            on: nil
        ) { byteBuffer, _, _ in
            data.append(contentsOf: byteBuffer.readableBytesView)
        }
        return data
    }

    public func uploadFile(url: URL, bucket: String, key: String) async throws {
        let data = try Data(contentsOf: url)
        let req = S3.PutObjectRequest(body: .data(data), bucket: bucket, key: key)
        _ = try await s3.putObject(req)
    }
}

public final class SotoDynamoClient: DynamoClientProtocol {
    private let dynamo: DynamoDB
    private let tableName: String

    public init(dynamo: DynamoDB, tableName: String) {
        self.dynamo = dynamo
        self.tableName = tableName
    }

    public func getOCRJob(imageId: String, jobId: String) async throws -> OCRJob {
        let key: [String: DynamoDB.AttributeValue] = [
            "PK": .s("IMAGE#\(imageId)"),
            "SK": .s("OCR_JOB#\(jobId)")
        ]
        let req = DynamoDB.GetItemInput(key: key, tableName: tableName)
        let resp = try await dynamo.getItem(req)
        guard let item = resp.item else { throw DynamoMapError.missing("item") }
        return try Self.decodeOCRJob(item)
    }

    public func updateOCRJob(_ job: OCRJob) async throws {
        // Update status and updated_at
        let key: [String: DynamoDB.AttributeValue] = [
            "PK": .s("IMAGE#\(job.imageId)"),
            "SK": .s("OCR_JOB#\(job.jobId)")
        ]
        let expr = "SET #s = :s, updated_at = :u"
        let names = ["#s": "status"]
        let values: [String: DynamoDB.AttributeValue] = [
            ":s": .s(job.status.rawValue),
            ":u": .s(ISO8601Python.format(job.updatedAt))
        ]
        let req = DynamoDB.UpdateItemInput(
            expressionAttributeNames: names,
            expressionAttributeValues: values,
            key: key,
            tableName: tableName,
            updateExpression: expr
        )
        _ = try await dynamo.updateItem(req)
    }

    public func addOCRRoutingDecision(_ decision: OCRRoutingDecision) async throws {
        var updatedAttr: DynamoDB.AttributeValue
        if let updated = decision.updatedAt { updatedAttr = .s(ISO8601Python.format(updated)) } else { updatedAttr = .null(true) }
        let item: [String: DynamoDB.AttributeValue] = [
            "PK": .s("IMAGE#\(decision.imageId)"),
            "SK": .s("ROUTING#\(decision.jobId)"),
            "TYPE": .s("OCR_ROUTING_DECISION"),
            "GSI1PK": .s("OCR_ROUTING_DECISION_STATUS#\(decision.status.rawValue)"),
            "GSI1SK": .s("ROUTING#\(decision.jobId)"),
            "s3_bucket": .s(decision.s3Bucket),
            "s3_key": .s(decision.s3Key),
            "created_at": .s(ISO8601Python.format(decision.createdAt)),
            "updated_at": updatedAttr,
            "receipt_count": .n(String(decision.receiptCount)),
            "status": .s(decision.status.rawValue)
        ]
        let req = DynamoDB.PutItemInput(item: item, tableName: tableName)
        _ = try await dynamo.putItem(req)
    }

    private static func decodeOCRJob(_ attrs: [String: DynamoDB.AttributeValue]) throws -> OCRJob {
        func getS(_ key: String) throws -> String {
            guard case .s(let v)? = attrs[key] else { throw DynamoMapError.missing(key) }
            return v
        }
        let pk = try getS("PK")
        let sk = try getS("SK")
        guard let imageId = pk.split(separator: "#").last.map(String.init) else { throw DynamoMapError.invalid("PK") }
        guard let jobId = sk.split(separator: "#").last.map(String.init) else { throw DynamoMapError.invalid("SK") }
        let s3Bucket = try getS("s3_bucket")
        let s3Key = try getS("s3_key")
        let createdAtStr = try getS("created_at")
        let updatedAtStr = try getS("updated_at")
        let statusStr = try getS("status")
        guard let createdAt = ISO8601Python.parse(createdAtStr) else { throw DynamoMapError.invalid("created_at") }
        guard let updatedAt = ISO8601Python.parse(updatedAtStr) ?? ISO8601Python.parse(createdAtStr) else { throw DynamoMapError.invalid("updated_at") }
        guard let status = OCRStatus(rawValue: statusStr) else { throw DynamoMapError.invalid("status") }
        return OCRJob(
            imageId: imageId,
            jobId: jobId,
            s3Bucket: s3Bucket,
            s3Key: s3Key,
            createdAt: createdAt,
            updatedAt: updatedAt,
            status: status
        )
    }
}


