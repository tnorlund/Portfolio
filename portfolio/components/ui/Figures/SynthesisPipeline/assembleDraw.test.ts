import {
  caretVisibleAt,
  newlyRevealedWordIndices,
  rectToCrop,
  revealedCountsForProgress,
} from "./assembleDraw";

test("revealedCountsForProgress scales each group independently", () => {
  expect(revealedCountsForProgress([[0, 1, 2, 3], [10, 11]], 0)).toEqual([0, 0]);
  expect(revealedCountsForProgress([[0, 1, 2, 3], [10, 11]], 0.5)).toEqual([2, 1]);
  expect(revealedCountsForProgress([[0, 1, 2, 3], [10, 11]], 1)).toEqual([4, 2]);
});

test("newlyRevealedWordIndices returns only the delta", () => {
  const groups = [
    [0, 1, 2, 3],
    [10, 11, 12],
  ];
  expect(newlyRevealedWordIndices(groups, [0, 0], [2, 1])).toEqual([0, 1, 10]);
  expect(newlyRevealedWordIndices(groups, [2, 1], [2, 1])).toEqual([]);
  expect(newlyRevealedWordIndices(groups, [2, 1], [4, 3])).toEqual([2, 3, 11, 12]);
});

test("rectToCrop rejects empty rects", () => {
  expect(rectToCrop({ left: 10, top: 10, width: 0, height: 5 }, 100, 100)).toBeNull();
  expect(rectToCrop({ left: 10, top: 20, width: 25, height: 10 }, 200, 400)).toEqual({
    sx: 20,
    sy: 80,
    sw: 50,
    sh: 40,
  });
});

test("caretVisibleAt blinks while typing", () => {
  expect(caretVisibleAt(0)).toBe(false);
  expect(caretVisibleAt(1)).toBe(false);
  expect(caretVisibleAt(0.02)).toBe(true);
});
