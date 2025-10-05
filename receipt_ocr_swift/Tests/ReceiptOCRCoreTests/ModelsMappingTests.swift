import XCTest
@testable import ReceiptOCRCore

final class ModelsMappingTests: XCTestCase {
    func test_ocr_job_round_trip() throws {
        let now = Date()
        let job = OCRJob(
            imageId: "11111111-1111-1111-1111-111111111111",
            jobId: "22222222-2222-2222-2222-222222222222",
            s3Bucket: "bucket",
            s3Key: "raw/key.png",
            createdAt: now,
            updatedAt: now,
            status: .pending
        )
        let item = job.toItem()
        let decoded = try OCRJob.fromItem(item)
        XCTAssertEqual(decoded.imageId, job.imageId)
        XCTAssertEqual(decoded.jobId, job.jobId)
        XCTAssertEqual(decoded.s3Bucket, job.s3Bucket)
        XCTAssertEqual(decoded.s3Key, job.s3Key)
        XCTAssertEqual(decoded.status, job.status)
        XCTAssertEqual(Int(decoded.createdAt.timeIntervalSince1970), Int(now.timeIntervalSince1970))
    }

    func test_ocr_routing_decision_round_trip() throws {
        let now = Date()
        let dec = OCRRoutingDecision(
            imageId: "11111111-1111-1111-1111-111111111111",
            jobId: "22222222-2222-2222-2222-222222222222",
            s3Bucket: "bucket",
            s3Key: "ocr_results/id.json",
            createdAt: now,
            updatedAt: now,
            receiptCount: 0,
            status: .pending
        )
        let item = dec.toItem()
        let decoded = try OCRRoutingDecision.fromItem(item)
        XCTAssertEqual(decoded.imageId, dec.imageId)
        XCTAssertEqual(decoded.jobId, dec.jobId)
        XCTAssertEqual(decoded.s3Bucket, dec.s3Bucket)
        XCTAssertEqual(decoded.s3Key, dec.s3Key)
        XCTAssertEqual(decoded.receiptCount, 0)
        XCTAssertEqual(decoded.status, .pending)
    }
}


