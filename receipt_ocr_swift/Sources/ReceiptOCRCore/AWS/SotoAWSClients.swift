import Foundation
import SotoCore
import NIOCore
import SotoS3
import SotoSQS
import SotoDynamoDB

/// Errors that can occur during batch write operations.
public enum BatchWriteError: Error, LocalizedError {
    case unprocessedItems(count: Int)

    public var errorDescription: String? {
        switch self {
        case .unprocessedItems(let count):
            return "Failed to write \(count) items after max retries"
        }
    }
}

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

    public func updateOCRJobStage(imageId: String, jobId: String, stage: String) async throws {
        let key: [String: DynamoDB.AttributeValue] = [
            "PK": .s("IMAGE#\(imageId)"),
            "SK": .s("OCR_JOB#\(jobId)")
        ]
        let expr = "SET processing_stage = :ps, updated_at = :u"
        let values: [String: DynamoDB.AttributeValue] = [
            ":ps": .s(stage),
            ":u": .s(ISO8601Python.format(Date()))
        ]
        let req = DynamoDB.UpdateItemInput(
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

    public func addReceiptWordLabels(_ labels: [ReceiptWordLabel]) async throws {
        guard !labels.isEmpty else { return }

        // BatchWrite cannot express a condition and could replace a human-VALID
        // label with a new PENDING inference. Create each exact label key once.
        // Retrying after a partial network failure is safe because collisions
        // are treated as successful idempotent replays.
        let maximumConcurrentWrites = 8
        for start in stride(
            from: 0,
            to: labels.count,
            by: maximumConcurrentWrites
        ) {
            let end = min(start + maximumConcurrentWrites, labels.count)
            try await withThrowingTaskGroup(of: Void.self) { group in
                for label in labels[start..<end] {
                    group.addTask {
                        let item = Self.convertToAttributeValues(
                            label.toDynamoItemDict()
                        )
                        let request = DynamoDB.PutItemInput(
                            conditionExpression:
                                "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                            item: item,
                            tableName: self.tableName
                        )
                        do {
                            _ = try await self.dynamo.putItem(request)
                        } catch let error as DynamoDBErrorType
                            where error.errorCode == "ConditionalCheckFailedException" {
                            // Existing PENDING or human-VALID rows win.
                        }
                    }
                }
                try await group.waitForAll()
            }
        }
    }

    /// Convert dictionary to DynamoDB.AttributeValue format
    private static func convertToAttributeValues(_ dict: [String: Any]) -> [String: DynamoDB.AttributeValue] {
        var result: [String: DynamoDB.AttributeValue] = [:]
        for (key, value) in dict {
            if let str = value as? String {
                result[key] = .s(str)
            } else if let num = value as? Float {
                result[key] = .n(String(num))
            } else if let num = value as? Int {
                result[key] = .n(String(num))
            } else if let num = value as? Double {
                result[key] = .n(String(num))
            } else if value is NSNull || (value as AnyObject) is NSNull {
                result[key] = .null(true)
            } else {
                // For nil optionals cast as Any, they become NSNull
                let mirror = Mirror(reflecting: value)
                if mirror.displayStyle == .optional && mirror.children.isEmpty {
                    result[key] = .null(true)
                }
            }
        }
        return result
    }

    private static func decodeOCRJob(_ attrs: [String: DynamoDB.AttributeValue]) throws -> OCRJob {
        func getS(_ key: String) throws -> String {
            guard case .s(let v)? = attrs[key] else { throw DynamoMapError.missing(key) }
            return v
        }
        func getOptionalS(_ key: String) -> String? {
            guard case .s(let v)? = attrs[key] else { return nil }
            return v
        }
        func getOptionalInt(_ key: String) -> Int? {
            guard case .n(let n)? = attrs[key] else { return nil }
            return Int(n)
        }
        func getOptionalRegion(_ key: String) throws -> ReOCRRegion? {
            guard let raw = attrs[key] else { return nil }
            switch raw {
            case .null(let isNull):
                return isNull ? nil : nil
            case .m(let map):
                func readDouble(_ field: String) throws -> Double {
                    guard case .n(let n)? = map[field], let value = Double(n) else {
                        throw DynamoMapError.invalid("\(key).\(field)")
                    }
                    return value
                }
                return try ReOCRRegion(
                    x: readDouble("x"),
                    y: readDouble("y"),
                    width: readDouble("width"),
                    height: readDouble("height")
                )
            default:
                throw DynamoMapError.invalid(key)
            }
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
        let jobTypeStr = getOptionalS("job_type") ?? OCRJobType.firstPass.rawValue
        guard let createdAt = ISO8601Python.parse(createdAtStr) else { throw DynamoMapError.invalid("created_at") }
        guard let updatedAt = ISO8601Python.parse(updatedAtStr) ?? ISO8601Python.parse(createdAtStr) else { throw DynamoMapError.invalid("updated_at") }
        guard let status = OCRStatus(rawValue: statusStr) else { throw DynamoMapError.invalid("status") }
        let jobType = OCRJobType(rawValue: jobTypeStr) ?? .firstPass
        return OCRJob(
            imageId: imageId,
            jobId: jobId,
            s3Bucket: s3Bucket,
            s3Key: s3Key,
            createdAt: createdAt,
            updatedAt: updatedAt,
            status: status,
            jobType: jobType,
            receiptId: getOptionalInt("receipt_id"),
            reocrRegion: try getOptionalRegion("reocr_region"),
            reocrReason: getOptionalS("reocr_reason")
        )
    }
}

#if os(macOS)
extension SotoDynamoClient: KnownReceiptEvidenceProviding {
    public func evidence(
        for references: Set<ReceiptReference>
    ) async throws -> [ReceiptReference: KnownReceiptEvidence] {
        var result: [ReceiptReference: KnownReceiptEvidence] = [:]
        for reference in references.sorted(by: {
            $0.imageID == $1.imageID
                ? $0.receiptID < $1.receiptID
                : $0.imageID < $1.imageID
        }) {
            let partition = "IMAGE#\(reference.imageID)"
            let receiptPrefix = "RECEIPT#" + String(format: "%05d", reference.receiptID)

            let placeRequest = DynamoDB.GetItemInput(
                consistentRead: true,
                key: [
                    "PK": .s(partition),
                    "SK": .s("\(receiptPrefix)#PLACE")
                ],
                tableName: tableName
            )
            let place = try await dynamo.getItem(placeRequest).item

            var validSectionByLine: [Int: String] = [:]
            var lastKey: [String: DynamoDB.AttributeValue]? = nil
            repeat {
                let request = DynamoDB.QueryInput(
                    consistentRead: true,
                    exclusiveStartKey: lastKey,
                    expressionAttributeNames: [
                        "#pk": "PK",
                        "#sk": "SK",
                        "#status": "validation_status"
                    ],
                    expressionAttributeValues: [
                        ":pk": .s(partition),
                        ":prefix": .s("\(receiptPrefix)#SECTION#"),
                        ":valid": .s("VALID")
                    ],
                    filterExpression: "#status = :valid",
                    keyConditionExpression: "#pk = :pk AND begins_with(#sk, :prefix)",
                    tableName: tableName
                )
                let response = try await dynamo.query(request)
                for item in response.items ?? [] {
                    guard
                        case .s(let section)? = item["section_type"],
                        case .l(let rawLineIDs)? = item["line_ids"]
                    else { continue }
                    for value in rawLineIDs {
                        guard case .n(let raw) = value, let lineID = Int(raw) else {
                            continue
                        }
                        validSectionByLine[lineID] = section
                    }
                }
                lastKey = response.lastEvaluatedKey
            } while lastKey?.isEmpty == false

            func string(_ key: String) -> String? {
                guard case .s(let value)? = place?[key] else { return nil }
                return value
            }
            func number(_ key: String) -> Double? {
                guard case .n(let value)? = place?[key] else { return nil }
                return Double(value)
            }
            result[reference] = KnownReceiptEvidence(
                reference: reference,
                placeID: string("place_id"),
                merchantName: string("merchant_name"),
                formattedAddress: string("formatted_address"),
                phoneNumber: string("phone_number"),
                website: string("website"),
                placeValidationStatus: string("validation_status"),
                placeConfidence: number("confidence"),
                validSectionByLine: validSectionByLine
            )
        }
        return result
    }
}
#endif
