import Foundation
import SotoDynamoDB

/// DynamoDB item serialization for worker-produced receipt structure.
///
/// Mirrors the Python entity writers exactly — `ReceiptSection.to_item()`
/// and `ReceiptLineItem.to_item()` in `receipt_dynamo` — so rows written by
/// the Mac worker are byte-compatible with rows written by the cloud. The
/// contract is pinned on both sides of the boundary by
/// `Tests/ReceiptOCRCoreTests/Fixtures/swift_dynamo_items_contract.json`:
/// a Swift test asserts this serializer reproduces the fixture, and
/// `infra/upload_images/container_ocr/handler/tests/`
/// `test_swift_dynamo_items_contract.py` asserts `item_to_receipt_section`
/// / `item_to_receipt_line_item` parse it back into valid entities.
public enum ReceiptStructureItems {
    /// Mirror of `ReceiptSection.to_item()` for a worker-produced section:
    /// PENDING validation status, worker provenance, no GSI keys (the
    /// section schema has none).
    public static func sectionItem(
        imageId: String,
        receiptId: Int,
        section: ReceiptSectionPayload,
        createdAt: Date
    ) -> [String: DynamoDB.AttributeValue] {
        [
            "PK": .s("IMAGE#\(imageId)"),
            "SK": .s(
                String(
                    format: "RECEIPT#%05d#SECTION#%@",
                    receiptId, section.sectionType
                )
            ),
            "TYPE": .s("RECEIPT_SECTION"),
            "section_type": .s(section.sectionType),
            "line_ids": .l(section.lineIds.map { .n(String($0)) }),
            "created_at": .s(ISO8601Python.format(createdAt)),
            "confidence": .n(String(section.confidence)),
            "model_source": .s(section.modelSource),
            "validation_status": .s("PENDING"),
            "row_ids": .l(section.rowIds.map { .n(String($0)) }),
        ]
    }

    /// Mirror of `ReceiptLineItem.to_item()` for a worker-produced row.
    ///
    /// `merchantName` is nil on the single-pass path — merchant
    /// resolution is cloud-side and has not run at ingest — and the
    /// sparse-GSI rule skips exactly that case. The refine pass runs
    /// AFTER resolution and carries the merchant on its job, so it
    /// passes one through: without it the refined put would replace
    /// merchant-aware cloud rows with rows missing `merchant_name` and
    /// the merchant rollup keys, and this path emits no stream event
    /// that would rebuild them. `collapsed_banding` is always false —
    /// the Python band-block decoder pins it false too.
    public static func lineItemItem(
        imageId: String,
        receiptId: Int,
        item payload: ReceiptLineItemPayload,
        extractedAt: Date,
        baselineFiguresAgreeing: Int?,
        merchantName: String? = nil
    ) -> [String: DynamoDB.AttributeValue] {
        var item: [String: DynamoDB.AttributeValue] = [
            "PK": .s("IMAGE#\(imageId)"),
            "SK": .s(
                String(
                    format: "RECEIPT#%05d#LINE_ITEM#%05d",
                    receiptId, payload.itemIndex
                )
            ),
            "TYPE": .s("RECEIPT_LINE_ITEM"),
            "name": .s(payload.name),
            "price": .s(String(format: "%.2f", payload.price)),
            "line_ids": .l(payload.lineIds.map { .n(String($0)) }),
            "extractor_version": .s(payload.extractorVersion),
            "extracted_at": .s(ISO8601Python.format(extractedAt)),
            "is_discount": .bool(payload.isDiscount),
            "collapsed_banding": .bool(false),
            "name_quality": .s(payload.nameQuality),
            "raw_text": .s(payload.rawText ?? ""),
            "source_section_status": .s("PENDING"),
            "source_model_source": .s(payload.modelSource),
            "reconciliation_status": .s(payload.reconciliationStatus),
        ]
        if let quantity = payload.quantity {
            item["quantity"] = .n(String(quantity))
        }
        if let unitPrice = payload.unitPrice {
            item["unit_price"] = .n(String(unitPrice))
        }
        if let grade = baselineFiguresAgreeing {
            item["baseline_figures_agreeing"] = .n(String(grade))
        }
        if let merchant = merchantName {
            // `to_item()` writes merchant_name whenever the field is
            // set, INCLUDING on low-quality rows; only the GSI pair is
            // gated, by `gsi1_key`'s sparse rule. Keeping that split
            // means a low-quality refined row still carries the
            // merchant the cloud resolved for it. The empty-name guard
            // is belt-and-braces: the decoder already stamps
            // name_quality "low" when it finds no real name, so an
            // empty GSI1SK product segment is unreachable.
            item["merchant_name"] = .s(merchant)
            if !merchant.isEmpty, payload.nameQuality != "low",
                !payload.name.isEmpty
            {
                item["GSI1PK"] = .s(
                    "MERCHANT#\(MerchantKeys.slugifyMerchant(merchant))"
                )
                item["GSI1SK"] = .s(
                    String(
                        format: "LINE_ITEM#%@#%@#%05d#%05d",
                        MerchantKeys.normalizeProductText(payload.name),
                        imageId, receiptId, payload.itemIndex
                    )
                )
            }
        }
        return item
    }
}
