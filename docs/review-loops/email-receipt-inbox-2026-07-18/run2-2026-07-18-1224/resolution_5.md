# Resolution — Review Round 5

1. [med] FIXED — Removed the broken `external_active_rule_set` escape hatch. The
   store rule is always created inside this component's own `rule_set`, so it can
   never be "merged" into a foreign externally-managed active set, and the boolean
   added no dependency on any external activation resource — MX could publish while
   mail routed to an unrelated active set and was dropped. The mode was also unused
   (default `False`; the sole caller `infra/__main__.py:1607` never passes it).
   Chose the "remove this mode" remediation over the external-rule-set redesign
   (option 2), which would add substantial complexity — creating the store rule
   inside a foreign set + threading an external activation resource — for a caller
   that does not exist. MX is now published only when `activation is not None`
   (i.e. this component activated its own rule set), and the MX record depends on
   that activation, versioning, notification, and the store rule.
