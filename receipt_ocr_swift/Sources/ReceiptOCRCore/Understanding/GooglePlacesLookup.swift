import Foundation

#if canImport(FoundationNetworking)
  import FoundationNetworking
#endif

#if os(macOS)

  /// Conservative adapter for the existing Google Places Text Search service.
  ///
  /// It is called only after known-receipt resolution abstains. Google response
  /// rank alone is never treated as confidence: the returned place must match
  /// receipt identity fields strongly enough to clear the resolver's 0.8 gate.
  public struct GooglePlacesLookup: PlacesLookingUp {
    private struct SearchResponse: Decodable {
      struct Place: Decodable {
        struct DisplayName: Decodable {
          let text: String
        }
        let id: String
        let displayName: DisplayName?
        let formattedAddress: String?
        let nationalPhoneNumber: String?
        let websiteUri: String?
      }
      let places: [Place]?
    }

    private let apiKey: String
    private let endpoint: URL
    private let session: URLSession

    public init(
      apiKey: String,
      endpoint: URL = URL(
        string: "https://places.googleapis.com/v1/places:searchText"
      )!,
      timeout: TimeInterval = 20
    ) throws {
      let key = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
      guard !key.isEmpty else {
        throw ReceiptEmbeddingError.invalidConfiguration(
          "Google Places API key must not be blank"
        )
      }
      self.apiKey = key
      self.endpoint = endpoint
      let configuration = URLSessionConfiguration.ephemeral
      configuration.timeoutIntervalForRequest = timeout
      configuration.timeoutIntervalForResource = timeout
      self.session = URLSession(configuration: configuration)
    }

    public func lookup(
      identity: ReceiptIdentitySignals
    ) async throws -> PlacesCandidate? {
      let textQuery =
        (identity.merchantNames.prefix(1)
        + identity.addresses.prefix(1)
        + identity.phoneNumbers.prefix(1)).joined(separator: " ")
      guard !textQuery.isEmpty else { return nil }

      var request = URLRequest(url: endpoint)
      request.httpMethod = "POST"
      request.httpBody = try JSONSerialization.data(
        withJSONObject: ["textQuery": textQuery]
      )
      request.setValue("application/json", forHTTPHeaderField: "Content-Type")
      request.setValue(apiKey, forHTTPHeaderField: "X-Goog-Api-Key")
      request.setValue(
        "places.id,places.displayName,places.formattedAddress,"
          + "places.nationalPhoneNumber,places.websiteUri",
        forHTTPHeaderField: "X-Goog-FieldMask"
      )
      let (data, response) = try await session.data(for: request)
      guard let http = response as? HTTPURLResponse,
        (200..<300).contains(http.statusCode)
      else {
        let status = (response as? HTTPURLResponse)?.statusCode ?? -1
        throw ReceiptEmbeddingError.http(
          status: status,
          body: String(data: data, encoding: .utf8) ?? "<invalid response>"
        )
      }
      let places =
        try JSONDecoder().decode(
          SearchResponse.self,
          from: data
        ).places ?? []
      let scored = places.compactMap {
        candidate($0, identity: identity)
      }.sorted {
        $0.confidence == $1.confidence
          ? $0.placeID < $1.placeID : $0.confidence > $1.confidence
      }
      guard let top = scored.first, top.confidence >= 0.8 else { return nil }
      if scored.count > 1,
        abs(top.confidence - scored[1].confidence) < 0.05
      {
        return nil
      }
      return top
    }

    private func candidate(
      _ place: SearchResponse.Place,
      identity: ReceiptIdentitySignals
    ) -> PlacesCandidate? {
      guard let name = place.displayName?.text, !name.isEmpty else {
        return nil
      }
      var score = 0.0
      var matched: [String] = []
      let nameScore =
        identity.merchantNames.map {
          tokenSimilarity($0, name)
        }.max() ?? 0
      if nameScore >= 0.7 {
        score += 0.4 * nameScore
        matched.append("merchant_name")
      }
      if let address = place.formattedAddress,
        identity.addresses.contains(where: {
          let left = normalize($0)
          let right = normalize(address)
          return !left.isEmpty && !right.isEmpty
            && (left.contains(right) || right.contains(left))
        })
      {
        score += 0.4
        matched.append("address")
      }
      if let phone = place.nationalPhoneNumber,
        identity.phoneNumbers.contains(where: {
          let left = phoneDigits($0)
          let right = phoneDigits(phone)
          return !left.isEmpty && left == right
        })
      {
        score += 0.6
        matched.append("phone")
      }
      if let website = place.websiteUri,
        identity.websites.contains(where: {
          let left = normalizeURL($0)
          let right = normalizeURL(website)
          return !left.isEmpty && left == right
        })
      {
        score += 0.5
        matched.append("website")
      }
      return PlacesCandidate(
        merchantName: name,
        placeID: place.id,
        formattedAddress: place.formattedAddress,
        phoneNumber: place.nationalPhoneNumber,
        website: place.websiteUri,
        confidence: min(score, 0.99),
        matchedFields: matched
      )
    }

    private func normalize(_ value: String) -> String {
      value.lowercased().filter { $0.isLetter || $0.isNumber }
    }

    private func tokenSimilarity(_ lhs: String, _ rhs: String) -> Double {
      let left = Set(
        lhs.lowercased().split(whereSeparator: { !$0.isLetter && !$0.isNumber })
      )
      let right = Set(
        rhs.lowercased().split(whereSeparator: { !$0.isLetter && !$0.isNumber })
      )
      guard !left.isEmpty, !right.isEmpty else { return 0 }
      return Double(left.intersection(right).count)
        / Double(left.union(right).count)
    }

    private func phoneDigits(_ value: String) -> String {
      var digits = String(value.filter(\.isNumber))
      if digits.count == 11, digits.hasPrefix("1") {
        digits.removeFirst()
      }
      guard digits.count == 10, Set(digits).count > 1 else { return "" }
      return digits
    }

    private func normalizeURL(_ value: String) -> String {
      normalize(value)
        .replacingOccurrences(of: "https", with: "")
        .replacingOccurrences(of: "http", with: "")
        .replacingOccurrences(of: "www", with: "")
    }
  }

#endif
