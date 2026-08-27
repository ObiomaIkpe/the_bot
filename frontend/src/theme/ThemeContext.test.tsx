import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider, useTheme } from "./ThemeContext";

const STORAGE_KEY = "theme_preference";

function TestConsumer() {
  const { theme, toggleTheme } = useTheme();
  return (
    <div>
      <p>theme: {theme}</p>
      <button onClick={toggleTheme}>Toggle</button>
    </div>
  );
}

describe("ThemeProvider", () => {
  beforeEach(() => {
    localStorage.removeItem(STORAGE_KEY);
    delete document.documentElement.dataset.theme;
  });
  afterEach(() => {
    localStorage.removeItem(STORAGE_KEY);
    delete document.documentElement.dataset.theme;
  });

  it("defaults to dark when nothing is stored", () => {
    render(
      <ThemeProvider>
        <TestConsumer />
      </ThemeProvider>,
    );
    expect(screen.getByText("theme: dark")).toBeInTheDocument();
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("reads a previously stored light preference", () => {
    localStorage.setItem(STORAGE_KEY, "light");
    render(
      <ThemeProvider>
        <TestConsumer />
      </ThemeProvider>,
    );
    expect(screen.getByText("theme: light")).toBeInTheDocument();
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("toggleTheme flips the theme, the DOM attribute, and persists to localStorage", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <TestConsumer />
      </ThemeProvider>,
    );

    await user.click(screen.getByText("Toggle"));

    expect(screen.getByText("theme: light")).toBeInTheDocument();
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("light");

    await user.click(screen.getByText("Toggle"));

    expect(screen.getByText("theme: dark")).toBeInTheDocument();
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("dark");
  });
});
