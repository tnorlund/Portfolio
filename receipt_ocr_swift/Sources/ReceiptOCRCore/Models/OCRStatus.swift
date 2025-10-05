import Foundation

public enum OCRStatus: String, Codable, CaseIterable {
    case pending = "PENDING"
    case completed = "COMPLETED"
    case failed = "FAILED"
}


