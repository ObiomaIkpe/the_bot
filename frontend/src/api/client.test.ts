import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  apiClient,
  ApiError,
  clearToken,
  getToken,
  login,
  setToken,
  setUnauthorizedHandler,
} from "./client";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("token storage", () => {
  afterEach(() => {
    clearToken();
  });

  it("round-trips a token through localStorage", () => {
    expect(getToken()).toBeNull();
    setToken("abc123");
    expect(getToken()).toBe("abc123");
    clearToken();
    expect(getToken()).toBeNull();
  });
});

describe("apiClient", () => {
  beforeEach(() => {
    clearToken();
    setUnauthorizedHandler(() => {});
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not attach an Authorization header when no token is set", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ ok: true }));
    await apiClient.get("/events");
    const [, options] = vi.mocked(fetch).mock.calls[0];
    const headers = new Headers(options?.headers);
    expect(headers.has("Authorization")).toBe(false);
  });

  it("attaches the stored token as a Bearer header", async () => {
    setToken("my-token");
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ ok: true }));
    await apiClient.get("/events");
    const [, options] = vi.mocked(fetch).mock.calls[0];
    const headers = new Headers(options?.headers);
    expect(headers.get("Authorization")).toBe("Bearer my-token");
  });

  it("clears the token and calls the unauthorized handler on a 401", async () => {
    setToken("stale-token");
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "Not authenticated" }, 401));

    await expect(apiClient.get("/events")).rejects.toThrow(ApiError);
    expect(getToken()).toBeNull();
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("throws an ApiError carrying the backend's detail message on a non-2xx response", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "Broker credential not found" }, 404));

    const error = await apiClient.get("/broker-credentials/x").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
    expect((error as ApiError).detail).toBe("Broker credential not found");
  });

  it("sends PATCH bodies as JSON with a Content-Type header", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ status: "active" }));
    await apiClient.patch("/model-configs/1", { status: "active" });
    const [, options] = vi.mocked(fetch).mock.calls[0];
    const headers = new Headers(options?.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(options?.body).toBe(JSON.stringify({ status: "active" }));
  });
});

describe("login", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("posts form-encoded credentials (not JSON) and returns the access token", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ access_token: "the-token", token_type: "bearer" }));

    const token = await login("a@example.com", "hunter2");

    expect(token).toBe("the-token");
    const [, options] = vi.mocked(fetch).mock.calls[0];
    const headers = new Headers(options?.headers);
    expect(headers.get("Content-Type")).toBe("application/x-www-form-urlencoded");
    expect(options?.body?.toString()).toBe("username=a%40example.com&password=hunter2");
  });

  it("throws with the backend's detail on a failed login", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "Incorrect email or password" }, 401));

    await expect(login("a@example.com", "wrong")).rejects.toThrow("Incorrect email or password");
  });
});
