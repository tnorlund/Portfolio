import Foundation

#if os(macOS)
import CoreGraphics

// MARK: - Receipt Bounds

/// Represents the 4 corner points of a detected receipt in normalized coordinates.
///
/// Coordinates are normalized (0-1 range) where:
/// - x=0 is left edge, x=1 is right edge
/// - y=0 is bottom edge, y=1 is top edge (standard math coordinates)
public struct ReceiptBounds: Codable {
    public let topLeft: CodablePoint
    public let topRight: CodablePoint
    public let bottomRight: CodablePoint
    public let bottomLeft: CodablePoint

    public init(
        topLeft: CodablePoint,
        topRight: CodablePoint,
        bottomRight: CodablePoint,
        bottomLeft: CodablePoint
    ) {
        self.topLeft = topLeft
        self.topRight = topRight
        self.bottomRight = bottomRight
        self.bottomLeft = bottomLeft
    }

    /// Create from tuple array [TL, TR, BR, BL] in normalized coordinates
    public init?(corners: [(CGFloat, CGFloat)]) {
        guard corners.count == 4 else { return nil }
        self.topLeft = CodablePoint(x: corners[0].0, y: corners[0].1)
        self.topRight = CodablePoint(x: corners[1].0, y: corners[1].1)
        self.bottomRight = CodablePoint(x: corners[2].0, y: corners[2].1)
        self.bottomLeft = CodablePoint(x: corners[3].0, y: corners[3].1)
    }

    private enum CodingKeys: String, CodingKey {
        case topLeft = "top_left"
        case topRight = "top_right"
        case bottomRight = "bottom_right"
        case bottomLeft = "bottom_left"
    }
}

// MARK: - Processed Receipt

/// Represents a fully processed receipt with warped image data.
///
/// This is the output of the ReceiptProcessor and contains everything
/// needed to upload the receipt to S3 and create DynamoDB entities.
public struct ProcessedReceipt {
    /// Cluster ID from DBSCAN clustering (used as receipt_id)
    public let clusterId: Int

    /// Bounding box corners in normalized coordinates
    public let bounds: ReceiptBounds

    /// The perspective-corrected/warped receipt image
    public let warpedImage: CGImage

    /// Width of the warped image in pixels
    public let warpedWidth: Int

    /// Height of the warped image in pixels
    public let warpedHeight: Int

    /// Line indices from the original lines array that belong to this receipt
    public let lineIndices: [Int]

    public init(
        clusterId: Int,
        bounds: ReceiptBounds,
        warpedImage: CGImage,
        warpedWidth: Int,
        warpedHeight: Int,
        lineIndices: [Int]
    ) {
        self.clusterId = clusterId
        self.bounds = bounds
        self.warpedImage = warpedImage
        self.warpedWidth = warpedWidth
        self.warpedHeight = warpedHeight
        self.lineIndices = lineIndices
    }
}

// MARK: - Receipt Output (for JSON serialization)

/// JSON-serializable representation of a processed receipt.
///
/// This is included in the OCR output JSON and contains the receipt
/// metadata and OCR results from the warped image.
public struct ReceiptOutput: Codable {
    /// Cluster ID (used as receipt_id)
    public let clusterId: Int

    /// Bounding box corners in normalized coordinates
    public let bounds: ReceiptBounds

    /// Width of the warped receipt image
    public let warpedWidth: Int

    /// Height of the warped receipt image
    public let warpedHeight: Int

    /// S3 key where the warped image was uploaded (set after upload)
    public var s3Key: String?

    /// Line indices from the original image that belong to this receipt
    public let lineIndices: [Int]

    /// OCR results from the warped receipt image (REFINEMENT)
    public let lines: [Line]?

    public init(
        clusterId: Int,
        bounds: ReceiptBounds,
        warpedWidth: Int,
        warpedHeight: Int,
        s3Key: String? = nil,
        lineIndices: [Int],
        lines: [Line]? = nil
    ) {
        self.clusterId = clusterId
        self.bounds = bounds
        self.warpedWidth = warpedWidth
        self.warpedHeight = warpedHeight
        self.s3Key = s3Key
        self.lineIndices = lineIndices
        self.lines = lines
    }

    /// Create from a ProcessedReceipt
    public init(from processed: ProcessedReceipt, s3Key: String? = nil, lines: [Line]? = nil) {
        self.clusterId = processed.clusterId
        self.bounds = processed.bounds
        self.warpedWidth = processed.warpedWidth
        self.warpedHeight = processed.warpedHeight
        self.s3Key = s3Key
        self.lineIndices = processed.lineIndices
        self.lines = lines
    }

    private enum CodingKeys: String, CodingKey {
        case clusterId = "cluster_id"
        case bounds
        case warpedWidth = "warped_width"
        case warpedHeight = "warped_height"
        case s3Key = "s3_key"
        case lineIndices = "line_indices"
        case lines
    }
}

// MARK: - Geometry Result

/// Overall geometry processing result for an image.
///
/// This is added to the OCR output JSON and contains all detected receipts.
public struct GeometryResult: Codable {
    /// The image type that was processed
    public let imageType: ImageType

    /// Array of detected and processed receipts
    public let receipts: [ReceiptOutput]

    public init(imageType: ImageType, receipts: [ReceiptOutput]) {
        self.imageType = imageType
        self.receipts = receipts
    }

    private enum CodingKeys: String, CodingKey {
        case imageType = "image_type"
        case receipts
    }
}

#endif
