import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import AuditDeck from "./AuditDeck";
import { fetchAuditDeck, fetchAuditReceipt, postAuditVerdict } from "./client";
import { AuditReceipt } from "./types";

jest.mock("./client", () => ({
  fetchAuditDeck: jest.fn(),
  fetchAuditReceipt: jest.fn(),
  postAuditVerdict: jest.fn(),
}));

jest.mock("../../ui/Figures/ReceiptFlow/useImageFormatSupport", () => ({
  useImageFormatSupport: () => ({ supportsAVIF: false, supportsWebP: false }),
}));

const mockedFetchAuditDeck = jest.mocked(fetchAuditDeck);
const mockedFetchAuditReceipt = jest.mocked(fetchAuditReceipt);
const mockedPostAuditVerdict = jest.mocked(postAuditVerdict);

const blindReceipt = (): AuditReceipt => ({
  pass_id: "pass-1",
  blind: true,
  image_id: "image-1",
  receipt_id: 1,
  merchant_name: "Sprouts Farmers Market",
  item_count: 2,
  items: [
    {
      item_index: 1,
      name: "BANANAS",
      price: 1.29,
      quantity: null,
      unit_price: null,
      is_discount: false,
      line_ids: [4],
      name_quality: null,
      reconciliation_status: "match",
      extractor_version: null,
    },
  ],
  items_sum: 1.29,
  delta: 0,
  reconciliation_status: "match",
  items_section_line_ids: null,
  items_section_status: null,
  image: null,
  lines: [],
  sections: [],
  summary: null,
  // The shim blanks the agent's conclusions; only its raw observations survive.
  dossier: {
    failure_mode: null,
    diagnosis: "",
    evidence: ["ITEMS section: 14 lines, status VALID"],
    proposal: null,
    abstain_reason: null,
    verdict_recommendation: null,
    confidence: null,
    signals_concurring: [],
    generated_at: null,
    author: "scout-session1",
    source: "image-1-1.json",
    blind: true,
  },
  dossier_error: null,
  reviews: [],
});

const deck = (frozen: string[] = []) => ({
  pass_id: "pass-1",
  size: 3,
  total_auto: 30,
  frozen,
  sample: [
    {
      image_id: "image-1",
      receipt_id: 1,
      merchant: "Sprouts Farmers Market",
      reviewed: false,
    },
    {
      image_id: "image-2",
      receipt_id: 1,
      merchant: "Smith's",
      reviewed: false,
    },
    {
      image_id: "image-3",
      receipt_id: 1,
      merchant: "Gelson's",
      reviewed: false,
    },
  ],
});

const reviewResponse = (verdict: "audit-agree" | "audit-disagree") => ({
  entry: {
    image_id: "image-1",
    receipt_id: 1,
    verdict,
    note: "",
    merchant: "Sprouts Farmers Market",
    status: "match",
    delta: 0,
    author: "user",
    ts: "2026-08-03T20:00:00.000Z",
  },
  revealed: {
    tier: "T0",
    reason: "auto-extension",
    failure_mode: "H-zone-gap-missing-items",
    diagnosis: "ITEMS stops short of the subtotal.",
    verdict_recommendation: "approve-fix",
    confidence: "high",
    signals_concurring: ["arithmetic", "vision"],
    proposal: {
      tool: "extend_items_section",
      args: { line_ids: [40, 41] },
      dry_run: null,
    },
    abstain_reason: null,
  },
  freeze_written: verdict === "audit-disagree" ? ["H", "T0"] : [],
  frozen: verdict === "audit-disagree" ? ["H", "T0"] : [],
});

beforeEach(() => {
  jest.clearAllMocks();
  mockedFetchAuditDeck.mockResolvedValue(deck());
  mockedFetchAuditReceipt.mockResolvedValue(blindReceipt());
});

describe("AuditDeck blind review", () => {
  it("hides the agent's verdict until the human commits, then reveals it", async () => {
    mockedPostAuditVerdict.mockResolvedValue(reviewResponse("audit-agree"));
    render(<AuditDeck />);

    expect(await screen.findByTestId("blind-notice")).toHaveTextContent(
      "hidden until you commit",
    );
    // Nothing on screen may name the agent's conclusion before the verdict.
    expect(screen.queryByTestId("audit-reveal")).not.toBeInTheDocument();
    expect(screen.queryByText(/approve-fix/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/H-zone-gap-missing-items/),
    ).not.toBeInTheDocument();
    expect(await screen.findByTestId("audit-evidence")).toHaveTextContent(
      "ITEMS section: 14 lines",
    );

    fireEvent.click(screen.getByRole("button", { name: /Agree with the agent/ }));

    const reveal = await screen.findByTestId("audit-reveal");
    expect(reveal).toHaveTextContent("H-zone-gap-missing-items");
    expect(reveal).toHaveTextContent("approve-fix");
    expect(reveal).toHaveTextContent("high confidence");
    await waitFor(() =>
      expect(mockedPostAuditVerdict).toHaveBeenCalledWith(
        expect.objectContaining({
          verdict: "audit-agree",
          image_id: "image-1",
          pass_id: "pass-1",
        }),
      ),
    );
    // One verdict per card: the buttons are gone once it is committed.
    expect(
      screen.queryByRole("button", { name: /Agree with the agent/ }),
    ).not.toBeInTheDocument();
  });

  it("freezes the failure class on a disagreement and says so", async () => {
    mockedPostAuditVerdict.mockResolvedValue(reviewResponse("audit-disagree"));
    const onFrozen = jest.fn();
    render(<AuditDeck onFrozen={onFrozen} />);

    // The verdict buttons stay disabled until the blind receipt has landed.
    await screen.findByTestId("audit-evidence");
    fireEvent.click(screen.getByRole("button", { name: /Disagree/ }));

    expect(await screen.findByTestId("freeze-written")).toHaveTextContent(
      "Tier frozen: H, T0",
    );
    await waitFor(() =>
      expect(onFrozen).toHaveBeenLastCalledWith(["H", "T0"]),
    );
    expect(mockedPostAuditVerdict).toHaveBeenCalledWith(
      expect.objectContaining({ verdict: "audit-disagree" }),
    );
  });

  it("carries the audit note into the committed verdict", async () => {
    mockedPostAuditVerdict.mockResolvedValue(reviewResponse("audit-disagree"));
    render(<AuditDeck />);

    await screen.findByTestId("audit-evidence");
    fireEvent.change(screen.getByRole("textbox", { name: "Audit note" }), {
      target: { value: "row 3 is a tender line, not a product" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Disagree/ }));

    await waitFor(() =>
      expect(mockedPostAuditVerdict).toHaveBeenCalledWith(
        expect.objectContaining({
          note: "row 3 is a tender line, not a product",
        }),
      ),
    );
  });

  it("moves through the sampled deck", async () => {
    render(<AuditDeck />);
    await screen.findByTestId("blind-notice");
    expect(screen.getByText("1 / 3")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /next/ }));
    await waitFor(() =>
      expect(mockedFetchAuditReceipt).toHaveBeenCalledWith("image-2", 1),
    );
    expect(screen.getByText("2 / 3")).toBeInTheDocument();
  });

  it("says when a pass applied nothing on its own", async () => {
    mockedFetchAuditDeck.mockResolvedValue({
      pass_id: "pass-1",
      size: 0,
      total_auto: 0,
      sample: [],
      frozen: [],
    });
    render(<AuditDeck />);
    expect(await screen.findByTestId("audit-empty")).toHaveTextContent(
      "applied no verdicts on its own",
    );
  });
});
