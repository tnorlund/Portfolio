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
        do {
            _ = try await s3.multipartDownload(
                req,
                logger: AWSClient.loggingDisabled,
                on: nil
            ) { byteBuffer, _, _ in
                data.append(contentsOf: byteBuffer.readableBytesView)
            }
        } catch let error as AWSErrorType where error.context?.responseCode == .notFound {
            throw ObjectNotFoundError(bucket: bucket, key: key)
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
        try await batchPutItems(
            labels.map {
                Self.convertToAttributeValues($0.toDynamoItemDict())
            }
        )
    }

    public func addReceiptSections(
        imageId: String, receiptId: Int,
        sections: [ReceiptSectionPayload], createdAt: Date
    ) async throws {
        try await batchPutItems(
            sections.map {
                ReceiptStructureItems.sectionItem(
                    imageId: imageId, receiptId: receiptId,
                    section: $0, createdAt: createdAt
                )
            }
        )
    }

    public func addReceiptLineItems(
        imageId: String, receiptId: Int,
        items: [ReceiptLineItemPayload], extractedAt: Date,
        baselineFiguresAgreeing: Int?
    ) async throws {
        try await batchPutItems(
            items.map {
                ReceiptStructureItems.lineItemItem(
                    imageId: imageId, receiptId: receiptId,
                    item: $0, extractedAt: extractedAt,
                    baselineFiguresAgreeing: baselineFiguresAgreeing
                )
            }
        )
    }

    public func replaceReceiptLineItems(
        imageId: String, receiptId: Int,
        items: [ReceiptLineItemPayload], extractedAt: Date,
        baselineFiguresAgreeing: Int?, merchantName: String?
    ) async throws {
        let newItems = items.map {
            ReceiptStructureItems.lineItemItem(
                imageId: imageId, receiptId: receiptId,
                item: $0, extractedAt: extractedAt,
                baselineFiguresAgreeing: baselineFiguresAgreeing,
                merchantName: merchantName
            )
        }
        let existingKeys = try await receiptLineItemKeys(
            imageId: imageId, receiptId: receiptId
        )
        try await batchPutItems(newItems)

        // Delete AFTER the put so a failure between the two leaves the
        // old rows in place rather than an empty item set.
        let written = Set(newItems.compactMap { Self.sortKey($0) })
        let stale = existingKeys.filter { key in
            guard let sk = Self.sortKey(key) else { return false }
            return !written.contains(sk)
        }
        try await batchWrite(
            stale.map { DynamoDB.WriteRequest(deleteRequest: .init(key: $0)) }
        )
    }

    /// Every stored `LINE_ITEM` key for the receipt, keys only.
    private func receiptLineItemKeys(
        imageId: String, receiptId: Int
    ) async throws -> [[String: DynamoDB.AttributeValue]] {
        var keys: [[String: DynamoDB.AttributeValue]] = []
        var startKey: [String: DynamoDB.AttributeValue]?
        repeat {
            let req = DynamoDB.QueryInput(
                exclusiveStartKey: startKey,
                expressionAttributeValues: [
                    ":pk": .s("IMAGE#\(imageId)"),
                    ":sk": .s(
                        String(
                            format: "RECEIPT#%05d#LINE_ITEM#", receiptId
                        )
                    ),
                ],
                keyConditionExpression:
                    "PK = :pk AND begins_with(SK, :sk)",
                projectionExpression: "PK, SK",
                tableName: tableName
            )
            let resp = try await dynamo.query(req)
            keys.append(contentsOf: resp.items ?? [])
            let last = resp.lastEvaluatedKey
            startKey = (last?.isEmpty ?? true) ? nil : last
        } while startKey != nil
        return keys
    }

    private static func sortKey(
        _ item: [String: DynamoDB.AttributeValue]
    ) -> String? {
        if case .s(let sk)? = item["SK"] { return sk }
        return nil
    }

    @discardableResult
    public func addReceiptLineItemsIfWorkerOwned(
        imageId: String, receiptId: Int,
        items: [ReceiptLineItemPayload], extractedAt: Date,
        baselineFiguresAgreeing: Int?
    ) async throws -> Int {
        // One PutItem per row: DynamoDB batch writes cannot carry a
        // condition expression, and the condition is the whole point here.
        // The row is ours to write only if nothing is there yet or the
        // worker stamped what is (see the protocol's ownership rule).
        var skipped = 0
        for payload in items {
            let item = ReceiptStructureItems.lineItemItem(
                imageId: imageId, receiptId: receiptId,
                item: payload, extractedAt: extractedAt,
                baselineFiguresAgreeing: baselineFiguresAgreeing
            )
            let req = DynamoDB.PutItemInput(
                conditionExpression:
                    "attribute_not_exists(PK) OR begins_with(extractor_version, :worker)",
                expressionAttributeValues: [
                    ":worker": .s(swiftWorkerExtractorVersionPrefix)
                ],
                item: item,
                tableName: tableName
            )
            do {
                _ = try await dynamo.putItem(req)
            } catch let error as AWSErrorType
                where error.errorCode
                    == DynamoDBErrorType.conditionalCheckFailedException.errorCode
            {
                // Cloud-owned row: leave it exactly as the pipeline left it.
                skipped += 1
            }
        }
        return skipped
    }

    /// Batch-put with chunking, unprocessed-item retry and backoff —
    /// shared by labels, sections and line items.
    private func batchPutItems(
        _ items: [[String: DynamoDB.AttributeValue]]
    ) async throws {
        try await batchWrite(
            items.map { DynamoDB.WriteRequest(putRequest: .init(item: $0)) }
        )
    }

    /// Chunked BatchWriteItem (puts, deletes, or both) with
    /// unprocessed-item retry and backoff.
    private func batchWrite(
        _ requests: [DynamoDB.WriteRequest]
    ) async throws {
        guard !requests.isEmpty else { return }

        // Process in chunks of 25 (DynamoDB batch write limit)
        let chunkSize = 25
        var remaining = requests[...]

        while !remaining.isEmpty {
            var writeRequests = Array(remaining.prefix(chunkSize))
            remaining = remaining.dropFirst(chunkSize)

            // Retry with exponential backoff until all items are processed
            let maxRetries = 8
            var retryCount = 0
            let baseDelayMs: UInt64 = 50

            while !writeRequests.isEmpty && retryCount < maxRetries {
                let req = DynamoDB.BatchWriteItemInput(requestItems: [tableName: writeRequests])
                let resp = try await dynamo.batchWriteItem(req)

                // Check for unprocessed items
                if let unprocessed = resp.unprocessedItems?[tableName], !unprocessed.isEmpty {
                    writeRequests = unprocessed
                    retryCount += 1

                    // Exponential backoff with jitter
                    let delayMs = baseDelayMs * UInt64(1 << retryCount)
                    let jitter = UInt64.random(in: 0...delayMs / 4)
                    try await Task.sleep(nanoseconds: (delayMs + jitter) * 1_000_000)
                } else {
                    // All items processed
                    writeRequests = []
                }
            }

            // If we still have unprocessed items after max retries, throw an error
            if !writeRequests.isEmpty {
                throw BatchWriteError.unprocessedItems(count: writeRequests.count)
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

    // internal (not private) so the contract test can decode real
    // AttributeValue shapes — the fromItem-based tests missed this path.
    static func decodeOCRJob(_ attrs: [String: DynamoDB.AttributeValue]) throws -> OCRJob {
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
        // Mirrors OCRJob.fromItem's optionalSummary: absent, NULL, or a
        // non-map value all mean "no carried summary"; missing figures
        // inside the map stay nil rather than collapsing to zero.
        func getOptionalSummary(_ key: String) -> LineItemSummary? {
            guard case .m(let map)? = attrs[key] else { return nil }
            func figure(_ field: String) -> Double? {
                guard case .n(let n)? = map[field] else { return nil }
                return Double(n)
            }
            return LineItemSummary(
                subtotal: figure("subtotal"),
                tax: figure("tax"),
                grandTotal: figure("grand_total")
            )
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
            reocrReason: getOptionalS("reocr_reason"),
            // Contract: absent (or unrecognized) -> .plain, mirroring
            // OCRJob.fromItem. This decoder is the PRODUCTION read path —
            // omitting the fields here silently downgraded every strategy
            // to plain while the fromItem-based tests stayed green.
            reocrStrategy: getOptionalS("reocr_strategy").flatMap(ReOCRStrategy.init(rawValue:)) ?? .plain,
            reocrMechanism: getOptionalS("reocr_mechanism"),
            // Same trap as the strategy fields above: a LINE_ITEM_REFINE
            // job decoded without its summary silently re-grades against
            // the worker's own scanned figures instead of the printed
            // ones the trigger carried, which is the entire pass.
            refineSummary: getOptionalSummary("refine_summary"),
            refineMerchantName: getOptionalS("refine_merchant_name")
        )
    }
}

