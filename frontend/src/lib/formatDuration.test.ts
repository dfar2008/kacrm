import { formatDuration } from "./formatDuration";
test("sub-minute → X.Xs", () => { expect(formatDuration(5700)).toBe("5.7s"); });
test("over-minute → M分SS秒", () => { expect(formatDuration(282600)).toBe("4分43秒"); });
test("null → empty", () => { expect(formatDuration(null)).toBe(""); });
