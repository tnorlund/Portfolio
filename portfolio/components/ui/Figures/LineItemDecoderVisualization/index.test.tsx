import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import LineItemDecoderVisualization from "./index";
import examples from "../../../../public/line-item-demo/receipts.json";

jest.mock("react-intersection-observer", () => {
  const ref = () => {};
  return { useInView: () => ({ ref, inView: true }) };
});
jest.mock("../../../../utils/imageFormat", () => ({
  getBestImageUrl: () => "/offline-receipt.png",
  getJpegFallbackUrl: () => "/offline-receipt.png",
  usePreloadReceiptImages: () => {},
}));
jest.mock("../ReceiptFlow/useImageFormatSupport", () => {
  const support = { avif: false, webp: true };
  return { useImageFormatSupport: () => support };
});
jest.mock("../ReceiptFlow/useFlyingReceipt", () => ({
  useFlyingReceipt: () => ({ flyingItem: null, showFlying: false }),
}));

const originalFetch = global.fetch;

beforeEach(() => {
  global.fetch = jest.fn().mockResolvedValue({
    ok: true,
    json: async () => examples,
  });
});
afterEach(() => {
  global.fetch = originalFetch;
});

test("pause and manual navigation keep the selected receipt visible", async () => {
  render(<LineItemDecoderVisualization />);
  fireEvent.click(await screen.findByRole("button", { name: "Pause walkthrough" }));
  expect(screen.getByRole("button", { name: "Play walkthrough" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Next receipt" }));
  expect(screen.getByText("2 / 8")).toBeVisible();
  expect(screen.getByRole("button", { name: "Play walkthrough" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Previous receipt" }));
  expect(screen.getByText("1 / 8")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Previous receipt" }));
  expect(screen.getByText("8 / 8")).toBeVisible();
});

test("a missing export renders an error instead of a blank figure", async () => {
  global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 404 });
  render(<LineItemDecoderVisualization />);
  await waitFor(() => expect(screen.getByText(/Error:/)).toBeVisible());
  expect(screen.queryByRole("button", { name: "Next receipt" })).not.toBeInTheDocument();
});
