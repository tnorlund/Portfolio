import { render } from "@testing-library/react";
import React from "react";
import NextJSLogo from "./NextJSLogo";

test("renders Next.js logo SVG", () => {
  const { container } = render(<NextJSLogo />);
  expect(container.querySelector("svg")).toBeInTheDocument();
});
