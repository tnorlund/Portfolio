import { render, screen } from "@testing-library/react";
import AnimatedInView from "./AnimatedInView";
import React from "react";

jest.mock("../../hooks/useOptimizedInView", () => () => [
  React.createRef(),
  true,
]);

// Mock the spring animation to avoid timing issues
jest.mock("@react-spring/web", () => ({
  useSpring: () => [
    { opacity: 1 },
    {
      start: jest.fn(),
      set: jest.fn(),
    },
  ],
  animated: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  },
}));

describe("AnimatedInView", () => {
  test("renders initial content", () => {
    render(
      <AnimatedInView>
        <span>test content</span>
      </AnimatedInView>
    );
    expect(screen.getByText("test content")).toBeInTheDocument();
  });

  test("renders with replacement prop", () => {
    render(
      <AnimatedInView replacement={<span>replacement</span>}>
        initial content
      </AnimatedInView>
    );
    // Since we're mocking the animation and inView is true,
    // both initial content and the component structure should be present
    expect(screen.getByText("initial content")).toBeInTheDocument();
  });

  test("applies custom className", () => {
    const { container } = render(
      <AnimatedInView className="custom-class">test content</AnimatedInView>
    );
    expect(container.firstChild).toHaveClass("custom-class");
  });
});
