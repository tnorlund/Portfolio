import { renderToStaticMarkup } from "react-dom/server";
import type { DocumentProps } from "next/document";
jest.mock("next/document", () => ({
  Head: "head",
  Html: "html",
  Main: () => null,
  NextScript: () => null,
}));
afterEach(() => {
  delete process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID;
  delete process.env.NEXT_PUBLIC_GTM_ID;
  jest.resetModules();
});
test("private planner HTML excludes analytics even when site analytics are configured", () => {
  process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID = "G-EXAMPLE";
  process.env.NEXT_PUBLIC_GTM_ID = "GTM-EXAMPLE";
  const Document = require("../../pages/_document").default;
  const planner = renderToStaticMarkup(
    <Document
      {...({ __NEXT_DATA__: { page: "/planner" } } as DocumentProps)}
    />,
  );
  expect(planner).not.toContain("googletagmanager");
  expect(planner).not.toContain("G-EXAMPLE");
  const home = renderToStaticMarkup(
    <Document {...({ __NEXT_DATA__: { page: "/" } } as DocumentProps)} />,
  );
  expect(home).toContain("G-EXAMPLE");
  expect(home).toContain("GTM-EXAMPLE");
});
