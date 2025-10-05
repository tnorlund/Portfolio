import Foundation

public enum Retry {
    public struct Policy {
        public let maxAttempts: Int
        public let initialDelayMs: UInt64
        public let multiplier: Double

        public init(maxAttempts: Int = 3, initialDelayMs: UInt64 = 200, multiplier: Double = 2.0) {
            self.maxAttempts = maxAttempts
            self.initialDelayMs = initialDelayMs
            self.multiplier = multiplier
        }
    }

    public static func withBackoff<T>(policy: Policy = Policy(), _ op: @escaping () async throws -> T) async throws -> T {
        var attempt = 1
        var delay = policy.initialDelayMs
        while true {
            do { return try await op() } catch {
                if attempt >= policy.maxAttempts { throw error }
                try? await Task.sleep(nanoseconds: delay * 1_000_000)
                let next = Double(delay) * policy.multiplier
                delay = UInt64(min(next, Double(UInt64.max)))
                attempt += 1
            }
        }
    }
}


