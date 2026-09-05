import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ItemEditor, FocusEditor } from "./Dialogs";
import type { Snapshot } from "../../services/planner/types";
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
});
test("saves the visible date after a native input event and keeps the due date separate", async () => {
  const execute = jest.fn().mockResolvedValue({});
  const close = jest.fn();
  render(
    <ItemEditor
      initial={{ week: "2026-09-07" }}
      areas={[]}
      execute={execute}
      close={close}
    />,
  );
  fireEvent.change(screen.getByLabelText("What would you like to do?"), {
    target: { value: "Visit the workshop" },
  });
  fireEvent.input(screen.getByLabelText("Plan for"), {
    target: { value: "2026-09-09" },
  });
  fireEvent.input(screen.getByLabelText("Due by"), {
    target: { value: "2026-09-11" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save item" }));
  await waitFor(() =>
    expect(execute).toHaveBeenCalledWith(
      expect.objectContaining({
        text: "Visit the workshop",
        date: "2026-09-09",
        due_date: "2026-09-11",
      }),
    ),
  );
  expect(close).toHaveBeenCalled();
});
test("a failed edit remains open and keeps the draft", async () => {
  const execute = jest
    .fn()
    .mockRejectedValue(
      new Error("This item changed. Refresh it before saving."),
    );
  const close = jest.fn();
  render(
    <ItemEditor initial={{}} areas={[]} execute={execute} close={close} />,
  );
  fireEvent.change(screen.getByLabelText("What would you like to do?"), {
    target: { value: "Keep my draft" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save item" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "This item changed",
  );
  expect(screen.getByLabelText("What would you like to do?")).toHaveValue(
    "Keep my draft",
  );
  expect(close).not.toHaveBeenCalled();
});

test("a focus draft keeps its original revision when a background refresh arrives", async () => {
  const execute = jest.fn().mockResolvedValue({});
  const close = jest.fn();
  const initial = {
    weeks: {
      "2026-09-07": {
        week: "2026-09-07",
        focus: "Original focus",
        revision: 1,
        status: "active",
        review: null,
      },
    },
  } as unknown as Snapshot;
  const { rerender } = render(
    <FocusEditor
      snapshot={initial}
      week="2026-09-07"
      execute={execute}
      close={close}
    />,
  );
  fireEvent.change(screen.getByLabelText("What matters most this week?"), {
    target: { value: "My local draft" },
  });
  const refreshed = {
    ...initial,
    weeks: {
      "2026-09-07": {
        ...initial.weeks["2026-09-07"],
        revision: 2,
        focus: "Changed by agent",
      },
    },
  };
  rerender(
    <FocusEditor
      snapshot={refreshed}
      week="2026-09-07"
      execute={execute}
      close={close}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Save focus" }));
  await waitFor(() =>
    expect(execute).toHaveBeenCalledWith({
      action: "save_week",
      week: "2026-09-07",
      focus: "My local draft",
      revision: 1,
    }),
  );
});
