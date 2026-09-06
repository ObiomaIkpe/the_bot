import { describe, expect, it } from "vitest";
import { formatPrice } from "./format";

// Reported live: the trade-story detail page showed a Stop price as
// "1.1577600000000001" -- binary floating-point noise, not real data.
describe("formatPrice", () => {
  it("strips floating-point noise from a price with real 6-decimal precision", () => {
    expect(formatPrice(1.1577600000000001)).toBe("1.15776");
  });

  it("leaves an already-clean price unchanged", () => {
    expect(formatPrice(1.1646)).toBe("1.1646");
    expect(formatPrice(1.158595)).toBe("1.158595");
  });

  it("shows a dash for a missing price", () => {
    expect(formatPrice(null)).toBe("-");
    expect(formatPrice(undefined)).toBe("-");
  });
});
