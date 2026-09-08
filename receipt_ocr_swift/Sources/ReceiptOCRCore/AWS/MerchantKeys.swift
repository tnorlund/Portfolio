import Foundation

/// Key helpers for the sparse merchant rollup index (GSI1) on
/// `RECEIPT_LINE_ITEM` rows.
///
/// Ported from `receipt_dynamo/entities/receipt_line_item.py`
/// (`slugify_merchant` / `normalize_product_text`). Both sides feed the
/// same index, so a divergence here does not fail a write — it silently
/// splits one merchant's catalog across two partitions. The Python
/// regexes each collapse a RUN of rejected characters into a single
/// replacement, which is what the shared `collapse` below reproduces.
enum MerchantKeys {
    /// `re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unknown"`.
    static func slugifyMerchant(_ merchantName: String) -> String {
        let slug = collapse(
            merchantName.lowercased(),
            separator: "-",
            isKept: { $0.isASCIILowercaseAlphanumeric }
        )
        return slug.isEmpty ? "unknown" : slug
    }

    /// `re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 ]+", " ", name))`
    /// stripped and uppercased.
    ///
    /// The Python form is two passes because the first one can leave
    /// adjacent spaces behind; since the space it inserts is itself a
    /// separator here, one pass over the same accept set is equivalent.
    static func normalizeProductText(_ name: String) -> String {
        collapse(
            name, separator: " ", isKept: { $0.isASCIIAlphanumeric }
        ).uppercased()
    }

    /// Keep accepted characters verbatim; replace every maximal run of
    /// rejected ones with a single `separator`, dropping leading and
    /// trailing runs (the `.strip()` both Python helpers end with).
    private static func collapse(
        _ input: String, separator: Character,
        isKept: (Character) -> Bool
    ) -> String {
        var out = ""
        var sawKept = false
        var pendingSeparator = false
        for character in input {
            guard isKept(character) else {
                pendingSeparator = true
                continue
            }
            if pendingSeparator && sawKept { out.append(separator) }
            out.append(character)
            pendingSeparator = false
            sawKept = true
        }
        return out
    }
}

extension Character {
    /// `[a-z0-9]` — ASCII only, matching the Python character class.
    /// `Character.isLetter` would also accept 'é' and 'カ', which the
    /// regex rejects.
    fileprivate var isASCIILowercaseAlphanumeric: Bool {
        ("a"..."z").contains(self) || ("0"..."9").contains(self)
    }

    /// `[A-Za-z0-9]`, again ASCII only.
    fileprivate var isASCIIAlphanumeric: Bool {
        ("A"..."Z").contains(self) || isASCIILowercaseAlphanumeric
    }
}
