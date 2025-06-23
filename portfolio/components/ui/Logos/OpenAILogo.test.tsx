import { render } from "@testing-library/react";
import React from "react";
import OpenAILogo from "./OpenAILogo";

test("renders OpenAI logo SVG", () => {
  const { container } = render(<OpenAILogo />);
  expect(container.querySelector("svg")).toBeInTheDocument();
});
