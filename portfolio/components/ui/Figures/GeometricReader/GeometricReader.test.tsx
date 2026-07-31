import { render, screen, waitFor } from "@testing-library/react";
import { api } from "../../../../services/api";
import { LineItemDecodeResponse } from "../../../../types/api";
import GeometricReader from ".";
import { boundsForLineIds, getReceiptProof } from "./geometry";

jest.mock("react-intersection-observer", () => ({
  useInView: () => ({ ref: jest.fn(), inView: true }),
}));

jest.mock("../ReceiptFlow/useImageFormatSupport", () => ({
  useImageFormatSupport: () => ({ supportsWebP: true, supportsAVIF: false }),
}));

jest.mock("../../../../utils/imageFormat", () => ({
  getBestImageUrl: () => "/receipt.webp",
  getJpegFallbackUrl: () => "/receipt.jpg",
  usePreloadReceiptImages: jest.fn(),
}));

jest.mock("../../../../services/api", () => ({
  api: { fetchLineItemDecode: jest.fn() },
}));

const response: LineItemDecodeResponse = {
  receipts: [
    {
      image_id: "image-1",
      receipt_id: 1,
      merchant_name: "Test Market",
      image: {
        image_id: "image-1",
        receipt_id: 1,
        width: 600,
        height: 1200,
        cdn_s3_key: "receipt.jpg",
      },
      lines: [
        {
          line_id: 0,
          text: "TEST MARKET",
          bounding_box: { x: 0.2, y: 0.86, width: 0.5, height: 0.03 },
        },
        {
          line_id: 1,
          text: "BANANAS",
          bounding_box: { x: 0.15, y: 0.55, width: 0.4, height: 0.03 },
        },
        {
          line_id: 2,
          text: "$1.69",
          bounding_box: { x: 0.72, y: 0.55, width: 0.13, height: 0.03 },
        },
        {
          line_id: 3,
          text: "MILK $4.99",
          bounding_box: { x: 0.15, y: 0.47, width: 0.7, height: 0.03 },
        },
        {
          line_id: 4,
          text: "SUBTOTAL $6.68",
          bounding_box: { x: 0.45, y: 0.15, width: 0.4, height: 0.03 },
        },
      ],
      sections: [
        { section_type: "HEADER", line_ids: [0] },
        { section_type: "ITEMS", line_ids: [1, 2, 3] },
        { section_type: "SUMMARY", line_ids: [4] },
      ],
      line_items: [
        {
          name: "Bananas",
          price: "1.69",
          quantity: 1.31,
          unit_price: 1.29,
          is_discount: false,
          line_ids: [1, 2],
          reconciliation_status: "match",
        },
        {
          name: "Milk",
          price: "4.99",
          quantity: null,
          unit_price: null,
          is_discount: false,
          line_ids: [3],
          reconciliation_status: "match",
        },
      ],
      printed_subtotal: 6.68,
    },
  ],
  batch_size: 1,
  candidate_count: 1,
  fetched_at: "2026-07-31T00:00:00Z",
};

beforeEach(() => {
  jest.mocked(api.fetchLineItemDecode).mockResolvedValue(response);
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: query.includes("prefers-reduced-motion"),
      media: query,
      onchange: null,
      addListener: jest.fn(),
      removeListener: jest.fn(),
      addEventListener: jest.fn(),
      removeEventListener: jest.fn(),
      dispatchEvent: jest.fn(),
    }),
  });
});

test("reduced motion renders the resolved sections, ledger, and exact proof", async () => {
  render(<GeometricReader />);

  await waitFor(() =>
    expect(screen.getByText("Test Market")).toBeInTheDocument(),
  );
  await waitFor(() =>
    expect(screen.getByTestId("section-zone-HEADER")).toBeInTheDocument(),
  );
  expect(screen.getByTestId("section-zone-ITEMS")).toBeInTheDocument();
  expect(screen.getByText("Bananas")).toBeInTheDocument();
  expect(screen.getByText("Milk")).toBeInTheDocument();
  expect(screen.getByText("reconciled · exact")).toBeInTheDocument();
  expect(screen.queryByText("re-OCR queued")).not.toBeInTheDocument();
});

test("proof keeps persisted mismatch status and reports its delta", () => {
  const proof = getReceiptProof({
    line_items: response.receipts[0].line_items.map((item) => ({
      ...item,
      reconciliation_status: "mismatch",
    })),
    printed_subtotal: 7,
  });

  expect(proof).toEqual({
    status: "mismatch",
    decodedTotal: 6.68,
    printedSubtotal: 7,
    delta: -0.32,
  });
});

test("geometry unions bottom-origin line boxes into a top-origin zone", () => {
  const bounds = boundsForLineIds(response.receipts[0].lines, [1, 2]);
  expect(bounds).not.toBeNull();
  expect(bounds?.x).toBeCloseTo(0.15);
  expect(bounds?.y).toBeCloseTo(0.42);
  expect(bounds?.width).toBeCloseTo(0.7);
  expect(bounds?.height).toBeCloseTo(0.03);
});
