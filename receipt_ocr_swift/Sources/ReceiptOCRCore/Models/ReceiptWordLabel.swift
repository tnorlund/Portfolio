import Foundation

#if os(macOS)

/// Validation status for receipt word labels.
/// Matches Python ValidationStatus enum in receipt_dynamo/constants.py
public enum ValidationStatus: String {
    case none = "NONE"
    case pending = "PENDING"
    case valid = "VALID"
    case invalid = "INVALID"
    case needsReview = "NEEDS_REVIEW"
}

/// Represents a label for a word in a receipt line, matching DynamoDB schema.
/// This mirrors the Python ReceiptWordLabel entity in receipt_dynamo.
public struct ReceiptWordLabel: Equatable {
    public let imageId: String
    public let receiptId: Int
    public let lineId: Int
    public let wordId: Int
    public let label: String           // Stripped label (e.g., "MERCHANT_NAME")
    public let reasoning: String?
    public let timestampAdded: Date
    public let validationStatus: ValidationStatus
    public let labelProposedBy: String
    public let labelConsolidatedFrom: String?
    public let confidence: Float       // Confidence score from LayoutLM

    public init(
        imageId: String,
        receiptId: Int,
        lineId: Int,
        wordId: Int,
        label: String,
        reasoning: String? = nil,
        timestampAdded: Date = Date(),
        validationStatus: ValidationStatus = .pending,
        labelProposedBy: String = "auto-inference",
        labelConsolidatedFrom: String? = nil,
        confidence: Float = 0.0
    ) {
        self.imageId = imageId
        self.receiptId = receiptId
        self.lineId = lineId
        self.wordId = wordId
        self.label = label.uppercased()
        self.reasoning = reasoning
        self.timestampAdded = timestampAdded
        self.validationStatus = validationStatus
        self.labelProposedBy = labelProposedBy
        self.labelConsolidatedFrom = labelConsolidatedFrom
        self.confidence = confidence
    }

    // MARK: - DynamoDB Keys

    /// Primary key (PK, SK) matching Python entity format
    public var primaryKey: (pk: String, sk: String) {
        (
            pk: "IMAGE#\(imageId)",
            sk: String(format: "RECEIPT#%05d#LINE#%05d#WORD#%05d#LABEL#%@",
                      receiptId, lineId, wordId, label)
        )
    }

    /// GSI1 key for querying by label type
    /// GSI1PK is padded to exactly 40 characters with underscores
    public var gsi1Key: (pk: String, sk: String) {
        let prefix = "LABEL#"
        let labelPart = prefix + label
        let paddingNeeded = max(0, 40 - labelPart.count)
        let paddedPK = labelPart + String(repeating: "_", count: paddingNeeded)
        return (
            pk: paddedPK,
            sk: String(format: "IMAGE#%@#RECEIPT#%05d#LINE#%05d#WORD#%05d",
                      imageId, receiptId, lineId, wordId)
        )
    }

    /// GSI2 key for querying by receipt
    public var gsi2Key: (pk: String, sk: String) {
        (
            pk: "RECEIPT",
            sk: String(format: "IMAGE#%@#RECEIPT#%05d#LINE#%05d#WORD#%05d",
                      imageId, receiptId, lineId, wordId)
        )
    }

    /// GSI3 key for querying by validation status
    public var gsi3Key: (pk: String, sk: String) {
        (
            pk: "VALIDATION_STATUS#\(validationStatus.rawValue)",
            sk: String(format: "IMAGE#%@#RECEIPT#%05d#LINE#%05d#WORD#%05d#LABEL#%@",
                      imageId, receiptId, lineId, wordId, label)
        )
    }

    /// Convert to DynamoDB item dictionary for batch write.
    /// Returns a dictionary with keys and string/number values.
    /// The actual DynamoDB.AttributeValue conversion happens in SotoDynamoClient.
    public func toDynamoItemDict() -> [String: Any] {
        let keys = primaryKey
        let gsi1 = gsi1Key
        let gsi2 = gsi2Key
        let gsi3 = gsi3Key

        var item: [String: Any] = [
            "PK": keys.pk,
            "SK": keys.sk,
            "GSI1PK": gsi1.pk,
            "GSI1SK": gsi1.sk,
            "GSI2PK": gsi2.pk,
            "GSI2SK": gsi2.sk,
            "GSI3PK": gsi3.pk,
            "GSI3SK": gsi3.sk,
            "TYPE": "RECEIPT_WORD_LABEL",
            "timestamp_added": ISO8601Python.format(timestampAdded),
            "validation_status": validationStatus.rawValue,
            "label_proposed_by": labelProposedBy,
            "confidence": confidence
        ]

        // Optional fields
        item["reasoning"] = reasoning as Any
        item["label_consolidated_from"] = labelConsolidatedFrom as Any

        return item
    }

    // MARK: - Label Helpers

    /// Strip B- or I- BIO prefix from a label
    /// e.g., "B-MERCHANT_NAME" -> "MERCHANT_NAME"
    public static func stripBIOPrefix(_ label: String) -> String {
        if label.hasPrefix("B-") || label.hasPrefix("I-") {
            return String(label.dropFirst(2))
        }
        return label
    }

    /// Create ReceiptWordLabel array from LayoutLM predictions
    public static func fromLinePredictions(
        predictions: [LinePrediction],
        imageId: String,
        receiptId: Int
    ) -> [ReceiptWordLabel] {
        var labels: [ReceiptWordLabel] = []
        let timestamp = Date()

        for (lineIndex, linePred) in predictions.enumerated() {
            let lineId = lineIndex + 1  // 1-indexed to match Python

            // Ensure parallel arrays are same length
            let count = min(linePred.tokens.count, linePred.labels.count, linePred.confidences.count)

            for wordIndex in 0..<count {
                let wordId = wordIndex + 1  // 1-indexed to match Python
                let rawLabel = linePred.labels[wordIndex]
                let confidence = linePred.confidences[wordIndex]

                // Strip B-/I- prefix
                let strippedLabel = stripBIOPrefix(rawLabel)

                let label = ReceiptWordLabel(
                    imageId: imageId,
                    receiptId: receiptId,
                    lineId: lineId,
                    wordId: wordId,
                    label: strippedLabel,
                    reasoning: nil,
                    timestampAdded: timestamp,
                    validationStatus: .pending,
                    labelProposedBy: "auto-inference",
                    labelConsolidatedFrom: nil,
                    confidence: confidence
                )
                labels.append(label)
            }
        }

        return labels
    }
}

#endif
