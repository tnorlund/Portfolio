import { render } from "@testing-library/react";
import React from "react";
import HuggingFaceLogo from "./HuggingFaceLogo";

test("renders Hugging Face logo SVG", () => {
  const { container } = render(<HuggingFaceLogo />);
  expect(container.querySelector("svg")).toBeInTheDocument();
});
