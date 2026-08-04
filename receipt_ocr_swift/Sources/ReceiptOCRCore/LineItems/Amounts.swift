import Foundation

/// Swift port of `receipt_dynamo/receipt_dynamo/amounts.py`.
///
/// Word-level receipt amount lexing: US-style amounts ("1,234.56"),
/// decimal-comma amounts ("8,82"), European grouped amounts ("1.234,56"),
/// and negative accounting forms (leading/trailing minus, parentheses).
/// Fuel 3-decimal unit prices, "(7.00g)" weights and date-like decimals are
/// rejected by `looksLikeReceiptAmount` exactly as in Python.

/// Minimal NSRegularExpression wrapper mirroring Python `re` semantics.
///
/// - `search`  == `re.search`  (first match anywhere)
/// - `match`   == `re.match`   (anchored at the start only)
/// - `fullMatch` == `re.fullmatch` (wraps the pattern in `\A(?:...)\z` so the
///   engine backtracks to a whole-string match exactly like Python, instead
///   of returning a shorter anchored prefix match)
///
/// All offsets are UTF-16 units; the decoder computes its word spans in the
/// same units so regex spans and word spans always agree (receipt OCR text
/// is BMP, where UTF-16 units == Python code points).
final class Rx {
    private let re: NSRegularExpression
    private let full: NSRegularExpression

    init(_ pattern: String, ci: Bool = false) {
        let options: NSRegularExpression.Options = ci ? [.caseInsensitive] : []
        // Patterns are compile-time constants; a failure is a programmer error.
        // swiftlint:disable:next force_try
        self.re = try! NSRegularExpression(pattern: pattern, options: options)
        // swiftlint:disable:next force_try
        self.full = try! NSRegularExpression(
            pattern: "\\A(?:" + pattern + ")\\z", options: options
        )
    }

    struct Match {
        let start: Int
        let end: Int
        private let result: NSTextCheckingResult
        private let source: NSString

        init(result: NSTextCheckingResult, source: NSString) {
            self.result = result
            self.source = source
            self.start = result.range.location
            self.end = result.range.location + result.range.length
        }

        /// `m.group(i)`; nil when the group did not participate.
        func group(_ index: Int) -> String? {
            let r = result.range(at: index)
            guard r.location != NSNotFound else { return nil }
            return source.substring(with: r)
        }

        var text: String { source.substring(with: result.range) }
    }

    private func firstMatch(
        _ regex: NSRegularExpression, _ s: String,
        options: NSRegularExpression.MatchingOptions = []
    ) -> Match? {
        let ns = s as NSString
        let range = NSRange(location: 0, length: ns.length)
        guard let m = regex.firstMatch(in: s, options: options, range: range)
        else { return nil }
        return Match(result: m, source: ns)
    }

    /// `re.search`
    func search(_ s: String) -> Match? { firstMatch(re, s) }

    /// `re.match` (anchored at the start)
    func match(_ s: String) -> Match? {
        firstMatch(re, s, options: [.anchored])
    }

    /// `re.fullmatch`
    func fullMatch(_ s: String) -> Match? { firstMatch(full, s) }

    func hasMatch(_ s: String) -> Bool { search(s) != nil }

    /// `re.sub(pattern, replacement, s)` (template must not contain `$`).
    func sub(_ s: String, with template: String) -> String {
        let ns = s as NSString
        return re.stringByReplacingMatches(
            in: s, options: [],
            range: NSRange(location: 0, length: ns.length),
            withTemplate: template
        )
    }

    /// `re.split(pattern, s)` for patterns without capture groups. Like
    /// Python, leading/trailing separators yield empty fields.
    func split(_ s: String) -> [String] {
        let ns = s as NSString
        let range = NSRange(location: 0, length: ns.length)
        var out: [String] = []
        var cursor = 0
        for m in re.matches(in: s, options: [], range: range)
        where m.range.length > 0 {
            out.append(
                ns.substring(
                    with: NSRange(
                        location: cursor, length: m.range.location - cursor
                    )
                )
            )
            cursor = m.range.location + m.range.length
        }
        out.append(
            ns.substring(
                with: NSRange(location: cursor, length: ns.length - cursor)
            )
        )
        return out
    }

    /// `re.findall` for patterns without capture groups (whole-match strings).
    func findAll(_ s: String) -> [String] {
        let ns = s as NSString
        let range = NSRange(location: 0, length: ns.length)
        return re.matches(in: s, options: [], range: range).map {
            ns.substring(with: $0.range)
        }
    }
}

enum Amounts {
    // _CURRENCY_SYMBOLS = r"$€£¥₹"
    private static let currencySearch = Rx("[\\$€£¥₹]")
    private static let currencyAndSpace = Rx("[\\$€£¥₹\\s]")
    private static let nonAmountChars = Rx("[^0-9,.\\-]")
    private static let hasDigit = Rx("\\d")
    private static let thousandsWithOptionalCents = Rx(
        "-?\\(?\\d{1,3}(,\\d{3})+(\\.\\d{2})?\\)?-?"
    )
    private static let plainTwoDecimal = Rx("-?\\(?\\d+([.,]\\d{2})\\)?-?")
    private static let thousandsOnly = Rx("\\d{1,3}(,\\d{3})+")
    private static let decimalComma = Rx("\\d+,\\d{2}")

    /// Port of `parse_receipt_amount`.
    static func parseReceiptAmount(_ text: String?) -> Double? {
        guard let text = text else { return nil }
        let raw = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if raw.isEmpty { return nil }

        var isNegative = raw.hasPrefix("(") && raw.hasSuffix(")")
        var cleaned = currencyAndSpace.sub(raw, with: "")

        if cleaned.hasPrefix("(") && cleaned.hasSuffix(")") {
            cleaned = String(cleaned.dropFirst().dropLast())
        }
        if cleaned.hasSuffix("-") {
            isNegative = true
            cleaned = String(cleaned.dropLast())
        }
        if cleaned.hasPrefix("-") {
            isNegative = true
            cleaned = String(cleaned.dropFirst())
        }

        cleaned = nonAmountChars.sub(cleaned, with: "")
        if cleaned.isEmpty || !hasDigit.hasMatch(cleaned) { return nil }

        guard let normalized = normalizeDecimalSeparators(cleaned),
            let value = Double(normalized)
        else { return nil }
        return isNegative ? -value : value
    }

    /// Port of `looks_like_receipt_amount`.
    static func looksLikeReceiptAmount(_ text: String?) -> Bool {
        guard let text = text else { return false }
        let raw = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if raw.isEmpty { return false }
        return currencySearch.hasMatch(raw)
            || thousandsWithOptionalCents.fullMatch(raw) != nil
            || plainTwoDecimal.fullMatch(raw) != nil
    }

    /// Port of `_normalize_decimal_separators`.
    static func normalizeDecimalSeparators(_ cleaned: String) -> String? {
        guard cleaned.contains(",") else { return cleaned }

        let ns = cleaned as NSString
        let lastComma = ns.range(
            of: ",", options: .backwards
        ).location
        let lastDotRange = ns.range(of: ".", options: .backwards)
        let lastDot = lastDotRange.location == NSNotFound
            ? -1 : lastDotRange.location

        if lastDot >= 0 && lastComma > lastDot {
            // European grouping: 1.234,56 -> 1234.56 (both Python branches
            // produce the same transformation; mirrored verbatim)
            return cleaned
                .replacingOccurrences(of: ".", with: "")
                .replacingOccurrences(of: ",", with: ".")
        }
        if lastDot >= 0 {
            // US grouping: 1,234.56 -> 1234.56
            return cleaned.replacingOccurrences(of: ",", with: "")
        }
        if thousandsOnly.fullMatch(cleaned) != nil {
            // Thousands-only grouping: 1,234 -> 1234
            return cleaned.replacingOccurrences(of: ",", with: "")
        }
        if decimalComma.fullMatch(cleaned) != nil {
            // Decimal comma: 8,82 -> 8.82
            return cleaned.replacingOccurrences(of: ",", with: ".")
        }
        // Ambiguous comma usage; preserve historical behavior as thousands.
        return cleaned.replacingOccurrences(of: ",", with: "")
    }
}
