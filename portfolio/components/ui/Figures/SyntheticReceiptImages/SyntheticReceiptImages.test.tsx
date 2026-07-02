import { render, screen } from "@testing-library/react";
import SyntheticReceiptImages from ".";

jest.mock("react-intersection-observer", () => ({
  useInView: () => ({
    ref: jest.fn(),
    inView: true,
  }),
}));

jest.mock("@react-spring/web", () => {
  const React = require("react");

  return {
    animated: {
      div: React.forwardRef((props: any, ref: any) => (
        <div ref={ref} {...props} />
      )),
    },
    to: (_values: unknown[], mapper: (...values: number[]) => string) =>
      mapper(0, 0, 1, 0),
    useSpring: () => [
      { x: 0, y: 0, scale: 1, rotate: 0, opacity: 1 },
      { set: jest.fn(), start: jest.fn() },
    ],
  };
});

describe("SyntheticReceiptImages", () => {
  test("renders the synthetic image cache through the receipt flow shell", () => {
    render(<SyntheticReceiptImages />);

    expect(
      screen.getByRole("img", { name: "Synthetic Add line item receipt" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Synthetic cache")).toBeInTheDocument();
    expect(screen.getByText("add_line_item")).toBeInTheDocument();
    expect(screen.getByText("0.948")).toBeInTheDocument();
    expect(screen.getByText("$29.08 -> $30.58")).toBeInTheDocument();
    expect(screen.getByText("Train-only guard")).toBeInTheDocument();
    expect(document.querySelector("[data-rf-queue]")).toBeInTheDocument();
    expect(document.querySelector("[data-rf-card-id='sprouts-remove-line-item']")).toBeInTheDocument();
  });
});
