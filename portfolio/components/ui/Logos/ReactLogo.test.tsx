import { render } from "@testing-library/react";
import React from "react";
import ReactLogo from "./ReactLogo";

test("renders React logo SVG", () => {
  const { container } = render(<ReactLogo />);
  expect(container.querySelector("svg")).toBeInTheDocument();
});
