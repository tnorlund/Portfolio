import Foundation

public struct OCRRoutingDecision: Equatable {
    public var imageId: String
    public var jobId: String
    public var s3Bucket: String
    public var s3Key: String
    public var createdAt: Date
    public var updatedAt: Date?
    public var receiptCount: Int
    public var status: OCRStatus

    public init(
        imageId: String,
        jobId: String,
        s3Bucket: String,
        s3Key: String,
        createdAt: Date,
        updatedAt: Date?,
        receiptCount: Int,
        status: OCRStatus
    ) {
        self.imageId = imageId
        self.jobId = jobId
        self.s3Bucket = s3Bucket
        self.s3Key = s3Key
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.receiptCount = receiptCount
        self.status = status
    }
}

public extension OCRRoutingDecision {
    func toItem() -> [String: Any] {
        var updatedValue: [String: Any]
        if let updatedAt = updatedAt {
            updatedValue = ["S": ISO8601Python.format(updatedAt)]
        } else {
            updatedValue = ["NULL": true]
        }
        return [
            "PK": ["S": "IMAGE#\(imageId)"],
            "SK": ["S": "ROUTING#\(jobId)"],
            "TYPE": ["S": "OCR_ROUTING_DECISION"],
            "GSI1PK": ["S": "OCR_ROUTING_DECISION_STATUS#\(status.rawValue)"],
            "GSI1SK": ["S": "ROUTING#\(jobId)"],
            "s3_bucket": ["S": s3Bucket],
            "s3_key": ["S": s3Key],
            "created_at": ["S": ISO8601Python.format(createdAt)],
            "updated_at": updatedValue,
            "receipt_count": ["N": String(receiptCount)],
            "status": ["S": status.rawValue]
        ]
    }

    static func fromItem(_ item: [String: Any]) throws -> OCRRoutingDecision {
        func str(_ key: String) throws -> String {
            if let dict = item[key] as? [String: Any], let v = dict["S"] as? String { return v }
            throw DynamoMapError.missing(key)
        }
        let pk = try str("PK")
        let sk = try str("SK")
        guard let imageId = pk.split(separator: "#").last.map(String.init) else { throw DynamoMapError.invalid("PK") }
        guard let jobId = sk.split(separator: "#").last.map(String.init) else { throw DynamoMapError.invalid("SK") }
        let s3Bucket = try str("s3_bucket")
        let s3Key = try str("s3_key")
        let createdAtStr = try str("created_at")
        let statusStr = try str("status")
        guard let createdAt = ISO8601Python.parse(createdAtStr) else { throw DynamoMapError.invalid("created_at") }
        // updated_at may be NULL or S
        var updatedAt: Date? = nil
        if let updatedDict = item["updated_at"] as? [String: Any] {
            if let s = updatedDict["S"] as? String { updatedAt = ISO8601Python.parse(s) }
        }
        guard let status = OCRStatus(rawValue: statusStr) else { throw DynamoMapError.invalid("status") }
        // receipt_count as number
        guard let rcDict = item["receipt_count"] as? [String: Any], let rcStr = rcDict["N"] as? String, let rc = Int(rcStr) else {
            throw DynamoMapError.missing("receipt_count")
        }
        return OCRRoutingDecision(
            imageId: imageId,
            jobId: jobId,
            s3Bucket: s3Bucket,
            s3Key: s3Key,
            createdAt: createdAt,
            updatedAt: updatedAt,
            receiptCount: rc,
            status: status
        )
    }
}


