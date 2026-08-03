import { fireEvent, render, screen } from "@testing-library/react";
import TruthPanel from "./TruthPanel";
import { failureHint } from "./truthChain";
import {
  ReceiptDossier,
  ValidationItem,
  ValidationReceipt,
  ValidationSummary,
} from "./types";

const dossier = (overrides: Partial<ReceiptDossier> = {}): ReceiptDossier => ({
  failure_mode: "H-zone-gap-missing-items",
  diagnosis: "The ITEMS section stops four rows above the subtotal.",
  evidence: ["bands 18-21 are priced rows no item claimed"],
  proposal: {
    tool: "extend_items_section",
    args: { line_ids: [18, 19, 20, 21] },
    dry_run: {
      before_delta: -4.18,
      after_delta: 0,
      before_status: "mismatch",
      after_status: "match",
    },
  },
  abstain_reason: null,
  verdict_recommendation: "approve-fix",
  confidence: "high",
  signals_concurring: ["arithmetic", "vision"],
  generated_at: "2026-08-03T12:00:00Z",
  author: "scout",
  source: "image-1-1.json",
  ...overrides,
});

const item = (overrides: Partial<ValidationItem> = {}): ValidationItem => ({
  item_index: 0,
  name: "ORGANIC BANANAS",
  price: 6.68,
  quantity: null,
  unit_price: null,
  is_discount: false,
  line_ids: [2, 3],
  name_quality: "ok",
  reconciliation_status: "match",
  extractor_version: "line-items-blocks-v2",
  ...overrides,
});

const summary = (
  overrides: Partial<ValidationSummary> = {},
): ValidationSummary => ({
  subtotal: 6.68,
  grand_total: 7.18,
  tax: 0.5,
  baseline: 6.68,
  merchant_name: "Sprouts Farmers Market",
  tender_class: "card",
  card_network: "VISA",
  card_last4: "4242",
  ledger: "chase",
  bank_amount: 7.18,
  bank_match_confidence: 0.9,
  ...overrides,
});

const receipt = (
  overrides: Partial<ValidationReceipt> = {},
): ValidationReceipt => ({
  image_id: "image-1",
  receipt_id: 1,
  merchant_name: "Sprouts Farmers Market",
  item_count: 1,
  items: [item()],
  items_sum: 6.68,
  delta: 0,
  reconciliation_status: "match",
  items_section_line_ids: [2, 3],
  items_section_status: "VALID",
  image: {
    image_id: "image-1",
    receipt_id: 1,
    width: 600,
    height: 1200,
    cdn_s3_key: "receipt.jpg",
  },
  lines: [],
  sections: [],
  summary: summary(),
  dossier: null,
  dossier_error: null,
  reviews: [],
  ...overrides,
});

const agreements = () =>
  Object.fromEntries(
    ["items", "subtotal", "total", "bank"].map((key) => [
      key,
      screen.getByTestId(`truth-row-${key}`).getAttribute("data-agreement"),
    ]),
  );

const renderPanel = (
  value: ValidationReceipt,
  onReview = jest.fn(),
  onFlagRequest = jest.fn(),
) => {
  render(
    <TruthPanel
      receipt={value}
      onHoverItem={jest.fn()}
      onReview={onReview}
      onFlagRequest={onFlagRequest}
      saving={false}
    />,
  );
  return { onReview, onFlagRequest };
};

describe("TruthPanel truth chain", () => {
  it("paints every link green when all four figures agree", () => {
    renderPanel(receipt());
    expect(agreements()).toEqual({
      items: "agree",
      subtotal: "agree",
      total: "agree",
      bank: "agree",
    });
  });

  it("marks the items hop red when the sum overshoots the baseline", () => {
    renderPanel(
      receipt({
        items: [item({ price: 31.29, reconciliation_status: "mismatch" })],
        items_sum: 31.29,
        delta: 24.61,
        reconciliation_status: "mismatch",
      }),
    );
    const result = agreements();
    expect(result.items).toBe("disagree");
    expect(result.subtotal).toBe("disagree");
    // The printed figures still agree with each other and with the bank.
    expect(result.total).toBe("agree");
    expect(result.bank).toBe("agree");
  });

  it("marks a small overshoot as near, not a mismatch", () => {
    renderPanel(
      receipt({
        items_sum: 7.18,
        delta: 0.5,
        reconciliation_status: "near",
      }),
    );
    expect(agreements().items).toBe("near");
  });

  it("leaves the subtotal hop unknown when only a total was printed", () => {
    renderPanel(
      receipt({
        summary: summary({ subtotal: null, baseline: 6.68 }),
      }),
    );
    const result = agreements();
    expect(result.items).toBe("agree");
    expect(result.subtotal).toBe("unknown");
    expect(result.total).toBe("unknown");
    expect(result.bank).toBe("agree");
  });

  it("leaves the bank hop unknown when no transaction matched", () => {
    renderPanel(receipt({ summary: summary({ bank_amount: null }) }));
    expect(agreements().bank).toBe("unknown");
  });

  it("flags a bank amount that disagrees with the printed total", () => {
    renderPanel(receipt({ summary: summary({ bank_amount: 19.97 }) }));
    const result = agreements();
    expect(result.bank).toBe("disagree");
    expect(result.items).toBe("agree");
  });

  it("marks every hop unknown when nothing was extracted", () => {
    renderPanel(receipt({ summary: null, delta: null }));
    expect(agreements()).toEqual({
      items: "unknown",
      subtotal: "unknown",
      total: "unknown",
      bank: "unknown",
    });
  });

  it("renders the tender badges from the summary", () => {
    renderPanel(receipt());
    const badges = screen.getByTestId("tender-badges");
    expect(badges).toHaveTextContent("card");
    expect(badges).toHaveTextContent("VISA");
    expect(badges).toHaveTextContent("4242");
    expect(badges).toHaveTextContent("chase");
  });

  it("confirms immediately and opens the note flow for flags", () => {
    const { onReview, onFlagRequest } = renderPanel(receipt());
    fireEvent.click(screen.getByRole("button", { name: /Confirm/ }));
    fireEvent.click(screen.getByRole("button", { name: /Flag with note/ }));
    expect(onReview).toHaveBeenCalledWith("confirm", "");
    expect(onFlagRequest).toHaveBeenCalledTimes(1);
  });

  it("renders the agent's diagnosis, evidence and dry-run deltas", () => {
    renderPanel(receipt({ dossier: dossier() }));
    expect(screen.getByTestId("dossier-mode")).toHaveTextContent(
      "H-zone-gap-missing-items",
    );
    expect(screen.getByTestId("dossier-diagnosis")).toHaveTextContent(
      "stops four rows above the subtotal",
    );
    expect(screen.getByTestId("dossier-evidence")).toHaveTextContent(
      "bands 18-21",
    );
    const dryRun = screen.getByTestId("dossier-dry-run");
    expect(dryRun).toHaveTextContent("-$4.18");
    expect(dryRun).toHaveTextContent("$0.00");
    expect(dryRun).toHaveTextContent("mismatch");
    expect(dryRun).toHaveTextContent("match");
  });

  it("renders an abstention rather than inventing a proposal", () => {
    renderPanel(
      receipt({
        dossier: dossier({
          proposal: null,
          abstain_reason: "The fix would close the delta but break the status.",
        }),
      }),
    );
    expect(screen.getByTestId("dossier-abstain")).toHaveTextContent(
      "break the status",
    );
    expect(screen.queryByTestId("dossier-proposal")).not.toBeInTheDocument();
  });

  it("says so when a proposal was never simulated", () => {
    renderPanel(
      receipt({
        dossier: dossier({
          proposal: { tool: "extend_items_section", args: {}, dry_run: null },
        }),
      }),
    );
    expect(screen.getByTestId("dossier-no-dry-run")).toBeInTheDocument();
  });

  it("shows the missing-dossier state instead of an empty panel", () => {
    renderPanel(receipt());
    expect(screen.getByTestId("dossier-empty")).toBeInTheDocument();
  });

  it("surfaces an unreadable dossier file", () => {
    renderPanel(receipt({ dossier_error: "image-1-1.json: bad JSON" }));
    expect(screen.getByTestId("dossier-error")).toHaveTextContent("bad JSON");
  });

  it("approves a fix with the dossier's mode and target rows", () => {
    const { onReview } = renderPanel(receipt({ dossier: dossier() }));
    fireEvent.click(screen.getByRole("button", { name: /Approve fix/ }));
    expect(onReview).toHaveBeenCalledWith("approve-fix", "", {
      reason: "H-zone-gap-missing-items",
      line_ids: [18, 19, 20, 21],
    });
  });

  it("disables approve-fix when the dossier proposed nothing", () => {
    renderPanel(receipt({ dossier: dossier({ proposal: null }) }));
    expect(screen.getByRole("button", { name: /Approve fix/ })).toBeDisabled();
  });

  it("allows golden only when the receipt matches and the bank agrees", () => {
    const { onReview } = renderPanel(receipt());
    const golden = screen.getByRole("button", { name: /Golden/ });
    expect(golden).toBeEnabled();
    fireEvent.click(golden);
    expect(onReview).toHaveBeenCalledWith("golden", "");
  });

  it("blocks golden when no bank transaction settled the printed total", () => {
    renderPanel(receipt({ summary: summary({ bank_amount: null }) }));
    expect(screen.getByRole("button", { name: /Golden/ })).toBeDisabled();
  });

  it("blocks golden on a receipt that does not reconcile", () => {
    renderPanel(
      receipt({
        items_sum: 2.5,
        delta: -4.18,
        reconciliation_status: "mismatch",
      }),
    );
    expect(screen.getByRole("button", { name: /Golden/ })).toBeDisabled();
  });

  it("keeps incomplete receipts visible as explicit review targets", () => {
    renderPanel(
      receipt({ image: null, sections: [], items: [], item_count: 0 }),
    );
    expect(screen.getByTestId("review-target")).toHaveTextContent(
      "Missing image + sections + items",
    );
    expect(screen.getByText("No line items extracted.")).toBeInTheDocument();
  });
});

describe("failureHint", () => {
  it("returns nothing when the receipt reconciles", () => {
    expect(failureHint(receipt())).toBeNull();
  });

  it("names the missing baseline first", () => {
    const hint = failureHint(
      receipt({ summary: summary({ baseline: null }), delta: null }),
    );
    expect(hint?.code).toBe("baseline");
  });

  it("spots a promo netting overshoot", () => {
    const hint = failureHint(
      receipt({
        items: [item(), item({ item_index: 1, price: -2, is_discount: true })],
        items_sum: 8.68,
        delta: 2,
      }),
    );
    expect(hint?.code).toBe("promo");
  });

  it("spots a summary figure that leaked into the items", () => {
    const hint = failureHint(
      receipt({
        items: [item(), item({ item_index: 1, price: 7.18 })],
        items_sum: 13.86,
        delta: 7.18,
      }),
    );
    expect(hint?.code).toBe("summary-band");
  });

  it("calls a short sum an items-zone gap", () => {
    const hint = failureHint(receipt({ items_sum: 2.5, delta: -4.18 }));
    expect(hint?.code).toBe("zone-gap");
  });

  it("falls back to an unexplained overshoot", () => {
    const hint = failureHint(receipt({ items_sum: 20, delta: 13.32 }));
    expect(hint?.code).toBe("overshoot");
  });
});
