import { describe, it, expect } from "vitest";
import {
  AuthError,
  NotFoundError,
  OpenGraphClient,
  RateLimitError,
} from "../src";

function mockFetchOnce(response: {
  status: number;
  body?: unknown;
  headers?: Record<string, string>;
}): typeof fetch {
  return async () =>
    new Response(response.body === undefined ? null : JSON.stringify(response.body), {
      status: response.status,
      headers: {
        "Content-Type": "application/json",
        ...(response.headers ?? {}),
      },
    });
}

describe("OpenGraphClient", () => {
  it("parses a happy-path query response", async () => {
    const fetchStub = mockFetchOnce({
      status: 200,
      body: {
        session_id: "sess-1",
        response: "hello",
        intent: "greet",
        kb_focus: "both",
        extracted_topics: [],
        follow_up_suggestions: ["what next?"],
        duration_ms: 42,
        history_persisted: true,
      },
    });
    const client = new OpenGraphClient({
      apiKey: "og_live_test",
      baseUrl: "http://test.example",
      maxRetries: 0,
      fetch: fetchStub,
    });
    const resp = await client.query({ workspaceId: "ws-1", query: "hi" });
    expect(resp.session_id).toBe("sess-1");
    expect(resp.response).toBe("hello");
    expect(resp.follow_up_suggestions).toEqual(["what next?"]);
  });

  it("maps 401 to AuthError", async () => {
    const fetchStub = mockFetchOnce({
      status: 401,
      body: { detail: "Invalid or missing API key." },
    });
    const client = new OpenGraphClient({
      apiKey: "og_live_test",
      baseUrl: "http://test.example",
      maxRetries: 0,
      fetch: fetchStub,
    });
    await expect(client.graph.stats({ workspaceId: "ws-1" })).rejects.toBeInstanceOf(AuthError);
  });

  it("maps 404 to NotFoundError", async () => {
    const fetchStub = mockFetchOnce({
      status: 404,
      body: { detail: "Node 'missing' not found." },
    });
    const client = new OpenGraphClient({
      apiKey: "og_live_test",
      baseUrl: "http://test.example",
      maxRetries: 0,
      fetch: fetchStub,
    });
    await expect(
      client.graph.node("missing", { workspaceId: "ws-1" }),
    ).rejects.toBeInstanceOf(NotFoundError);
  });

  it("propagates Retry-After on 429", async () => {
    const fetchStub = mockFetchOnce({
      status: 429,
      body: { detail: "Rate limit exceeded — 60 per minute." },
      headers: { "Retry-After": "42" },
    });
    const client = new OpenGraphClient({
      apiKey: "og_live_test",
      baseUrl: "http://test.example",
      maxRetries: 0,
      fetch: fetchStub,
    });
    try {
      await client.graph.stats({ workspaceId: "ws-1" });
      throw new Error("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(RateLimitError);
      expect((err as RateLimitError).retryAfterSeconds).toBe(42);
    }
  });

  it("falls back to OPENGRAPH_WORKSPACE_ID env", async () => {
    let capturedBody = "";
    const fetchStub: typeof fetch = async (_url, init) => {
      capturedBody = init?.body ? String(init.body) : "";
      return new Response(
        JSON.stringify({
          session_id: "s",
          response: "ok",
          intent: "",
          kb_focus: "both",
          extracted_topics: [],
          follow_up_suggestions: [],
          duration_ms: 0,
          history_persisted: true,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };
    process.env.OPENGRAPH_WORKSPACE_ID = "ws-from-env";
    try {
      const client = new OpenGraphClient({
        apiKey: "og_live_test",
        baseUrl: "http://test.example",
        maxRetries: 0,
        fetch: fetchStub,
      });
      await client.query({ query: "hello" });
    } finally {
      delete process.env.OPENGRAPH_WORKSPACE_ID;
    }
    const payload = JSON.parse(capturedBody);
    expect(payload.workspace_id).toBe("ws-from-env");
  });
});
