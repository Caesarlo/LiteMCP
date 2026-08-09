import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { useEffect, useState } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

const MOCKED_URL = "http://localhost/api/v1/mock/hello";
const UNHANDLED_URL = "http://localhost/api/v1/mock/unhandled";

// Test-only component: performs a real fetch() on mount and renders the
// response body. It exists solely inside this test file so the MSW
// interception is exercised against an actual network request, never a stub.
function FetchingComponent({ url = MOCKED_URL }: { url?: string }) {
  const [message, setMessage] = useState("loading");

  useEffect(() => {
    let cancelled = false;

    fetch(url)
      .then((response) => response.json())
      .then((data: { message: string }) => {
        if (!cancelled) setMessage(data.message);
      })
      .catch(() => {
        if (!cancelled) setMessage("request-failed");
      });

    return () => {
      cancelled = true;
    };
  }, [url]);

  return <div>{message}</div>;
}

const server = setupServer(
  http.get(MOCKED_URL, () => HttpResponse.json({ message: "mocked-from-msw" })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));

afterEach(() => {
  cleanup();
  server.resetHandlers();
});

afterAll(() => server.close());

describe("MSW request interception", () => {
  it("serves mocked data to a real fetch() performed by a mounted component", async () => {
    render(<FetchingComponent />);

    expect(screen.getByText("loading")).toBeInTheDocument();

    const delivered = await screen.findByText("mocked-from-msw");

    expect(delivered).toBeInTheDocument();
  });

  it("allows per-test handler overrides through server.use()", async () => {
    server.use(
      http.get(MOCKED_URL, () =>
        HttpResponse.json({ message: "overridden-by-server-use" }),
      ),
    );

    render(<FetchingComponent />);

    expect(
      await screen.findByText("overridden-by-server-use"),
    ).toBeInTheDocument();
  });

  it("blocks requests that match no handler when onUnhandledRequest is 'error'", async () => {
    render(<FetchingComponent url={UNHANDLED_URL} />);

    expect(await screen.findByText("request-failed")).toBeInTheDocument();
  });
});
