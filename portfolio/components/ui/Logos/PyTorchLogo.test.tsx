import { render } from "@testing-library/react";
import React from "react";
import PyTorchLogo from "./PyTorchLogo";

test("renders PyTorch logo SVG", () => {
  const { container } = render(<PyTorchLogo />);
  expect(container.querySelector("svg")).toBeInTheDocument();
});
