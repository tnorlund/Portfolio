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
    /// SETTLEMENT_RE: settlement lines are never items
    static let settlement = Rx(
        "^\\W*(?:BALANCE(?:\\s+DUE|\\s+TO\\s+PAY)?|TO\\s+PAY|CREDIT|DEBIT"
            + "|CHANGE(?:\\s+DUE)?|CASH(?:\\s+BACK)?|TENDER(?:ED)?"
            + "|SUB\\s*TOTAL|TOTAL|(?:SALES\\s+)?TAX)\\W*$",
        ci: true
    )
    /// WAS_PRICE_RE: price-comparison metadata ("WAS: $3.59 each")
    static let wasPrice = Rx("\\b(?:WAS|REG)\\b[:.]?\\s*\\$?\\d", ci: true)
    /// SALE_PRICE_RE: BOGO annotation echo ("Sale Price 1.99")
    static let salePrice = Rx("\\bSALE\\s+PRICE\\b", ci: true)
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
    /// Bare "2 3.99" qty + unit price (fullmatch)
    static let bareQtyPrice = Rx("(\\d{1,2})\\s+\\$?(\\d+\\.\\d{2})")
    static let digits12 = Rx("\\d{1,2}")
    /// Strips amounts before the settlement vocabulary test
    static let amountStrip = Rx("\\$?\\d[\\d.,]*")
    /// merge_price_fragments: left token "1." / "5," / "$12."
    static let fragmentLeft = Rx("\\$?\\d{1,4}[.,]")
    static let fragmentRight = Rx("\\d{2}")
}

/// DISCOUNT_WORDS
let discountWords = ["SAVED", "SAVING", "OFF", "COUPON", "DISCOUNT", "PROMO"]

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
        (price != nil && price! < 0)
        || discountWords.contains { upper.contains($0) }

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

/// Port of `blocks.decode_band_blocks`: block decode over deskewed visual
/// bands.
func decodeBandBlocks(
    words: [ZoneWord], zoneLineIds: Set<Int>, priors: [String: RolePrior]
) -> [ParsedBand] {
    var bands = zoneBands(words: words, zoneLineIds: zoneLineIds)
    if bands.isEmpty { return [] }

    for idx in bands.indices {
        let text = bands[idx].text
        // Settlement / WAS / SALE-PRICE bands are never items; strip
        // amounts before the settlement test so "CHANGE 0.00" reduces to
        // its vocabulary.
        let bare = pyStrip(LineItemRegex.amountStrip.sub(text, with: " "))
        if LineItemRegex.settlement.match(bare) != nil
            || LineItemRegex.wasPrice.hasMatch(text)
            || LineItemRegex.salePrice.hasMatch(text)
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
    return items
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
    words: [ZoneWord], zoneLineIds: Set<Int>
) -> [DecodedLineItem] {
    extractLineItems(
        words: words, zoneLineIds: zoneLineIds, priors: defaultPriors
    )
}

/// Priors are immutable after load; cache them process-wide.
private let defaultPriors: [String: RolePrior] = loadDefaultPriors()

/// `extract_items` with explicit priors (parity with
/// `decode_band_blocks(ocr, priors)`).
public func extractLineItems(
    words: [ZoneWord], zoneLineIds: Set<Int>, priors: [String: RolePrior]
) -> [DecodedLineItem] {
    decodeBandBlocks(
        words: words, zoneLineIds: zoneLineIds, priors: priors
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

/// Result of `reconcileLineItems` (Python `geometry.reconcile`).
public struct ReconcileResult: Sendable {
    public let status: String  // "no-baseline" | "match" | "near" | "mismatch"
    public let itemSum: Double?
    public let baseline: Double?
}

/// Port of `geometry.reconcile`: compare extracted item sum against the
/// summary subtotal / grand total.
public func reconcileLineItems(
    items: [DecodedLineItem],
    subtotal: Double?, grandTotal: Double?, tax: Double?
) -> ReconcileResult {
    var baseline = subtotal
    if baseline == nil, let grand = grandTotal {
        baseline = grand - (tax ?? 0.0)
    }
    guard let base = baseline, base > 0 else {
        return ReconcileResult(status: "no-baseline", itemSum: nil, baseline: nil)
    }
    let itemSum = pythonRound2(items.reduce(0.0) { $0 + $1.price })
    let diff = abs(itemSum - base)
    if diff <= max(0.02, base * 0.01) {
        return ReconcileResult(status: "match", itemSum: itemSum, baseline: base)
    }
    if diff <= max(1.0, base * 0.10) {
        return ReconcileResult(status: "near", itemSum: itemSum, baseline: base)
    }
    return ReconcileResult(status: "mismatch", itemSum: itemSum, baseline: base)
}
