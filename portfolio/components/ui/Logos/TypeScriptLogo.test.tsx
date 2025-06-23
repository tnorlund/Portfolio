import { render } from "@testing-library/react";
import React from "react";
import TypeScriptLogo from "./TypeScriptLogo";

test("renders TypeScript logo SVG", () => {
  const { container } = render(<TypeScriptLogo />);
  expect(container.querySelector("svg")).toBeInTheDocument();
});
