import Foundation

enum ISO8601Python {
    private static let writeWithFraction: DateFormatter = {
        let df = DateFormatter()
        df.locale = Locale(identifier: "en_US_POSIX")
        df.timeZone = TimeZone(secondsFromGMT: 0)
        df.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSXXXXX" // +00:00 offset
        return df
    }()

    private static let writeNoFraction: DateFormatter = {
        let df = DateFormatter()
        df.locale = Locale(identifier: "en_US_POSIX")
        df.timeZone = TimeZone(secondsFromGMT: 0)
        df.dateFormat = "yyyy-MM-dd'T'HH:mm:ssXXXXX" // +00:00 offset
        return df
    }()

    static func format(_ date: Date) -> String {
        return writeWithFraction.string(from: date)
    }

    static func parse(_ string: String) -> Date? {
        // Normalize space to 'T'
        let base = string.replacingOccurrences(of: " ", with: "T")
        // Detect timezone only in the time portion (after 'T'), not the date hyphens
        let afterT: Substring = {
            if let t = base.firstIndex(of: "T") { return base[base.index(after: t)...] }
            return base[base.startIndex...]
        }()
        let hasTZ = afterT.contains("+") || afterT.contains("-") || base.hasSuffix("Z")

        // Prepare candidate strings to try in order
        var candidates: [String] = [base]
        if !hasTZ { candidates.append(base + "Z"); candidates.append(base + "+00:00") }
        if base.hasSuffix("Z") {
            // also try '+00:00'
            candidates.append(String(base.dropLast()) + "+00:00")
        }
        // If ends with +00:00, try Z variant
        if base.hasSuffix("+00:00") { candidates.append(String(base.dropLast(6)) + "Z") }

        // First, try a normalized variant: keep original tz, but trim/pad fractional to 3 digits
        if let normKeepTZ = normalizeFractionToMilliseconds(base) {
            candidates.insert(normKeepTZ, at: 0)
        }
        // Also try variant collapsing +00:00 to Z with milliseconds
        if let normZ = normalizeToRFC3339Milliseconds(base) {
            candidates.insert(normZ, at: 1)
        }

        let iso = ISO8601DateFormatter()
        iso.timeZone = TimeZone(secondsFromGMT: 0)
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]

        for cand in candidates {
            // Try explicit Python-like patterns first
            let patterns = [
                "yyyy-MM-dd'T'HH:mm:ss.SSSSSSXXXXX",
                "yyyy-MM-dd'T'HH:mm:ss.SSSXXXXX",
                "yyyy-MM-dd'T'HH:mm:ssXXXXX",
                "yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'",
                "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'",
                "yyyy-MM-dd'T'HH:mm:ss'Z'",
                // naive (no timezone) variants
                "yyyy-MM-dd'T'HH:mm:ss.SSSSSS",
                "yyyy-MM-dd'T'HH:mm:ss.SSS",
                "yyyy-MM-dd'T'HH:mm:ss"
            ]
            for p in patterns {
                let df = makeFormatter(pattern: p)
                if let d = df.date(from: cand) { return d }
            }
            if let d = iso.date(from: cand) { return d }
            // Try with expanded options
            iso.formatOptions = [.withFullDate, .withDashSeparatorInDate, .withFullTime, .withColonSeparatorInTime, .withColonSeparatorInTimeZone, .withTimeZone, .withFractionalSeconds]
            if let d = iso.date(from: cand) { return d }
            iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            // If fractional seconds present, try trimming to milliseconds
            if let dot = cand.firstIndex(of: ".") {
                let tzIdx = cand.lastIndex(of: "+") ?? cand.lastIndex(of: "-") ?? (cand.hasSuffix("Z") ? cand.index(before: cand.endIndex) : cand.endIndex)
                if tzIdx > dot {
                    let frac = cand.index(after: dot)..<tzIdx
                    var f = String(cand[frac])
                    if f.count > 3 { f = String(f.prefix(3)) }
                    let collapsed = String(cand[..<dot]) + "." + f + String(cand[tzIdx...])
                    if let d2 = iso.date(from: collapsed) { return d2 }
                }
            }
            // Try without fractional option
            iso.formatOptions = [.withInternetDateTime]
            if let d = iso.date(from: cand) { return d }
            iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        }
        return nil
    }

    private static func normalizeFractionToMilliseconds(_ s: String) -> String? {
        var out = s
        // Ensure fractional seconds exactly 3 digits
        if let tIdx = out.firstIndex(of: "T") {
            let timePart = out[out.index(after: tIdx)..<out.endIndex]
            if let dot = timePart.firstIndex(of: ".") {
                // dotGlobal should point to the '.' character in the original string
                let dotOffset = timePart.distance(from: timePart.startIndex, to: dot)
                let dotGlobal = out.index(tIdx, offsetBy: 1 + dotOffset)
                // Find timezone boundary (Z or + or - or end)
                let tzStart = out[out.index(after: dotGlobal)..<out.endIndex].firstIndex(where: { $0 == "Z" || $0 == "+" || $0 == "-" }) ?? out.endIndex
                var frac = String(out[out.index(after: dotGlobal)..<tzStart])
                if frac.count > 3 { frac = String(frac.prefix(3)) }
                while frac.count < 3 { frac.append("0") }
                out = String(out[..<dotGlobal]) + "." + frac + String(out[tzStart...])
            } else {
                // No fractional seconds; insert .000 before timezone
                let tzStart = out[out.index(after: tIdx)..<out.endIndex].firstIndex(where: { $0 == "Z" || $0 == "+" || $0 == "-" }) ?? out.endIndex
                out = String(out[..<tzStart]) + ".000" + String(out[tzStart...])
            }
        }
        return out
    }

    private static func normalizeToRFC3339Milliseconds(_ s: String) -> String? {
        var out = s
        // Collapse +00:00 to Z
        if out.hasSuffix("+00:00") { out = String(out.dropLast(6)) + "Z" }
        // Then ensure fractional seconds exactly 3 digits
        return normalizeFractionToMilliseconds(out)
    }

    private static func makeFormatter(pattern: String) -> DateFormatter {
        let df = DateFormatter()
        df.locale = Locale(identifier: "en_US_POSIX")
        df.timeZone = TimeZone(secondsFromGMT: 0)
        df.dateFormat = pattern
        return df
    }
}


