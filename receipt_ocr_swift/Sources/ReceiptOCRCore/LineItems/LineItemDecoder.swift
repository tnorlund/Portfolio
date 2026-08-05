import Foundation

/// Swift port of the Python band-block line-item decoder:
/// - `receipt_upload/receipt_upload/line_items/geometry.py`
///   (banding, parse_band, _name_is_real, reconcile, extract_items)
/// - `receipt_upload/receipt_upload/line_items/blocks.py`
///   (templatize, _line_amounts, decode_band_blocks, load_default_priors,
///    should_reocr_items_zone, merge_price_fragments)
///
/// The port is line-for-line faithful: regex semantics, float tolerances
/// (e.g. `abs(qty*unit - price) <= 0.02`), Python's stable sorts, and the
/// exact rule ordering of META absorption are all preserved so the Swift
/// decoder is bit-compatible with the Python decoder on the golden set
/// (see LineItemParityTests).

// MARK: - Public data types

/// One OCR word inside the ITEMS zone; mirrors the Python word-dict schema
/// (line_id, word_id, text, x, y_mid, h in receipt-relative coordinates).
public struct ZoneWord: Codable, Sendable {
    public let lineId: Int
    public let wordId: Int
    public let text: String
    public let x: Double
    public let yMid: Double
    public let h: Double

    enum CodingKeys: String, CodingKey {
        case lineId = "line_id"
        case wordId = "word_id"
        case text
        case x
        case yMid = "y_mid"
        case h
    }

    public init(
        lineId: Int, wordId: Int, text: String,
        x: Double, yMid: Double, h: Double
    ) {
        self.lineId = lineId
        self.wordId = wordId
        self.text = text
        self.x = x
        self.yMid = yMid
        self.h = h
    }
}

/// One decoded line item, mirroring the parity-relevant fields of the
/// Python item dict.
public struct DecodedLineItem: Sendable {
    public var name: String
    public var price: Double
    public var quantity: Double?
    public var unitPrice: Double?
    public var isDiscount: Bool
    /// "low" when no human-readable name was found anywhere (Python
    /// `name_quality`); nil otherwise.
    public var nameQuality: String?
    public var lineIds: [Int]
}

/// Template -> role prior entry (assets/block_role_priors_v2.json).
public struct RolePrior: Decodable, Sendable {
    public let role: String
    public let support: Int
    public let purity: Double
}

// MARK: - Regexes (ported verbatim from geometry.py / blocks.py)

enum LineItemRegex {
    /// PRICE_RE (kept for stray-line detection parity; unused by decode)
    static let price = Rx("\\$?(\\d{1,4}(?:,\\d{3})?\\.\\d{2})(-?)")
    /// QTY_AT_RE: "2 @ 3.99", "1.23 lb @ 4.99/lb", "18.871 @ $5.299/Gal"
    static let qtyAt = Rx(
        "(\\d+(?:\\.\\d+)?)\\s*(?:lb|1b|kg|oz|gal)?\\s*@\\s*"
            + "\\$?(\\d+(?:\\.\\d{2,3}))(?:\\s*/\\s*\\w+)?",
        ci: true
    )
    /// QTY_FOR_RE: "4 FOR 1.00", "2 @ 2 FOR 3.00"
    static let qtyFor = Rx(
        "(?:(\\d{1,2})\\s*@\\s*)?(\\d{1,2})\\s+FOR\\s+\\$?(\\d+\\.\\d{2})",
        ci: true
    )
    /// QTY_AT_OCR_RE: OCR reads "@" as "g" in "4 @ $1.79"
    static let qtyAtOcr = Rx("(\\d+(?:\\.\\d+)?)\\s*g\\s*\\$(\\d+\\.\\d{2,3})")
    /// QTY_MULT_RE: leading "1x" / "2X" multiplier
    static let qtyMult = Rx("^(\\d{1,2})[xX]$")
    /// LEAD_QTY_RE (ported for completeness)
    static let leadQty = Rx("^(\\d{1,2})\\s+(?=[A-Za-z])")
    /// TAX_FLAG_RE (ported for completeness)
    static let taxFlag = Rx("\\s+[TFNOAB]X?$")
    /// SETTLEMENT_RE: settlement lines are never items, even when a broken
    /// ITEMS section includes them. OCR sometimes scrambles word order
    /// ("17.98 DUE BALANCE") or prefixes an item count ("[1 item] Sub Total
    /// 16.00"); both forms are covered.
    static let settlement = Rx(
        "^\\W*(?:ITEMS?\\W+)?"
            + "(?:BALANCE(?:\\s+DUE|\\s+TO\\s+PAY)?|DUE\\s+BALANCE"
            + "|(?:AMOUNT|TOTAL)\\s+(?:DUE|TO\\s+PAY)|TO\\s+PAY|CREDIT"
            + "|(?:AUTH\\s+)?DEBIT"
            + "|CHANGE(?:\\s+DUE)?|CASH(?:\\s+BACK)?|TENDER(?:ED)?"
            + "|SUB\\W{0,2}T(?:OTA|T)L|TOTAL|(?:SALES\\s+)?TAX)\\W*$",
        ci: true
    )
    /// WAS_PRICE_RE: price-comparison metadata ("WAS: $3.59 each")
    static let wasPrice = Rx("\\b(?:WAS|REG)\\b[:.]?\\s*\\$?\\d", ci: true)
    /// SALE_PRICE_RE: annotation echo that restates a post-discount unit
    /// price ("Sale Price 1.99", Target's "Regular Price $22.99", Nordstrom
    /// Rack's "Comparable Value 59.95"). Exact phrases only — "BAG SALE
    /// PAPER EA" is a real item.
    static let salePrice = Rx(
        "\\b(?:(?:SALE|REG(?:ULAR)?\\.?)\\s+PRICE|COMPARABLE\\s+VALUE)\\b",
        ci: true
    )
    /// NON_PRODUCT_NOTE_RE: tip-suggestion footers ("22% Tip = 4.40",
    /// "18%: (Tip Total 9.27)") and transaction-count notes ("Items in
    /// Transaction: 5"). The %-sign / exact-phrase anchors keep product
    /// names ("6% FAT MLK", "STEAK TIPS") out of reach.
    static let nonProductNote = Rx(
        "\\d{1,3}\\s*%\\s*[:=]|%\\s*TIP\\b|\\bTIP\\s+TOTAL\\b"
            + "|\\bITEMS?\\s+IN\\s+TRANSACTION\\b",
        ci: true
    )
    /// SKU_LIKE_RE
    static let skuLike = Rx("\\d{4,}")
    /// Amount fraction gate: r"\d[.,]\d{2}(?!\d)"
    static let twoDecimal = Rx("\\d[.,]\\d{2}(?!\\d)")
    /// Fused taxability flag on an amount: r"\$?\d[\d.,]*\d[A-Z]" (fullmatch)
    static let fusedFlagAmount = Rx("\\$?\\d[\\d.,]*\\d[A-Z]")
    static let alpha2 = Rx("[A-Za-z]{2,}")
    static let alpha3 = Rx("[A-Za-z]{3,}")
    static let multiSpace = Rx("\\s{2,}")
    static let whitespaceRun = Rx("\\s+")
    static let digitRun = Rx("\\d+")
    static let digits4Plus = Rx("\\d{4,}")
    /// Standalone taxability flag word (fullmatch in parse_band)
    static let taxFlagWord = Rx("[TFNOAB]X?")
    /// Alpha runs of a de-amounted row (`re.findall(r"[A-Za-z]+", bare)`)
    static let alphaRun = Rx("[A-Za-z]+")
    /// A run of masking characters left once digits are stripped
    /// ("xXXX5061" -> "xXXX"); never a word.
    static let maskToken = Rx("x+", ci: true)
    /// Trailing single-letter name token, S excluded (see parse_band).
    static let trailingFlagLetter = Rx("[A-RT-Za-rt-z]")
    /// Bare "2 3.99" qty + unit price (fullmatch)
    static let bareQtyPrice = Rx("(\\d{1,2})\\s+\\$?(\\d+\\.\\d{2})")
    static let digits12 = Rx("\\d{1,2}")
    /// Strips amounts before the settlement vocabulary test
    static let amountStrip = Rx("\\$?\\d[\\d.,]*")
    /// merge_price_fragments: left token "1." / "5," / "$12."
    static let fragmentLeft = Rx("\\$?\\d{1,4}[.,]")
    static let fragmentRight = Rx("\\d{2}")
}

/// DISCOUNT_WORDS (kept for reference; matching goes through
/// `discountWordRegex`, the port of Python's DISCOUNT_WORD_RE).
let discountWords = ["SAVED", "SAVING", "OFF", "COUPON", "DISCOUNT", "PROMO"]

/// DISCOUNT_WORD_RE: discount markers matched as WORDS. The substring
/// scan this replaces read "OFF" inside COFFEE / TOFFEE / Office and
/// flagged 15 real prod items as discounts -- and discounts are excluded
/// from reconciliation, so those receipts could never balance. Bare "OFF"
/// is product vocabulary too ("SALMON FILLET SKIN OFF", "EASY OFF"), so a
/// genuine markdown has to carry its percent/amount.
let discountWordRegex = Rx(
    "\\b(?:SAVED|SAVINGS?|COUPONS?|DISCOUNTS?|PROMO(?:TION)?S?)\\b"
        + "|[\\d%]\\s*%?\\s*OFF\\b",
    ci: true
)

/// TENDER_ANCHOR_TOKENS: a settlement row must carry one of these.
let tenderAnchorTokens: Set<String> = [
    "visa", "mastercard", "master", "amex", "discover", "diners", "jcb",
    "unionpay", "maestro", "interac", "eftpos",
    "cash", "change", "credit", "debit", "tender", "tendered",
]

/// PAYMENT_AFFIX_TOKENS: words that may surround an anchor.
let paymentAffixTokens: Set<String> = [
    "acct", "account", "aid", "appr", "approved", "auth", "authorization",
    "batch", "card", "cardholder", "cards", "chip", "contactless", "ending",
    "emv", "entry", "express", "american", "for", "insert", "inserted",
    "keyed",
    "local", "manual", "mid", "mobile", "no", "num", "number", "paid", "pay",
    "payment", "payments", "pin", "purchase", "read", "ref", "reference",
    "sale", "seq", "swipe", "swiped", "tap", "tapped", "term", "terminal",
    "tid", "trans", "transaction", "type", "us", "usd", "verified",
]

/// Port of `geometry.is_settlement_row`. SETTLEMENT_RE alone only ever
/// matched a row that is EXACTLY the tender word, so branded forms
/// ("Visa Debit", "xXXX5061 MASTERCARD", "MasterCard 1394 (Swipe)")
/// decoded as phantom items on 12 prod receipts. Recognizing them by
/// CLOSED VOCABULARY is what keeps real food safe: "33965 PORK TENDER"
/// and "CHICKEN TENDER" carry a tender word plus a word this vocabulary
/// does not contain, so they stay items.
public func isSettlementRow(_ bare: String) -> Bool {
    if LineItemRegex.settlement.match(bare) != nil { return true }
    let tokens = LineItemRegex.alphaRun.allMatches(bare).map {
        $0.lowercased()
    }
    if tokens.isEmpty { return false }
    if !tokens.contains(where: { tenderAnchorTokens.contains($0) }) {
        return false
    }
    return tokens.allSatisfy {
        tenderAnchorTokens.contains($0)
            || paymentAffixTokens.contains($0)
            || LineItemRegex.maskToken.fullMatch($0) != nil
    }
}

/// UNIT_WORDS: tokens that don't count as product-name content
let unitWords: Set<String> = [
    "EA", "LB", "KG", "OZ", "CT", "PK", "X", "C", "F", "T", "N", "O", "A",
    "B", "TX", "FS", "QTY", "EACH",
]

// MARK: - Small helpers

extension Array {
    /// Python's `sorted` is stable; Swift's is not guaranteed to be.
    /// Tie-break on the original index to force stability.
    func stableSorted(
        by areInIncreasingOrder: (Element, Element) -> Bool
    ) -> [Element] {
        enumerated().sorted { a, b in
            if areInIncreasingOrder(a.element, b.element) { return true }
            if areInIncreasingOrder(b.element, a.element) { return false }
            return a.offset < b.offset
        }.map(\.element)
    }
}

/// Python `round(x, 2)` (round-half-to-even).
func pythonRound2(_ x: Double) -> Double {
    (x * 100).rounded(.toNearestOrEven) / 100
}

/// Python `s.strip(" @$-")`-style strip of specific characters.
private func stripChars(_ s: String, _ chars: Set<Character>) -> String {
    var sub = Substring(s)
    while let f = sub.first, chars.contains(f) { sub.removeFirst() }
    while let l = sub.last, chars.contains(l) { sub.removeLast() }
    return String(sub)
}

private func pyStrip(_ s: String) -> String {
    s.trimmingCharacters(in: .whitespacesAndNewlines)
}

/// Python code-point-lexicographic string comparison (Swift's `<` uses
/// Unicode canonical ordering, which can differ).
private func scalarLess(_ a: String, _ b: String) -> Bool {
    a.unicodeScalars.lexicographicallyPrecedes(b.unicodeScalars) {
        $0.value < $1.value
    }
}

// MARK: - templatize / name predicates

/// Port of `blocks.templatize`: digit-collapsed shape of a line.
func templatize(_ text: String) -> String {
    let t = LineItemRegex.whitespaceRun.sub(
        pyStrip(text).uppercased(), with: " "
    )
    return LineItemRegex.digitRun.sub(t, with: "#")
}

/// Port of `geometry._name_is_real`.
func nameIsReal(_ name: String) -> Bool {
    let tokens = LineItemRegex.alpha2.findAll(name)
    let real = tokens.filter { !unitWords.contains($0.uppercased()) }
    return real.reduce(0) { $0 + $1.utf16.count } >= 3
}

/// Port of `blocks._sku_dominated` (decode_band_blocks variant).
func skuDominated(_ name: String) -> Bool {
    let stripped = LineItemRegex.digits4Plus.sub(name, with: " ")
    return LineItemRegex.alpha2.findAll(stripped).count < 2
}

// MARK: - Banding (geometry.estimate_skew / band_words)

/// Port of `geometry.estimate_skew`: residual baseline slope (dy/dx).
func estimateSkew(_ words: [ZoneWord]) -> Double {
    var byLine: [Int: [(Double, Double)]] = [:]
    for w in words {
        byLine[w.lineId, default: []].append((w.x, w.yMid))
    }
    var slopes: [Double] = []
    for pts0 in byLine.values {
        if pts0.count < 2 { continue }
        // Python sorts (x, y_mid) tuples lexicographically.
        let pts = pts0.sorted {
            $0.0 != $1.0 ? $0.0 < $1.0 : $0.1 < $1.1
        }
        let dx = pts[pts.count - 1].0 - pts[0].0
        if dx > 0.15 {
            slopes.append((pts[pts.count - 1].1 - pts[0].1) / dx)
        }
    }
    if slopes.isEmpty { return 0.0 }
    slopes.sort()
    return slopes[slopes.count / 2]
}

/// Port of `geometry.band_words`: cluster words into visual bands by
/// deskewed y-center gaps; each band is x-sorted.
func bandWords(_ words: [ZoneWord]) -> [[ZoneWord]] {
    if words.isEmpty { return [] }
    let hs = words.map(\.h).sorted()
    var medH = hs[words.count / 2]
    if medH == 0 { medH = 0.01 }  // Python `or 0.01`

    let slope = estimateSkew(words)
    func yFlat(_ w: ZoneWord) -> Double { w.yMid - slope * w.x }

    let ws = words.stableSorted { yFlat($0) < yFlat($1) }
    var bands: [[ZoneWord]] = [[ws[0]]]
    for w in ws.dropFirst() {
        // Gap test anchored to the band's FIRST word (see Python comment).
        if yFlat(w) - yFlat(bands[bands.count - 1][0]) < medH * 0.6 {
            bands[bands.count - 1].append(w)
        } else {
            bands.append([w])
        }
    }
    return bands.map { $0.stableSorted { $0.x < $1.x } }
}

// MARK: - Amount extraction per line/band (blocks._line_amounts)

/// Port of `blocks._line_amounts`.
func lineAmounts(_ words: [ZoneWord]) -> [Double] {
    var out: [Double] = []
    for w in words {
        var t = w.text
        // OCR carcass ("80.00)" from "@0.00)"), never a price
        if t.hasSuffix(")") && !t.contains("(") { continue }
        // Strip a single fused trailing taxability flag ("0.38N")
        if LineItemRegex.fusedFlagAmount.fullMatch(t) != nil {
            t = String(t.dropLast())
        }
        if Amounts.looksLikeReceiptAmount(t),
            LineItemRegex.twoDecimal.hasMatch(t),
            let v = Amounts.parseReceiptAmount(t)
        {
            out.append(v)
        }
    }
    return out
}

// MARK: - parse_band (geometry.parse_band)

/// Parsed contents of one visual band. A class so that the META-absorption
/// stage's mutations (quantity transplant) are shared with the emission
/// stage, exactly like the Python dicts in `parsed_cache`.
final class ParsedBand {
    var name: String
    var quantity: Double?
    var unitPrice: Double?
    var price: Double
    var isDiscount: Bool
    let rawText: String
    let nAmounts: Int
    var nameQuality: String?
    var stacked: Bool = false
    var lineIds: [Int] = []

    init(
        name: String, quantity: Double?, unitPrice: Double?, price: Double,
        isDiscount: Bool, rawText: String, nAmounts: Int
    ) {
        self.name = name
        self.quantity = quantity
        self.unitPrice = unitPrice
        self.price = price
        self.isDiscount = isDiscount
        self.rawText = rawText
        self.nAmounts = nAmounts
    }
}

/// Port of `geometry.parse_band`. `band` must be x-sorted (as produced by
/// `bandWords`). Returns nil when the band carries no price and no
/// quantity form.
func parseBand(_ band: [ZoneWord]) -> ParsedBand? {
    let texts = band.map(\.text)
    let joined = texts.joined(separator: " ")

    // Char span (UTF-16 units, matching NSRegularExpression offsets) of
    // each word in the joined text.
    var spans: [(Int, Int)] = []
    var pos = 0
    for t in texts {
        let n = t.utf16.count
        spans.append((pos, pos + n))
        pos += n + 1
    }
    func wordsInSpan(_ a: Int, _ b: Int) -> Set<Int> {
        Set(spans.indices.filter { spans[$0].0 < b && spans[$0].1 > a })
    }

    var consumed: Set<Int> = []

    // Word-level amounts (candidate prices).
    var amounts: [(Int, Double)] = []
    for (i, t) in texts.enumerated() {
        if t.hasSuffix(")") && !t.contains("(") { continue }
        if Amounts.looksLikeReceiptAmount(t),
            LineItemRegex.twoDecimal.hasMatch(t),
            let v = Amounts.parseReceiptAmount(t), abs(v) < 100000
        {
            amounts.append((i, v))
        }
    }

    // Quantity forms, joined-text. M-FOR-X deal runs first.
    var qty: Double?
    var unitPrice: Double?
    var qtyWordIdxs: Set<Int> = []
    if let m = LineItemRegex.qtyFor.search(joined) {
        let dealN = Double(m.group(2)!)!
        qty = m.group(1).flatMap(Double.init) ?? dealN
        unitPrice = pythonRound2(Double(m.group(3)!)! / dealN)
        qtyWordIdxs = wordsInSpan(m.start, m.end)
    }
    if qty == nil {
        if let m = LineItemRegex.qtyAt.search(joined)
            ?? LineItemRegex.qtyAtOcr.search(joined)
        {
            qty = Double(m.group(1)!)
            unitPrice = Double(m.group(2)!)
            qtyWordIdxs = wordsInSpan(m.start, m.end)
        }
    }
    if qty == nil {
        // bare "2 3.99" (qty + unit price, no @)
        let stripped = pyStrip(joined.replacingOccurrences(of: "$", with: ""))
        if let m2 = LineItemRegex.bareQtyPrice.fullMatch(stripped) {
            qty = Double(m2.group(1)!)
            unitPrice = Double(m2.group(2)!)
            qtyWordIdxs = Set(texts.indices)
        }
    }

    // Price = last amount word that isn't part of the qty expression.
    var price: Double?
    var priceIdx: Int?
    for (i, v) in amounts {
        if qty != nil && qtyWordIdxs.contains(i) && amounts.count > 1 {
            continue
        }
        price = v
        priceIdx = i
    }
    if price == nil, let last = amounts.last {
        priceIdx = last.0
        price = last.1
    }
    if price == nil && qty == nil { return nil }

    consumed.formUnion(qtyWordIdxs)
    if let pi = priceIdx { consumed.insert(pi) }

    let upper = joined.uppercased()
    let isDiscount =
        (price != nil && price! < 0) || discountWordRegex.hasMatch(upper)

    // Name = words not consumed by price/qty, minus flags/currency tokens
    var nameIdxs: [Int] = []
    for (i, t) in texts.enumerated() {
        if consumed.contains(i) { continue }
        if Amounts.looksLikeReceiptAmount(t) { continue }
        if LineItemRegex.taxFlagWord.fullMatch(t) != nil { continue }
        nameIdxs.append(i)
    }

    // Leading quantity forms consume their word
    if qty == nil && !nameIdxs.isEmpty {
        let first = texts[nameIdxs[0]]
        if let m3 = LineItemRegex.qtyMult.match(first) {
            qty = Double(m3.group(1)!)
            nameIdxs.removeFirst()
        } else if LineItemRegex.digits12.fullMatch(first) != nil,
            nameIdxs.dropFirst().contains(where: {
                LineItemRegex.alpha2.hasMatch(texts[$0])
            })
        {
            qty = Double(first)
            nameIdxs.removeFirst()
        }
    }

    // Trailing single-letter token: a taxability flag the fixed [TFNOAB]
    // filter misses, or a truncation glyph OCR read as a letter (Trader
    // Joe's prints "SALMON FILLET SKIN OFF (" on two identically-named
    // rows and Vision read the cut-off paren as "C" on one). Bounded by
    // corpus measurement: at least three preceding name words, and never
    // "S" (more often a truncated plural than a flag).
    if nameIdxs.count >= 4,
        LineItemRegex.trailingFlagLetter.fullMatch(texts[nameIdxs.last!])
            != nil
    {
        nameIdxs.removeLast()
    }

    let name = stripChars(
        LineItemRegex.multiSpace.sub(
            nameIdxs.map { texts[$0] }.joined(separator: " "), with: " "
        ),
        [" ", "@", "$", "-"]
    )

    return ParsedBand(
        name: name,
        quantity: qty,
        unitPrice: unitPrice,
        price: price ?? 0.0,
        isDiscount: isDiscount,
        rawText: joined,
        nAmounts: amounts.count
    )
}

// MARK: - Zone bands (blocks._zone_bands)

struct ZoneBand {
    var words: [ZoneWord]
    var lineIds: [Int]
    var text: String
    var template: String
    var amounts: [Double]
    var y: Double
    var role: String = ""
}

/// Port of `blocks._zone_bands`: deskewed visual bands over the zone,
/// sorted into reading order (descending y; larger y_mid is higher).
func zoneBands(words: [ZoneWord], zoneLineIds: Set<Int>) -> [ZoneBand] {
    let zoneWords = words.filter { zoneLineIds.contains($0.lineId) }
    var out: [ZoneBand] = []
    for band in bandWords(zoneWords) {
        let text = band.map(\.text).joined(separator: " ")
        out.append(
            ZoneBand(
                words: band,
                lineIds: Set(band.map(\.lineId)).sorted(),
                text: text,
                template: templatize(text),
                amounts: lineAmounts(band),
                y: band.reduce(0.0) { $0 + $1.yMid } / Double(band.count)
            )
        )
    }
    return out.stableSorted { $0.y > $1.y }
}

// MARK: - decode_band_blocks (blocks.decode_band_blocks)

/// The printed summary figures a decode may be filtered against
/// (Python's `summary` dict: subtotal / tax / grand_total).
public struct LineItemSummary: Sendable, Equatable {
    public var subtotal: Double?
    public var tax: Double?
    public var grandTotal: Double?

    public init(
        subtotal: Double? = nil, tax: Double? = nil, grandTotal: Double? = nil
    ) {
        self.subtotal = subtotal
        self.tax = tax
        self.grandTotal = grandTotal
    }
}

/// Port of `blocks.filter_summary_figure_items` (#1320): drop non-product
/// bands whose price merely restates a printed summary figure.
///
/// Every guard is load-bearing and ported verbatim:
///   * runs only when the receipt does NOT already reconcile, so the filter
///     can never lose a currently-matching receipt;
///   * at least 2 other non-discount items must survive every drop
///     (single-item receipts legitimately have item price == total);
///   * an UNNAMED band may match subtotal / tax / grand_total and is dropped
///     only when the drop strictly improves the delta;
///   * a NAMED band may match only subtotal / grand_total and is dropped
///     only when the remaining items then reconcile to a match.
func filterSummaryFigureItems(
    _ items: [ParsedBand], summary: LineItemSummary?
) -> [ParsedBand] {
    guard let summary, !items.isEmpty else { return items }

    let subtotal = summary.subtotal
    let tax = summary.tax
    let grand = summary.grandTotal
    var baseline = subtotal
    if baseline == nil, let g = grand { baseline = g - (tax ?? 0.0) }
    guard let base = baseline, base > 0 else { return items }

    let nonDisc = items.filter { !$0.isDiscount }
    var cur = pythonRound2(nonDisc.reduce(0.0) { $0 + $1.price })
    let tol = max(0.02, base * 0.01)
    if abs(cur - base) <= tol { return items }  // already reconciles

    var drop: Set<Int> = []
    var changed = true
    while changed {
        changed = false
        for (idx, it) in items.enumerated() {
            if drop.contains(idx) || it.isDiscount { continue }
            let price = it.price
            if price <= 0 { continue }
            let unnamed = !nameIsReal(it.name)
            var figures: [Double?] = [subtotal, grand]
            if unnamed { figures.append(tax) }
            // 1% figure tolerance (same shape as reconcile's match band):
            // the printed figure itself carries OCR jitter.
            let matchesFigure = figures.contains { f in
                guard let f else { return false }
                return abs(price - f) <= max(0.02, f * 0.01)
            }
            if !matchesFigure { continue }
            if nonDisc.count - drop.count - 1 < 2 { continue }
            let newDiff = abs(pythonRound2(cur - price) - base)
            let ok =
                unnamed
                ? newDiff < abs(cur - base) - 0.005
                : newDiff <= tol
            if ok {
                drop.insert(idx)
                cur = pythonRound2(cur - price)
                changed = true
            }
        }
    }
    if drop.isEmpty { return items }
    return items.enumerated()
        .filter { !drop.contains($0.offset) }
        .map(\.element)
}

/// Port of `blocks.decode_band_blocks`: block decode over deskewed visual
/// bands. `summary` (optional) enables the non-product band filter; callers
/// without a summary pass nil and get the unfiltered decode.
func decodeBandBlocks(
    words: [ZoneWord], zoneLineIds: Set<Int>, priors: [String: RolePrior],
    summary: LineItemSummary? = nil
) -> [ParsedBand] {
    var bands = zoneBands(words: words, zoneLineIds: zoneLineIds)
    if bands.isEmpty { return [] }

    for idx in bands.indices {
        let text = bands[idx].text
        // Settlement / WAS / SALE-PRICE bands are never items; strip
        // amounts before the settlement test so "CHANGE 0.00" reduces to
        // its vocabulary.
        let bare = pyStrip(LineItemRegex.amountStrip.sub(text, with: " "))
        if isSettlementRow(bare)
            || LineItemRegex.wasPrice.hasMatch(text)
            || LineItemRegex.salePrice.hasMatch(text)
            || LineItemRegex.nonProductNote.hasMatch(text)
        {
            bands[idx].role = "OUTSIDE"
            continue
        }
        if let prior = priors[bands[idx].template],
            prior.purity >= 0.75, prior.support >= 2
        {
            bands[idx].role = prior.role
        } else if !bands[idx].amounts.isEmpty {
            bands[idx].role = "PRICE"
        } else if LineItemRegex.alpha3.hasMatch(text) {
            bands[idx].role = "MEMBER"
        } else {
            bands[idx].role = "OUTSIDE"
        }
    }

    let priceIdxAll = bands.indices.filter { bands[$0].role == "PRICE" }

    // Zone price column: right-most amount-word x ("in column" = within
    // 0.15).
    var zoneAmtX: [Double] = []
    for b in bands {
        for w in b.words where !lineAmounts([w]).isEmpty {
            zoneAmtX.append(w.x)
        }
    }
    let zoneColX = zoneAmtX.max()

    // META absorption (three rules, in Python's exact order).
    var parsedCache: [Int: ParsedBand] = [:]
    for p in priceIdxAll {
        if let parsed = parseBand(bands[p].words) { parsedCache[p] = parsed }
    }
    var absorbed: Set<Int> = []
    for (pos, p) in priceIdxAll.enumerated() {
        guard let mp = parsedCache[p] else { continue }
        if nameIsReal(mp.name) { continue }
        let qty = mp.quantity
        let unit = mp.unitPrice
        for npos in [pos - 1, pos + 1] {
            guard npos >= 0 && npos < priceIdxAll.count else { continue }
            let q = priceIdxAll[npos]
            if absorbed.contains(q) { continue }
            guard let nb = parsedCache[q] else { continue }
            // Rule 1: qty metadata explains the neighbor's price
            if let qv = qty, let uv = unit,
                abs(qv * uv - nb.price) <= 0.02
            {
                if nb.quantity == nil {
                    nb.quantity = qv
                    nb.unitPrice = uv
                }
                absorbed.insert(p)
                break
            }
            // Rule 2: echo absorption ONLY into a real-named neighbor
            if nameIsReal(nb.name),
                abs(mp.price) == abs(nb.price),
                LineItemRegex.skuLike.hasMatch(mp.rawText) || qty != nil
            {
                if qty != nil && nb.quantity == nil {
                    nb.quantity = qty
                    nb.unitPrice = unit
                }
                absorbed.insert(p)
                break
            }
            // Rule 3: unit-price echo with the qty prefix lost to OCR.
            // Column gate: only LEFT-column amounts are echoes.
            let inPriceCol =
                zoneColX != nil
                && bands[p].words.contains { w in
                    !lineAmounts([w]).isEmpty && abs(w.x - zoneColX!) < 0.15
                }
            if qty == nil, !inPriceCol,
                pyStrip(mp.name).isEmpty,
                !LineItemRegex.skuLike.hasMatch(mp.rawText),
                nameIsReal(nb.name),
                mp.price > 0, nb.price > 0
            {
                let ratio = nb.price / mp.price
                let k = ratio.rounded(.toNearestOrEven)
                if k >= 2, k <= 12, abs(nb.price - k * mp.price) <= 0.02 {
                    if nb.quantity == nil {
                        nb.quantity = k
                        nb.unitPrice = mp.price
                    }
                    absorbed.insert(p)
                    break
                }
            }
        }
    }

    let priceIdx = priceIdxAll.filter { !absorbed.contains($0) }

    // Member attachment: nearer adjacent PRICE band in reading order;
    // ties go up.
    var blocks: [Int: [Int]] = [:]
    for p in priceIdx { blocks[p] = [] }
    for i in bands.indices where bands[i].role == "MEMBER" {
        let prevP = priceIdx.last { $0 < i }
        let nextP = priceIdx.first { $0 > i }
        if prevP == nil && nextP == nil { continue }
        if let np = nextP, prevP == nil {
            blocks[np]!.append(i)
        } else if let pp = prevP, nextP == nil {
            blocks[pp]!.append(i)
        } else {
            let pp = prevP!
            let np = nextP!
            blocks[(i - pp) <= (np - i) ? pp : np]!.append(i)
        }
    }

    // Hybrid emission in ascending-y order (bottom of the receipt first):
    // item order is a pinned guarantee.
    var items: [ParsedBand] = []
    for p in priceIdx.reversed() {
        guard let parsed = parsedCache[p] ?? parseBand(bands[p].words)
        else { continue }
        if parsed.quantity == nil {
            for i in blocks[p]! {
                if let mp = parseBand(bands[i].words),
                    let q = mp.quantity, let u = mp.unitPrice,
                    abs(q * u - parsed.price) <= 0.02
                {
                    parsed.quantity = q
                    parsed.unitPrice = u
                    break
                }
            }
        }
        if skuDominated(parsed.name) {
            // Donor criterion is _name_is_real (>=3 alpha chars), NOT the
            // two-token SKU test; Python takes max() over (len, text)
            // tuples.
            var best: (Int, String)?
            for i in blocks[p]! where nameIsReal(bands[i].text) {
                let cand = (bands[i].text.utf16.count, bands[i].text)
                if let cur = best {
                    if cand.0 > cur.0
                        || (cand.0 == cur.0 && scalarLess(cur.1, cand.1))
                    {
                        best = cand
                    }
                } else {
                    best = cand
                }
            }
            if let b = best {
                parsed.name = pyStrip(b.1)
                parsed.stacked = true
            }
        }
        if !nameIsReal(parsed.name) {
            // No name anywhere: keep the price, flag the quality.
            parsed.nameQuality = "low"
        }
        var lids = Set(bands[p].lineIds)
        for i in blocks[p]! { lids.formUnion(bands[i].lineIds) }
        parsed.lineIds = lids.sorted()
        items.append(parsed)
    }
    return filterSummaryFigureItems(items, summary: summary)
}

// MARK: - Priors (blocks.load_default_priors)

private struct PriorsFile: Decodable {
    let templates: [String: RolePrior]
}

/// Port of `blocks.load_default_priors`: the committed template->role
/// prior asset (v2, self-labeled), bundled as a package resource.
///
/// Loaded via `Bundle.module` rather than `#filePath` arithmetic — the
/// path-component counting in a previous draft (PR #1290) was off by one;
/// the bundle lookup cannot drift with file location.
public func loadDefaultPriors() -> [String: RolePrior] {
    guard
        let url = Bundle.module.url(
            forResource: "block_role_priors_v2", withExtension: "json"
        )
    else {
        fatalError(
            "block_role_priors_v2.json missing from ReceiptOCRCore bundle; "
                + "check Package.swift resources"
        )
    }
    do {
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(PriorsFile.self, from: data).templates
    } catch {
        fatalError("failed to decode block_role_priors_v2.json: \(error)")
    }
}

// MARK: - Public API

/// Port of `geometry.extract_items`: extract items via the band-block
/// decoder with the committed golden-trained priors.
public func extractLineItems(
    words: [ZoneWord], zoneLineIds: Set<Int>,
    summary: LineItemSummary? = nil
) -> [DecodedLineItem] {
    extractLineItems(
        words: words, zoneLineIds: zoneLineIds, priors: defaultPriors,
        summary: summary
    )
}

/// Priors are immutable after load; cache them process-wide.
private let defaultPriors: [String: RolePrior] = loadDefaultPriors()

/// `extract_items` with explicit priors (parity with
/// `decode_band_blocks(ocr, priors, summary)`).
public func extractLineItems(
    words: [ZoneWord], zoneLineIds: Set<Int>, priors: [String: RolePrior],
    summary: LineItemSummary? = nil
) -> [DecodedLineItem] {
    decodeBandBlocks(
        words: words, zoneLineIds: zoneLineIds, priors: priors,
        summary: summary
    ).map { p in
        DecodedLineItem(
            name: p.name,
            price: p.price,
            quantity: p.quantity,
            unitPrice: p.unitPrice,
            isDiscount: p.isDiscount,
            nameQuality: p.nameQuality,
            lineIds: p.lineIds
        )
    }
}

/// Port of `blocks.should_reocr_items_zone`.
public func shouldReocrItemsZone(
    items: [DecodedLineItem], printedSubtotal: Double?
) -> Bool {
    guard !items.isEmpty, let subtotal = printedSubtotal, subtotal > 0
    else { return false }
    let total = items.reduce(0.0) { $0 + $1.price }
    let diff = abs(pythonRound2(total) - subtotal)
    return diff > max(1.0, subtotal * 0.10)
}

/// Port of `blocks.merge_price_fragments`: concatenate OCR-shattered price
/// fragments within a band ("1." + "99" -> "1.99", "5," + "90" -> "5.90").
public func mergePriceFragments(_ words: [ZoneWord]) -> [ZoneWord] {
    if words.count < 2 { return words }
    let ws = words.stableSorted { $0.x < $1.x }
    var out: [ZoneWord] = []
    var i = 0
    while i < ws.count {
        let w = ws[i]
        if i + 1 < ws.count {
            let nxt = ws[i + 1]
            let gap = nxt.x - w.x
            if LineItemRegex.fragmentLeft.fullMatch(w.text) != nil,
                LineItemRegex.fragmentRight.fullMatch(nxt.text) != nil,
                gap >= 0, gap < 0.08
            {
                let merged = ZoneWord(
                    lineId: w.lineId,
                    wordId: w.wordId,
                    text: w.text.replacingOccurrences(of: ",", with: ".")
                        + nxt.text,
                    x: w.x,
                    yMid: w.yMid,
                    h: w.h
                )
                out.append(merged)
                i += 2
                continue
            }
        }
        out.append(w)
        i += 1
    }
    return out
}

/// Result of `reconcileLineItems` (Python `geometry.ReconcileResult`).
///
/// `status` keeps the four-value vocabulary the golden floors, the stream
/// stage and the ReceiptLineItem entity depend on. `baselineSource` and
/// `baselineFiguresAgreeing` are the additive #1324 diagnostics: which
/// printed figure was compared against, and how many printed figures
/// participate in an arithmetic story consistent with the item sum
/// (3 / 2 / 1; nil when the status is mismatch or no-baseline).
public struct ReconcileResult: Sendable, Equatable {
    public let status: String  // "no-baseline" | "match" | "near" | "mismatch"
    public let itemSum: Double?
    public let baseline: Double?
    public let baselineSource: String?
    public let baselineFiguresAgreeing: Int?

    public init(
        status: String, itemSum: Double?, baseline: Double?,
        baselineSource: String? = nil, baselineFiguresAgreeing: Int? = nil
    ) {
        self.status = status
        self.itemSum = itemSum
        self.baseline = baseline
        self.baselineSource = baselineSource
        self.baselineFiguresAgreeing = baselineFiguresAgreeing
    }
}

/// Absolute plausibility ceiling for any printed summary figure. SKU /
/// barcode strings occasionally OCR-parse as money (a 24-char SKU tail
/// became a $2.0B subtotal); no honest receipt approaches this.
public let maxPlausibleBaseline: Double = 50_000.0

/// Port of `geometry._classify_against`.
func classifyAgainst(itemSum: Double, baseline: Double) -> String {
    let diff = abs(itemSum - baseline)
    if diff <= max(0.02, baseline * 0.01) { return "match" }
    if diff <= max(1.0, baseline * 0.10) { return "near" }
    return "mismatch"
}

/// Port of `geometry._baseline_implausible`: a printed baseline no honest
/// receipt produces. Deliberately one-directional — a baseline far ABOVE
/// the item sum is severe under-extraction (the extractor's fault) and must
/// stay a hard mismatch.
func baselineImplausible(itemSum: Double, baseline: Double) -> Bool {
    itemSum > 3 * baseline
}

/// Port of `geometry.reconcile_detailed` (#1324): three-figure baseline
/// with printed-figure hygiene and a 1..3 agreement grade.
public func reconcileLineItemsDetailed(
    items: [DecodedLineItem], summary: LineItemSummary?
) -> ReconcileResult {
    guard let summary else {
        return ReconcileResult(
            status: "no-baseline", itemSum: nil, baseline: nil
        )
    }
    let tax = summary.tax
    // Figure hygiene: a zero/negative printed figure is no figure at all,
    // and neither is an impossible one.
    var subtotal = summary.subtotal
    var grand = summary.grandTotal
    if let s = subtotal, !(s > 0 && s <= maxPlausibleBaseline) {
        subtotal = nil
    }
    if let g = grand, !(g > 0 && g <= maxPlausibleBaseline) { grand = nil }

    var fallback: Double?
    if let g = grand {
        let f = pythonRound2(g - (tax ?? 0.0))
        fallback = f > 0 ? f : nil
    }

    var baseline: Double
    var source: String
    if let s = subtotal {
        baseline = s
        source = "subtotal"
    } else if let f = fallback {
        baseline = f
        source = "grand_total_minus_tax"
    } else {
        return ReconcileResult(
            status: "no-baseline", itemSum: nil, baseline: nil
        )
    }

    let itemSum = pythonRound2(items.reduce(0.0) { $0 + $1.price })
    var status = classifyAgainst(itemSum: itemSum, baseline: baseline)

    if status == "mismatch" {
        if source == "subtotal" {
            let s = subtotal!
            let insane =
                (grand != nil && s > grand! + 0.01)
                || baselineImplausible(itemSum: itemSum, baseline: s)
            if insane {
                if let f = fallback, abs(f - s) > 0.005,
                    !baselineImplausible(itemSum: itemSum, baseline: f),
                    classifyAgainst(itemSum: itemSum, baseline: f) != "mismatch"
                {
                    baseline = f
                    source = "grand_total_minus_tax"
                    status = classifyAgainst(itemSum: itemSum, baseline: f)
                } else {
                    return ReconcileResult(
                        status: "no-baseline", itemSum: nil, baseline: nil
                    )
                }
            }
        } else if baselineImplausible(itemSum: itemSum, baseline: baseline) {
            return ReconcileResult(
                status: "no-baseline", itemSum: nil, baseline: nil
            )
        }
    }

    var grade: Int?
    if status == "match" || status == "near" {
        grade = 1
        if source == "subtotal", let s = subtotal, let g = grand {
            if abs(pythonRound2(s + (tax ?? 0.0)) - g) <= max(0.02, g * 0.01) {
                grade = tax != nil ? 3 : 2
            }
        } else if source != "subtotal", tax != nil {
            // items ~= grand_total - printed tax: grand and tax both
            // corroborate; no printed subtotal so 3 is unreachable.
            grade = 2
        }
    }
    return ReconcileResult(
        status: status, itemSum: itemSum, baseline: baseline,
        baselineSource: source, baselineFiguresAgreeing: grade
    )
}

/// Port of `geometry.reconcile`: tuple-compatible wrapper kept for the
/// existing subtotal-only callers.
public func reconcileLineItems(
    items: [DecodedLineItem],
    subtotal: Double?, grandTotal: Double?, tax: Double?
) -> ReconcileResult {
    reconcileLineItemsDetailed(
        items: items,
        summary: LineItemSummary(
            subtotal: subtotal, tax: tax, grandTotal: grandTotal
        )
    )
}

/// PROVEN policy constant (user-decided 2026-08-03): exact-to-the-cent
/// means a difference strictly under half a cent.
public let provenCentTolerance: Double = 0.005

/// Port of `geometry.is_proven`: PROVEN = exact-to-the-cent on BOTH truth
/// hops. `near` NEVER counts, however small the band; missing figures on
/// either hop fail closed.
public func isProven(
    reconStatus: String?, printedTotal: Double?, bankAmount: Double?
) -> Bool {
    guard reconStatus == "match" else { return false }
    guard let printed = printedTotal, let bank = bankAmount else {
        return false
    }
    // Round the difference to the mill first: 21.075 - 21.07 computes to
    // 0.004999... in binary floats, and a half-cent gap must NOT slip under
    // the strict < 0.005 policy line on representation noise alone.
    return (abs(printed - bank) * 1000).rounded(.toNearestOrEven) / 1000
        < provenCentTolerance
}
