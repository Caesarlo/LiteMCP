import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { Navbar } from "@/components/navbar";

// HeroUI's useTheme (used by the ThemeSwitch rendered inside the Navbar) reads
// the OS color-scheme through window.matchMedia, which jsdom does not implement.
// A minimal MediaQueryList stand-in keeps the real component renderable under
// jsdom; this is test-only scaffolding and touches no production code.
beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
});

afterEach(() => {
  cleanup();
});

const topLevelNavLabels = ["Home", "Docs", "Pricing", "Blog", "About"];

describe("Navbar", () => {
  it("renders the brand and every top-level navigation link", () => {
    render(<Navbar />);

    expect(screen.getByText("ACME")).toBeInTheDocument();

    for (const label of topLevelNavLabels) {
      expect(screen.getByRole("link", { name: label })).toHaveAttribute("href");
    }
  });

  it("renders the Sponsor action button", () => {
    render(<Navbar />);

    expect(
      screen.getByRole("button", { name: /sponsor/i }),
    ).toBeInTheDocument();
  });

  it("renders the search input placeholder", () => {
    render(<Navbar />);

    expect(screen.getByPlaceholderText("Search...")).toBeInTheDocument();
  });

  it("opens the mobile navigation menu when the toggle is pressed", () => {
    render(<Navbar />);

    const toggle = screen.getByRole("button", { name: "Toggle menu" });

    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Logout")).not.toBeInTheDocument();

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Logout")).toBeInTheDocument();
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
  });
});
