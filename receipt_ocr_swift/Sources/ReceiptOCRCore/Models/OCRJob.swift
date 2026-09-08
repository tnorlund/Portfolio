import Foundation

public enum OCRJobType: String {
    case firstPass = "FIRST_PASS"
    case refinement = "REFINEMENT"
    case regionalReocr = "REGIONAL_REOCR"
    /// Second worker pass: re-decode line items once the receipt's
    /// summary exists. The job's s3_key points at the receipt's ORIGINAL
    /// OCR-result JSON, never an image — the refine decode must run over
    /// the same word universe as the persisted rows.
    case lineItemRefine = "LINE_ITEM_REFINE"
}

/// Preprocessing strategy applied to the cropped region before Vision OCR.
///
/// Contract with the Python side (which writes these OCRJob fields):
/// `reocr_strategy` is one of "plain" | "invert" | "deskew" | "upscale2x";
/// an absent or unrecognized value means `plain` (passthrough).
public enum ReOCRStrategy: String, Equatable, CaseIterable, Sendable {
    case plain
    case invert
    case deskew
    case upscale2x
}

public struct ReOCRRegion: Equatable {
    public var x: Double
    public var y: Double
    public var width: Double
    public var height: Double

    public init(x: Double, y: Double, width: Double, height: Double) {
        self.x = x
        self.y = y
        self.width = width
        self.height = height
    }
}

public struct OCRJob: Equatable {
    public var imageId: String
    public var jobId: String
    public var s3Bucket: String
    public var s3Key: String
    public var createdAt: Date
    public var updatedAt: Date
    public var status: OCRStatus
    public var jobType: OCRJobType
    public var receiptId: Int?
    public var reocrRegion: ReOCRRegion?
    public var reocrReason: String?
    /// Preprocess strategy for REGIONAL_REOCR jobs (absent → .plain).
    public var reocrStrategy: ReOCRStrategy
    /// Informational string written by the Python side describing what
    /// triggered/selected the strategy. Passed through untouched.
    public var reocrMechanism: String?
    /// LINE_ITEM_REFINE: the real summary (from ReceiptSummary), carried
    /// on the job so the refine decode uses the graded baseline instead
    /// of the worker's own scanned figures.
    public var refineSummary: LineItemSummary?
    /// LINE_ITEM_REFINE: the resolved merchant, for the section assigner's
    /// merchant-conditioned prior.
    public var refineMerchantName: String?

    public init(
        imageId: String,
        jobId: String,
        s3Bucket: String,
        s3Key: String,
        createdAt: Date,
        updatedAt: Date,
        status: OCRStatus,
        jobType: OCRJobType = .firstPass,
        receiptId: Int? = nil,
        reocrRegion: ReOCRRegion? = nil,
        reocrReason: String? = nil,
        reocrStrategy: ReOCRStrategy = .plain,
        reocrMechanism: String? = nil,
        refineSummary: LineItemSummary? = nil,
        refineMerchantName: String? = nil
    ) {
        self.imageId = imageId
        self.jobId = jobId
        self.s3Bucket = s3Bucket
        self.s3Key = s3Key
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.status = status
        self.jobType = jobType
        self.receiptId = receiptId
        self.reocrRegion = reocrRegion
        self.reocrReason = reocrReason
        self.reocrStrategy = reocrStrategy
        self.reocrMechanism = reocrMechanism
        self.refineSummary = refineSummary
        self.refineMerchantName = refineMerchantName
    }
}

public enum DynamoMapError: Error {
    case missing(String)
    case invalid(String)
}

public extension OCRJob {
    func toItem() -> [String: Any] {
        var item: [String: Any] = [
            "PK": ["S": "IMAGE#\(imageId)"],
            "SK": ["S": "OCR_JOB#\(jobId)"],
            "TYPE": ["S": "OCR_JOB"],
            "s3_bucket": ["S": s3Bucket],
            "s3_key": ["S": s3Key],
            "created_at": ["S": ISO8601Python.format(createdAt)],
            "updated_at": ["S": ISO8601Python.format(updatedAt)],
            "status": ["S": status.rawValue],
            "job_type": ["S": jobType.rawValue]
        ]

        if let receiptId = receiptId {
            item["receipt_id"] = ["N": "\(receiptId)"]
        } else {
            item["receipt_id"] = ["NULL": true]
        }

        if let region = reocrRegion {
            item["reocr_region"] = [
                "M": [
                    "x": ["N": "\(region.x)"],
                    "y": ["N": "\(region.y)"],
                    "width": ["N": "\(region.width)"],
                    "height": ["N": "\(region.height)"]
                ]
            ]
        } else {
            item["reocr_region"] = ["NULL": true]
        }

        if let reason = reocrReason {
            item["reocr_reason"] = ["S": reason]
        } else {
            item["reocr_reason"] = ["NULL": true]
        }

        item["reocr_strategy"] = ["S": reocrStrategy.rawValue]

        if let mechanism = reocrMechanism {
            item["reocr_mechanism"] = ["S": mechanism]
        } else {
            item["reocr_mechanism"] = ["NULL": true]
        }

        if let summary = refineSummary {
            func figure(_ value: Double?) -> [String: Any] {
                if let value { return ["N": "\(value)"] }
                return ["NULL": true]
            }
            item["refine_summary"] = [
                "M": [
                    "subtotal": figure(summary.subtotal),
                    "tax": figure(summary.tax),
                    "grand_total": figure(summary.grandTotal),
                ]
            ]
        } else {
            item["refine_summary"] = ["NULL": true]
        }

        if let merchant = refineMerchantName {
            item["refine_merchant_name"] = ["S": merchant]
        } else {
            item["refine_merchant_name"] = ["NULL": true]
        }

        return item
    }

    static func fromItem(_ item: [String: Any]) throws -> OCRJob {
        func str(_ key: String) throws -> String {
            if let dict = item[key] as? [String: Any], let v = dict["S"] as? String { return v }
            throw DynamoMapError.missing(key)
        }
        func optionalInt(_ key: String) -> Int? {
            guard let dict = item[key] as? [String: Any] else { return nil }
            if let n = dict["N"] as? String { return Int(n) }
            return nil
        }
        func optionalString(_ key: String) -> String? {
            guard let dict = item[key] as? [String: Any] else { return nil }
            return dict["S"] as? String
        }
        func optionalRegion(_ key: String) throws -> ReOCRRegion? {
            guard let dict = item[key] as? [String: Any] else { return nil }
            if let isNull = dict["NULL"] as? Bool, isNull { return nil }
            guard let map = dict["M"] as? [String: Any] else {
                throw DynamoMapError.invalid(key)
            }
            func number(_ field: String) throws -> Double {
                guard let f = map[field] as? [String: Any], let n = f["N"] as? String, let value = Double(n) else {
                    throw DynamoMapError.invalid("\(key).\(field)")
                }
                return value
            }
            return try ReOCRRegion(
                x: number("x"),
                y: number("y"),
                width: number("width"),
                height: number("height")
            )
        }

        func optionalSummary(_ key: String) -> LineItemSummary? {
            guard let dict = item[key] as? [String: Any] else { return nil }
            if let isNull = dict["NULL"] as? Bool, isNull { return nil }
            guard let map = dict["M"] as? [String: Any] else { return nil }
            func figure(_ field: String) -> Double? {
                guard let f = map[field] as? [String: Any],
                      let n = f["N"] as? String else { return nil }
                return Double(n)
            }
            return LineItemSummary(
                subtotal: figure("subtotal"),
                tax: figure("tax"),
                grandTotal: figure("grand_total")
            )
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
        let jobTypeStr = (try? str("job_type")) ?? OCRJobType.firstPass.rawValue
        guard let createdAt = ISO8601Python.parse(createdAtStr) else { throw DynamoMapError.invalid("created_at") }
        let parsedUpdated = ISO8601Python.parse(updatedAtStr) ?? createdAt
        guard let status = OCRStatus(rawValue: statusStr) else { throw DynamoMapError.invalid("status") }
        let jobType = OCRJobType(rawValue: jobTypeStr) ?? .firstPass
        return OCRJob(
            imageId: imageId,
            jobId: jobId,
            s3Bucket: s3Bucket,
            s3Key: s3Key,
            createdAt: createdAt,
            updatedAt: parsedUpdated,
            status: status,
            jobType: jobType,
            receiptId: optionalInt("receipt_id"),
            reocrRegion: try optionalRegion("reocr_region"),
            reocrReason: optionalString("reocr_reason"),
            // Contract: absent (or unrecognized) reocr_strategy → plain.
            reocrStrategy: optionalString("reocr_strategy").flatMap(ReOCRStrategy.init(rawValue:)) ?? .plain,
            reocrMechanism: optionalString("reocr_mechanism"),
            refineSummary: optionalSummary("refine_summary"),
            refineMerchantName: optionalString("refine_merchant_name")
        )
    }
}
