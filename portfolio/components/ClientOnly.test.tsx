import { render, screen } from "@testing-library/react";
import React from "react";
import ClientOnly from "./ClientOnly";
import { act } from "react";

describe("ClientOnly", () => {
  test("replaces fallback with children after mount", () => {
    jest.useFakeTimers();
    render(
      <ClientOnly fallback={<span data-testid="fallback">loading</span>}>
        <span>content</span>
      </ClientOnly>,
    );
    act(() => {
      jest.runAllTimers();
    });
    expect(screen.queryByTestId("fallback")).not.toBeInTheDocument();
    expect(screen.getByText("content")).toBeInTheDocument();
    jest.useRealTimers();
  });
});
