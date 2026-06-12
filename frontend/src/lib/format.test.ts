import { expect, test } from "vitest";
import { formatPct } from "./format";

test("formatPct renders a fraction as a percentage", () => {
  expect(formatPct(0.0312)).toBe("3.12%");
  expect(formatPct(-0.15, 0)).toBe("-15%");
});
