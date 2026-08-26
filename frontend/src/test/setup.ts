import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

// Testing Library doesn't auto-register cleanup for Vitest the way it
// does for Jest -- without this, each test's render() output accumulates
// in the same jsdom document, so a later test's queries can match
// elements left over from an earlier one.
afterEach(() => {
  cleanup();
});
