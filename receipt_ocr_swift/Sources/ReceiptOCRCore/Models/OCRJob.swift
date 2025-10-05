import Foundation

public struct OCRJob: Equatable {
    public var imageId: String
    public var jobId: String
    public var s3Bucket: String
    public var s3Key: String
    public var createdAt: Date
    public var updatedAt: Date
    public var status: OCRStatus

    public init(
        imageId: String,
        jobId: String,
        s3Bucket: String,
        s3Key: String,
        createdAt: Date,
        updatedAt: Date,
        status: OCRStatus
    ) {
        self.imageId = imageId
        self.jobId = jobId
        self.s3Bucket = s3Bucket
        self.s3Key = s3Key
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.status = status
    }
}

public enum DynamoMapError: Error {
    case missing(String)
    case invalid(String)
}

public extension OCRJob {
    func toItem() -> [String: Any] {
        return [
            "PK": ["S": "IMAGE#\(imageId)"],
            "SK": ["S": "OCR_JOB#\(jobId)"],
            "TYPE": ["S": "OCR_JOB"],
            "s3_bucket": ["S": s3Bucket],
            "s3_key": ["S": s3Key],
            "created_at": ["S": ISO8601Python.format(createdAt)],
            "updated_at": ["S": ISO8601Python.format(updatedAt)],
            "status": ["S": status.rawValue]
        ]
    }

    static func fromItem(_ item: [String: Any]) throws -> OCRJob {
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
        let updatedAtStr = try str("updated_at")
        let statusStr = try str("status")
        guard let createdAt = ISO8601Python.parse(createdAtStr) else { throw DynamoMapError.invalid("created_at") }
        let parsedUpdated = ISO8601Python.parse(updatedAtStr) ?? createdAt
        guard let status = OCRStatus(rawValue: statusStr) else { throw DynamoMapError.invalid("status") }
        return OCRJob(
            imageId: imageId,
            jobId: jobId,
            s3Bucket: s3Bucket,
            s3Key: s3Key,
            createdAt: createdAt,
            updatedAt: parsedUpdated,
            status: status
        )
    }
}


