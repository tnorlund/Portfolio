import { render } from "@testing-library/react";
import React from "react";
import PineconeLogo from "./PineconeLogo";

test("renders Pinecone logo SVG", () => {
  const { container } = render(<PineconeLogo />);
  expect(container.querySelector("svg")).toBeInTheDocument();
});
