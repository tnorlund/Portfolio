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
}
