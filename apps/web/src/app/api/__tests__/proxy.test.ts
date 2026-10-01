import { once } from "node:events";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import { GET, POST } from "../[...path]/route";

type Handler = (req: IncomingMessage, res: ServerResponse) => void;

let server: Server;
let baseUrl: string;
let handler: Handler = (_req, res) => {
  res.writeHead(404).end();
};

beforeAll(async () => {
  server = createServer((req, res) => handler(req, res));
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  baseUrl = `http://127.0.0.1:${port}`;
});

afterAll(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

beforeEach(() => {
  process.env.API_BASE_URL = baseUrl;
});

afterEach(() => {
  delete process.env.API_BASE_URL;
  handler = (_req, res) => {
    res.writeHead(404).end();
  };
});

function context(path: string[]) {
  return { params: Promise.resolve({ path }) };
}

describe("/api/[...path] proxy", () => {
  it("forces Content-Encoding: identity on the proxied response", async () => {
    handler = (_req, res) => {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ status: "ok" }));
    };

    const request = new NextRequest("http://localhost:3000/api/health");
    const response = await GET(request, context(["health"]));

    expect(response.headers.get("content-encoding")).toBe("identity");
    expect(await response.json()).toEqual({ status: "ok" });
  });

  it("streams SSE chunks byte-for-byte with no buffering or re-encoding", async () => {
    const chunk1 = 'event: message_start\ndata: {"type":"message_start"}\n\n';
    const chunk2 = 'event: text_delta\ndata: {"type":"text_delta","delta":"Hi"}\n\n';
    const chunk3 = "event: message_end\ndata: {}\n\n";

    let releaseChunk2: () => void;
    const chunk2Gate = new Promise<void>((resolve) => {
      releaseChunk2 = resolve;
    });

    handler = (_req, res) => {
      res.writeHead(200, {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
      });
      res.write(chunk1);
      // Only writes the rest once the test has already consumed the first
      // chunk, proving the proxy forwards each chunk as it arrives instead
      // of buffering the whole body before responding.
      void chunk2Gate.then(() => {
        res.write(chunk2);
        res.end(chunk3);
      });
    };

    const request = new NextRequest("http://localhost:3000/api/conversations/abc/messages", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content: "hello" }),
    });
    const response = await POST(request, context(["conversations", "abc", "messages"]));

    expect(response.headers.get("content-encoding")).toBe("identity");
    expect(response.headers.get("content-type")).toBe("text/event-stream; charset=utf-8");

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();

    const first = await reader.read();
    expect(first.done).toBe(false);
    expect(decoder.decode(first.value!, { stream: true })).toBe(chunk1);

    releaseChunk2!();

    let rest = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      rest += decoder.decode(value, { stream: true });
    }
    expect(rest).toBe(chunk2 + chunk3);
  });

  it("propagates a client abort to the upstream connection via request.signal", async () => {
    let upstreamResponse: ServerResponse | undefined;

    handler = (_req, res) => {
      upstreamResponse = res;
      res.writeHead(200, { "content-type": "text/event-stream; charset=utf-8" });
      res.write("event: message_start\ndata: {}\n\n");
      // Deliberately never calls res.end(): the only way this connection
      // closes is the client aborting it (AC-10's "client disconnect").
    };

    const controller = new AbortController();
    const request = new NextRequest("http://localhost:3000/api/conversations/abc/messages", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content: "hello" }),
      signal: controller.signal,
    });

    const response = await POST(request, context(["conversations", "abc", "messages"]));
    expect(response.status).toBe(200);
    expect(upstreamResponse).toBeDefined();

    controller.abort();

    // Node's http.ServerResponse emits "close" when the underlying
    // connection terminates. Awaiting the real event (not a guessed delay)
    // proves request.signal actually reached the upstream socket.
    await once(upstreamResponse!, "close");
  });
});
