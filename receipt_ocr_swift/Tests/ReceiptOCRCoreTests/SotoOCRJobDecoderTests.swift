import Foundation
import SotoDynamoDB
import Testing

@testable import ReceiptOCRCore

/// The PRODUCTION OCRJob read path is SotoDynamoClient.decodeOCRJob, not
/// OCRJob.fromItem — the two decoders must agree on the smart re-OCR
/// contract. The strategy fields were originally added only to fromItem,
/// so every live job decoded as .plain while the fromItem tests stayed
/// green; this suite pins the Soto path directly.
@Suite struct SotoOCRJobDecoderTests {

    private func baseAttrs(
        strategy: DynamoDB.AttributeValue? = nil,
        mechanism: DynamoDB.AttributeValue? = nil
    ) -> [String: DynamoDB.AttributeValue] {
        var attrs: [String: DynamoDB.AttributeValue] = [
            "PK": .s("IMAGE#img-1"),
            "SK": .s("OCR_JOB#job-1"),
            "s3_bucket": .s("b"),
            "s3_key": .s("raw/img.png"),
            "created_at": .s("2026-08-04T00:00:00.000+00:00"),
            "updated_at": .s("2026-08-04T00:00:00.000+00:00"),
            "status": .s("PENDING"),
            "job_type": .s("REGIONAL_REOCR"),
        ]
        if let strategy = strategy { attrs["reocr_strategy"] = strategy }
        if let mechanism = mechanism { attrs["reocr_mechanism"] = mechanism }
        return attrs
    }

    @Test func strategyAndMechanismDecodeFromSotoAttributes() throws {
        let job = try SotoDynamoClient.decodeOCRJob(
            baseAttrs(strategy: .s("invert"), mechanism: .s("reverse-video"))
        )
        #expect(job.reocrStrategy == .invert)
        #expect(job.reocrMechanism == "reverse-video")
    }

    @Test func everyStrategyStringDecodes() throws {
        for strategy in ReOCRStrategy.allCases {
            let job = try SotoDynamoClient.decodeOCRJob(baseAttrs(strategy: .s(strategy.rawValue)))
            #expect(job.reocrStrategy == strategy, "raw=\(strategy.rawValue)")
        }
    }

    @Test func absentStrategyDefaultsToPlain() throws {
        let job = try SotoDynamoClient.decodeOCRJob(baseAttrs())
        #expect(job.reocrStrategy == .plain)
        #expect(job.reocrMechanism == nil)
    }

    @Test func nullOrUnrecognizedStrategyFallsBackToPlain() throws {
        let nullJob = try SotoDynamoClient.decodeOCRJob(
            baseAttrs(strategy: .null(true), mechanism: .null(true))
        )
        #expect(nullJob.reocrStrategy == .plain)
        #expect(nullJob.reocrMechanism == nil)

        let bogusJob = try SotoDynamoClient.decodeOCRJob(baseAttrs(strategy: .s("sharpen4x")))
        #expect(bogusJob.reocrStrategy == .plain)
    }

    private func refineAttrs(
        summary: DynamoDB.AttributeValue? = nil,
        merchant: DynamoDB.AttributeValue? = nil
    ) -> [String: DynamoDB.AttributeValue] {
        var attrs = baseAttrs()
        attrs["job_type"] = .s("LINE_ITEM_REFINE")
        attrs["receipt_id"] = .n("2")
        if let summary = summary { attrs["refine_summary"] = summary }
        if let merchant = merchant { attrs["refine_merchant_name"] = merchant }
        return attrs
    }

    /// The refine fields walked into the same trap as the strategy
    /// fields: a job decoded here without its summary re-grades against
    /// the worker's own scanned figures instead of the printed ones the
    /// trigger carried, which is the entire point of the pass.
    @Test func refineSummaryAndMerchantDecodeFromSotoAttributes() throws {
        let job = try SotoDynamoClient.decodeOCRJob(
            refineAttrs(
                summary: .m([
                    "subtotal": .n("3.99"),
                    "tax": .n("0.35"),
                    "grand_total": .n("4.34"),
                ]),
                merchant: .s("SPROUTS FARMERS MARKET")
            )
        )
        #expect(job.jobType == .lineItemRefine)
        #expect(job.receiptId == 2)
        #expect(job.refineSummary?.subtotal == 3.99)
        #expect(job.refineSummary?.tax == 0.35)
        #expect(job.refineSummary?.grandTotal == 4.34)
        #expect(job.refineMerchantName == "SPROUTS FARMERS MARKET")
    }

    /// A figure the receipt never printed stays nil — collapsing it to
    /// zero would hand the reconciler a baseline the receipt never had.
    @Test func partialRefineSummaryKeepsMissingFiguresNil() throws {
        let job = try SotoDynamoClient.decodeOCRJob(
            refineAttrs(
                summary: .m([
                    "subtotal": .n("12.50"),
                    "tax": .null(true),
                ])
            )
        )
        #expect(job.refineSummary?.subtotal == 12.50)
        #expect(job.refineSummary?.tax == nil)
        #expect(job.refineSummary?.grandTotal == nil)
        #expect(job.refineMerchantName == nil)
    }

    @Test func absentOrNullRefineFieldsDecodeAsNil() throws {
        let absent = try SotoDynamoClient.decodeOCRJob(refineAttrs())
        #expect(absent.refineSummary == nil)
        #expect(absent.refineMerchantName == nil)

        let nulled = try SotoDynamoClient.decodeOCRJob(
            refineAttrs(summary: .null(true), merchant: .null(true))
        )
        #expect(nulled.refineSummary == nil)
        #expect(nulled.refineMerchantName == nil)
    }
}
