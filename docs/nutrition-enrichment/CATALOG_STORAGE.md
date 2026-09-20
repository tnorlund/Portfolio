# Catalog storage checkpoint

Milestone B is split into B1 (product/alias persistence) and B2 (receipt
generation publication). This file records B1; it does not claim B2 is done.

- FoodProduct uses append-only content revisions. Domain-to-DAL translation
  preserves canonical decimal strings and identity hashes; DAL imports no
  sibling receipt package. No product update/delete accessor exists.
- ProductAlias requires expected revision, an increment of exactly one, and
  an existing pinned product in the same transaction for matched decisions.
  Automatic writes cannot supersede a user decision even at a fresh revision.
  Explicit user corrections can supersede earlier decisions.
- Keys escape components to avoid delimiter collisions. Reads are strong;
  pagination is bounded and cursors cannot cross merchant/product scopes.
- Expiry is application-enforced. Expired records are retained, without the
  table's `time_to_live` attribute, so an old revision cannot recur after TTL
  deletion. This refines SPEC's earlier optional TTL-cleanup language.
- Facts use a 300 KB canonical payload limit and a conservative serialized
  item size limit. Raw provider bodies belong in pinned artifacts elsewhere.
- 52 entity/moto tests pass, including 100-item pagination, immutable writes,
  missing-product conditions, stale/updated model races, expiry, validation,
  and standard AWS error mapping. One domain-to-storage round-trip test covers
  every current contract product and verifies matching content hashes.
- Targeted mypy passes for all four new DAL implementation modules. AWS,
  correction fan-out, generation publication, and model quality remain untested.
- Three independent full-diff reviews: no HIGH/MEDIUM findings. Final pass
  includes the documented TTL refinement and package-specific import sorting;
  deployment and later milestones remain pending.

Per the package rules these new DAL tests are temporarily marked
`unused_in_production` until the infrastructure consumer is wired in E. They
are run explicitly at every local milestone; the ordinary CI selection skips
that marker. Remove it when E imports the accessors.
