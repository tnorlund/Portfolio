import Logging

extension Logger.Level {
    /// Parse a log level string (trace, debug, info, warn, error) into a Logger.Level.
    /// Returns .info for unrecognized strings.
    public static func from(string: String) -> Logger.Level {
        switch string.lowercased() {
        case "trace": return .trace
        case "debug": return .debug
        case "warn", "warning": return .warning
        case "error": return .error
        default: return .info
        }
    }
}
