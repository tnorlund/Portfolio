import { render, screen } from "@testing-library/react";
import React from "react";
import ReceiptStack from "./ReceiptStack";

jest.mock("../../../hooks/useOptimizedInView", () => ({
  __esModule: true,
  default: () => [React.createRef(), true] as const,
}));

jest.mock("../../../services/api", () => ({
  api: {
    fetchReceipts: jest.fn(() => new Promise(() => {})),
  },
}));

jest.mock("../../../utils/imageFormat", () => ({
  detectImageFormatSupport: jest.fn(() =>
    Promise.resolve({ supportsAVIF: true, supportsWebP: true })
  ),
  getBestImageUrl: jest.fn(() => ""),
}));

test("displays loading state", async () => {
  render(<ReceiptStack />);
  expect(await screen.findByText(/loading receipts/i)).toBeInTheDocument();
});
