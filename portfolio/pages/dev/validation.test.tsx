import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as client from "../../components/dev/validation/client";
import {
  fetchAuditDeck,
  fetchDigest,
  fetchQueues,
  fetchReceipt,
  fetchReviews,
  fetchWorklist,
  postReview,
} from "../../components/dev/validation/client";
import { ValidationReceipt } from "../../components/dev/validation/types";
import ValidationWorkstation from "./validation";

jest.mock("../../components/dev/validation/client", () => ({
  fetchAuditDeck: jest.fn(),
  fetchAuditReceipt: jest.fn(),
  fetchDigest: jest.fn(),
  fetchQueues: jest.fn(),
  fetchReceipt: jest.fn(),
  fetchReviews: jest.fn(),
  fetchWorklist: jest.fn(),
  postApprove: jest.fn(),
  postAuditVerdict: jest.fn(),
  postReview: jest.fn(),
}));

jest.mock(
  "../../components/ui/Figures/ReceiptFlow/useImageFormatSupport",
  () => ({
    useImageFormatSupport: () => ({ supportsAVIF: false, supportsWebP: false }),
  }),
);

const mockedFetchAuditDeck = jest.mocked(fetchAuditDeck);
const mockedFetchDigest = jest.mocked(fetchDigest);
const mockedFetchQueues = jest.mocked(fetchQueues);
const mockedFetchReceipt = jest.mocked(fetchReceipt);
const mockedFetchReviews = jest.mocked(fetchReviews);
const mockedFetchWorklist = jest.mocked(fetchWorklist);
const mockedPostReview = jest.mocked(postReview);

const detail = (receiptId: number): ValidationReceipt => ({
  image_id: `image-${receiptId}`,
  receipt_id: receiptId,
  merchant_name: receiptId === 1 ? "Alpha Market" : "Beta Market",
  item_count: 0,
  items: [],
  items_sum: 0,
  delta: -5,
  reconciliation_status: "mismatch",
  items_section_line_ids: null,
  items_section_status: null,
  image: null,
  lines: [],
  sections: [],
  summary: null,
  dossier: null,
  dossier_error: null,
  reviews: [],
});

const worklistRow = (receiptId: number, merchant: string) => ({
  image_id: `image-${receiptId}`,
  receipt_id: receiptId,
  merchant,
  status: "mismatch" as const,
  items: 0,
  items_sum: 0,
  baseline: 5,
  subtotal: 5,
  grand_total: 5,
  tax: 0,
  delta: -5,
  tender_class: null,
  card_network: null,
  card_last4: null,
  ledger: null,
  bank_amount: null,
  bank_match_confidence: null,
});

const showEscalation = async () => {
  fireEvent.click(screen.getByRole("tab", { name: /Escalation/ }));
  return screen.findByText("Alpha Market · receipt 1");
};

beforeEach(() => {
  jest.clearAllMocks();
  mockedFetchDigest.mockResolvedValue({
    pass_id: "pass-1",
    groups: [],
    passes: ["pass-1"],
    frozen: [],
    generated_at: null,
    source: "pass-1.jsonl",
  });
  mockedFetchAuditDeck.mockResolvedValue({
    pass_id: "pass-1",
    size: 0,
    total_auto: 0,
    sample: [],
    frozen: [],
  });
  mockedFetchReviews.mockResolvedValue({ entries: [], log: "/tmp/reviews" });
  mockedFetchQueues.mockResolvedValue({
    queues: [
      {
        name: "session-1",
        count: 2,
        description: "Smith's + Gelson's",
        error: null,
      },
      { name: "session-2", count: 1, description: null, error: null },
    ],
    dir: "/tmp/queues",
  });
  mockedFetchWorklist.mockResolvedValue({
    queue: "session-1",
    matching: 2,
    built_at: "now",
    receipts: [worklistRow(1, "Alpha Market"), worklistRow(2, "Beta Market")],
  });
  mockedFetchReceipt.mockImplementation(async (_imageId, receiptId) =>
    detail(receiptId),
  );
  mockedPostReview.mockResolvedValue({
    image_id: "image-1",
    receipt_id: 1,
    verdict: "confirm",
    note: "",
    merchant: "Alpha Market",
    status: "mismatch",
    delta: -5,
    author: "user",
    ts: "2026-08-03T20:00:00.000Z",
  });
});

describe("ValidationWorkstation three-screen shell", () => {
  it("opens on the digest and switches between the three screens", async () => {
    render(<ValidationWorkstation />);
    expect(await screen.findByTestId("digest-panel")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Digest/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    fireEvent.click(screen.getByRole("tab", { name: /Audit/ }));
    expect(await screen.findByTestId("audit-deck")).toBeInTheDocument();
    expect(screen.queryByTestId("digest-panel")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Escalation/ }));
    expect(await screen.findByTestId("truth-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("audit-deck")).not.toBeInTheDocument();
  });
});

describe("ValidationWorkstation escalation screen", () => {
  it("defaults to the first available queue, with no browse or filters", async () => {
    render(<ValidationWorkstation />);
    await showEscalation();

    expect(mockedFetchWorklist).toHaveBeenCalledWith("session-1");
    expect(
      screen.getByRole("combobox", { name: "Escalation queue" }),
    ).toHaveValue("session-1");
    // The merchant browse panel and status chips are gone for good.
    expect(screen.queryByTestId("merchant-panel")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("searchbox", { name: "Search merchants" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("group", { name: "Status filter" }),
    ).not.toBeInTheDocument();
  });

  it("loads another queue when the selector changes", async () => {
    render(<ValidationWorkstation />);
    await showEscalation();

    fireEvent.change(
      screen.getByRole("combobox", { name: "Escalation queue" }),
      { target: { value: "session-2" } },
    );
    await waitFor(() =>
      expect(mockedFetchWorklist).toHaveBeenCalledWith("session-2"),
    );
  });

  it("says so when no escalation queue is selected", async () => {
    mockedFetchQueues.mockResolvedValue({ queues: [], dir: "/tmp/queues" });
    render(<ValidationWorkstation />);
    fireEvent.click(screen.getByRole("tab", { name: /Escalation/ }));
    expect(await screen.findByTestId("no-queue")).toHaveTextContent(
      "No escalation queue selected",
    );
    expect(mockedFetchWorklist).not.toHaveBeenCalled();
  });

  it("keeps the keyboard verdict flow", async () => {
    render(<ValidationWorkstation />);
    await showEscalation();

    fireEvent.keyDown(window, { key: "j" });
    expect(await screen.findByText("Beta Market · receipt 2")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "k" });
    expect(await screen.findByText("Alpha Market · receipt 1")).toBeInTheDocument();
    // The verdict keys only fire once the receipt behind the heading loaded.
    await waitFor(() =>
      expect(screen.getByTestId("truth-panel")).toHaveTextContent("Alpha Market"),
    );

    fireEvent.keyDown(window, { key: "f" });
    expect(
      screen.getByRole("dialog", { name: "Describe the failure mode" }),
    ).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: "c" });
    await waitFor(() =>
      expect(mockedPostReview).toHaveBeenCalledWith(
        expect.objectContaining({ verdict: "confirm", note: "" }),
      ),
    );
  });

  it("reports queued ids the index does not know about", async () => {
    mockedFetchWorklist.mockResolvedValueOnce({
      matching: 0,
      built_at: "now",
      queue: "session-1",
      receipts: [],
      missing: [{ image_id: "image-9", receipt_id: 9 }],
    });
    render(<ValidationWorkstation />);
    fireEvent.click(screen.getByRole("tab", { name: /Escalation/ }));
    expect(
      await screen.findByText(/1 queued receipt\(s\) are not in the index/),
    ).toBeInTheDocument();
  });

  it("approves the dossier's proposal with its mode and rows", async () => {
    mockedFetchReceipt.mockImplementation(async (_imageId, receiptId) => ({
      ...detail(receiptId),
      dossier: {
        failure_mode: "H-zone-gap-missing-items",
        diagnosis: "ITEMS stops short of the subtotal.",
        evidence: [],
        proposal: {
          tool: "extend_items_section",
          args: { line_ids: [18, 19] },
          dry_run: {
            before_delta: -5,
            after_delta: 0,
            before_status: "mismatch",
            after_status: "match",
          },
        },
        abstain_reason: null,
        verdict_recommendation: "approve-fix",
        confidence: "high",
        signals_concurring: ["arithmetic"],
        generated_at: null,
        author: "scout",
        source: "image-1-1.json",
      },
    }));
    render(<ValidationWorkstation />);
    fireEvent.click(screen.getByRole("tab", { name: /Escalation/ }));
    await screen.findByTestId("dossier");

    fireEvent.keyDown(window, { key: "a" });
    await waitFor(() =>
      expect(mockedPostReview).toHaveBeenCalledWith(
        expect.objectContaining({
          verdict: "approve-fix",
          reason: "H-zone-gap-missing-items",
          line_ids: [18, 19],
        }),
      ),
    );
  });
});

describe("harness shrink", () => {
  it("no longer ships the merchant browse panel", () => {
    expect(() =>
      require.resolve("../../components/dev/validation/MerchantList"),
    ).toThrow();
    // The browse index it was the only consumer of is gone from the client
    // too, so nothing can quietly bring worst-first ordering back.
    expect(client).not.toHaveProperty("fetchMerchants");
  });
});
