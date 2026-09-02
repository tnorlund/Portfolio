/**
 * Numbers baked into the /email page. Pulled from the local
 * email-receipts SQLite primary and the SES inbox bucket on 2026-09-01.
 * Counts and shares only — never dollar amounts.
 */

export const EMAIL_DATA_AS_OF = "2026-09-01";

export interface FunnelStage {
  label: string;
  count: number;
}

/** Thirteen years of email, narrowed to unique receipts. */
export const EMAIL_FUNNEL: FunnelStage[] = [
  { label: "Messages indexed", count: 162333 },
  { label: "From receipt senders", count: 30255 },
  { label: "Receipt rows parsed", count: 4169 },
  { label: "Unique receipts", count: 3845 },
];

export interface SenderGroup {
  group: string;
  label: string;
  receipts: number;
}

/** Unique outflow receipts per sender group (all groups). */
export const SENDER_GROUPS: SenderGroup[] = [
  { group: "doordash", label: "DoorDash", receipts: 770 },
  { group: "apple", label: "Apple", receipts: 727 },
  { group: "amazon", label: "Amazon", receipts: 677 },
  { group: "services", label: "Services", receipts: 311 },
  { group: "paypal", label: "PayPal", receipts: 258 },
  { group: "retail", label: "Retail", receipts: 236 },
  { group: "venmo", label: "Venmo", receipts: 223 },
  { group: "equinox", label: "Equinox", receipts: 137 },
  { group: "pos-restaurants", label: "Restaurant POS", receipts: 129 },
  { group: "uber", label: "Uber", receipts: 68 },
  { group: "sce", label: "SCE", receipts: 66 },
  { group: "restaurant-platforms", label: "Restaurant platforms", receipts: 63 },
  { group: "costco-warehouse", label: "Costco warehouse", receipts: 28 },
  { group: "travel-housing", label: "Travel & housing", receipts: 17 },
  { group: "github", label: "GitHub", receipts: 10 },
];

/** Top N groups with the tail folded into "Other" (dataviz: ≤7 classes). */
export function senderCensus(top = 7): SenderGroup[] {
  const sorted = [...SENDER_GROUPS].sort((a, b) => b.receipts - a.receipts);
  const head = sorted.slice(0, top);
  const tail = sorted.slice(top);
  const other = tail.reduce((sum, g) => sum + g.receipts, 0);
  return other > 0
    ? [...head, { group: "other", label: "Other", receipts: other }]
    : head;
}

export interface CoveragePoint {
  month: string; // YYYY-MM
  personal: number; // share of card purchases with a matched receipt
  business: number;
}

/** Count-based coverage share by month. Aug 2026 not reconciled yet. */
export const COVERAGE_BY_MONTH: CoveragePoint[] = [
  { month: "2025-09", personal: 0.154, business: 0.196 },
  { month: "2025-10", personal: 0.364, business: 0.186 },
  { month: "2025-11", personal: 0.207, business: 0.195 },
  { month: "2025-12", personal: 0.143, business: 0.171 },
  { month: "2026-01", personal: 0.174, business: 0.057 },
  { month: "2026-02", personal: 0.286, business: 0.049 },
  { month: "2026-03", personal: 0.388, business: 0.076 },
  { month: "2026-04", personal: 0.156, business: 0.036 },
  { month: "2026-05", personal: 0.242, business: 0.032 },
  { month: "2026-06", personal: 0.444, business: 0.012 },
  { month: "2026-07", personal: 0.128, business: 0.007 },
];

/** The SES inbox since it went live on 2026-07-23. */
export const SES_INBOX = {
  since: "2026-07-23",
  forwarded: 1189,
  gateRejected: 14,
  receipts: 59,
  githubNotifications: 756,
};
