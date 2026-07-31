import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  fetchMerchants,
  fetchReceipt,
  fetchReviews,
  fetchWorklist,
  postReview,
} from "../../components/dev/validation/client";
import { ValidationReceipt } from "../../components/dev/validation/types";
import ValidationWorkstation from "./validation";

jest.mock("../../components/dev/validation/client", () => ({
  fetchMerchants: jest.fn(),
  fetchReceipt: jest.fn(),
  fetchReviews: jest.fn(),
  fetchWorklist: jest.fn(),
  postReview: jest.fn(),
}));

jest.mock(
  "../../components/ui/Figures/ReceiptFlow/useImageFormatSupport",
  () => ({
    useImageFormatSupport: () => ({ supportsAVIF: false, supportsWebP: false }),
  }),
);

const mockedFetchMerchants = jest.mocked(fetchMerchants);
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
  reviews: [],
});

beforeEach(() => {
  mockedFetchMerchants.mockResolvedValue({
    merchants: [],
    totals: { mismatch: 2 },
    receipts: 2,
    built_at: "now",
    table: "dev",
  });
  mockedFetchReviews.mockResolvedValue({ entries: [], log: "/tmp/reviews" });
  mockedFetchWorklist.mockResolvedValue({
    merchant: "",
    status: "failures",
    matching: 2,
    built_at: "now",
    receipts: [
      {
        image_id: "image-1",
        receipt_id: 1,
        merchant: "Alpha Market",
        status: "mismatch",
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
      },
      {
        image_id: "image-2",
        receipt_id: 2,
        merchant: "Beta Market",
        status: "mismatch",
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
      },
    ],
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
    ts: "2026-07-31T20:00:00.000Z",
  });
});

describe("ValidationWorkstation keyboard flow", () => {
  it("navigates, focuses search, confirms, and manages the flag dialog", async () => {
    render(<ValidationWorkstation />);
    expect(await screen.findByText("Alpha Market · receipt 1")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "m" });
    expect(screen.getByRole("searchbox", { name: "Search merchants" })).toHaveFocus();
    screen.getByRole("searchbox").blur();

    fireEvent.keyDown(window, { key: "j" });
    expect(await screen.findByText("Beta Market · receipt 2")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "k" });
    expect(await screen.findByText("Alpha Market · receipt 1")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("truth-panel")).toHaveTextContent("Alpha Market"),
    );

    fireEvent.keyDown(window, { key: "f" });
    expect(screen.getByRole("dialog", { name: "Describe the failure mode" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: "c" });
    await waitFor(() =>
      expect(mockedPostReview).toHaveBeenCalledWith(
        expect.objectContaining({ verdict: "confirm", note: "" }),
      ),
    );
  });
});
