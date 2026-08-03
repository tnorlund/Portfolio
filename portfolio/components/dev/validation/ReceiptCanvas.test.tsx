import { fireEvent, render, screen } from "@testing-library/react";
import ReceiptCanvas from "./ReceiptCanvas";
import { ValidationReceipt } from "./types";

const receipt = (overrides: Partial<ValidationReceipt> = {}): ValidationReceipt => ({
  image_id: "image-1",
  receipt_id: 1,
  merchant_name: "Market",
  item_count: 1,
  items: [
    {
      item_index: 0,
      name: "Milk",
      price: 4.25,
      quantity: null,
      unit_price: null,
      is_discount: false,
      line_ids: [1],
      name_quality: "ok",
      reconciliation_status: "match",
      extractor_version: "test",
    },
  ],
  items_sum: 4.25,
  delta: 0,
  reconciliation_status: "match",
  items_section_line_ids: [1],
  items_section_status: "VALID",
  image: {
    image_id: "image-1",
    receipt_id: 1,
    width: 600,
    height: 1000,
    cdn_s3_key: "receipt.jpg",
  },
  lines: [
    {
      line_id: 1,
      text: "Milk 4.25",
      bounding_box: { x: 0.1, y: 0.7, width: 0.8, height: 0.04 },
    },
  ],
  sections: [
    { section_type: "ITEMS", line_ids: [1], validation_status: "VALID" },
  ],
  summary: null,
  dossier: null,
  dossier_error: null,
  reviews: [],
  ...overrides,
});

const renderCanvas = (value = receipt(), overlayMode = "both" as const) =>
  render(
    <ReceiptCanvas
      receipt={value}
      formatSupport={{ supportsAVIF: false, supportsWebP: false }}
      highlightLineIds={null}
      overlayMode={overlayMode}
    />,
  );

describe("ReceiptCanvas", () => {
  it("zooms and resets the transformed canvas", () => {
    renderCanvas();
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(screen.getByTestId("canvas-stage")).toHaveStyle(
      "transform: translate(0px, 0px) scale(1.25)",
    );
    fireEvent.click(screen.getByRole("button", { name: "125%" }));
    expect(screen.getByTestId("canvas-stage")).toHaveStyle(
      "transform: translate(0px, 0px) scale(1)",
    );
  });

  it("pans the canvas with pointer dragging", () => {
    renderCanvas();
    const viewport = screen.getByTestId("canvas-viewport");
    fireEvent(
      viewport,
      new MouseEvent("pointerdown", {
        bubbles: true,
        button: 0,
        clientX: 10,
        clientY: 20,
      }),
    );
    fireEvent(
      viewport,
      new MouseEvent("pointermove", {
        bubbles: true,
        clientX: 35,
        clientY: 55,
      }),
    );
    fireEvent(viewport, new MouseEvent("pointerup", { bubbles: true }));
    expect(screen.getByTestId("canvas-stage")).toHaveStyle(
      "transform: translate(25px, 35px) scale(1)",
    );
  });

  it("switches between section, item, and combined overlays", () => {
    const { rerender } = render(
      <ReceiptCanvas
        receipt={receipt()}
        formatSupport={{ supportsAVIF: false, supportsWebP: false }}
        highlightLineIds={null}
        overlayMode="sections"
      />,
    );
    expect(screen.getByTestId("section-overlay")).toBeInTheDocument();
    expect(screen.queryByTestId("item-overlay")).not.toBeInTheDocument();

    rerender(
      <ReceiptCanvas
        receipt={receipt()}
        formatSupport={{ supportsAVIF: false, supportsWebP: false }}
        highlightLineIds={null}
        overlayMode="items"
      />,
    );
    expect(screen.queryByTestId("section-overlay")).not.toBeInTheDocument();
    expect(screen.getByTestId("item-overlay")).toBeInTheDocument();

    rerender(
      <ReceiptCanvas
        receipt={receipt()}
        formatSupport={{ supportsAVIF: false, supportsWebP: false }}
        highlightLineIds={null}
        overlayMode="both"
      />,
    );
    expect(screen.getByTestId("section-overlay")).toBeInTheDocument();
    expect(screen.getByTestId("item-overlay")).toBeInTheDocument();
  });

  it("renders missing image, sections, and items as review evidence", () => {
    renderCanvas(receipt({ image: null, sections: [], items: [], item_count: 0 }));
    expect(screen.getByTestId("image-placeholder")).toHaveTextContent(
      "Image record missing",
    );
    expect(screen.getByText("No sections")).toBeInTheDocument();
    expect(screen.getByText("No items")).toBeInTheDocument();
  });
});
