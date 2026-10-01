import type { NextRequest } from "next/server";

// Next.js route-handler proxy to the FastAPI backend (docs/contracts.md →
// REST API → Topology; Streaming events → Next.js proxy passthrough). The
// browser never calls the API directly; this handler strips the `/api`
// prefix and forwards everything else — method, headers, body, status, and
// (for the SSE message endpoint) the response stream — byte-for-byte, with
// no buffering or re-encoding.

// Headers that must not be copied across a proxy hop: standard hop-by-hop
// headers (RFC 9110 §7.6.1) plus `host`, which must reflect the upstream
// FastAPI origin, not the browser-facing Next.js origin.
const HOP_BY_HOP_HEADERS: Record<string, true> = {
  connection: true,
  "keep-alive": true,
  "proxy-authenticate": true,
  "proxy-authorization": true,
  te: true,
  trailer: true,
  "transfer-encoding": true,
  upgrade: true,
  host: true,
};

function apiBaseUrl(): string {
  // contracts.md → Configuration: server-side only, defaults to
  // http://localhost:8000 on the host; Compose sets http://api:8000.
  return process.env.API_BASE_URL ?? "http://localhost:8000";
}

function copyHeaders(source: Headers): Headers {
  const out = new Headers();
  for (const [key, value] of source) {
    if (!HOP_BY_HOP_HEADERS[key.toLowerCase()]) {
      out.set(key, value);
    }
  }
  return out;
}

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await context.params;
  const upstreamUrl = new URL(`/${path.join("/")}`, apiBaseUrl());
  upstreamUrl.search = request.nextUrl.search;

  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  const upstreamResponse = await fetch(upstreamUrl, {
    method: request.method,
    headers: copyHeaders(request.headers),
    body: hasBody ? request.body : undefined,
    // Node's fetch (undici) requires `duplex: "half"` for a streamed
    // request body; not yet part of the DOM `RequestInit` type.
    ...(hasBody ? { duplex: "half" } : {}),
    redirect: "manual",
    cache: "no-store",
    // Propagates a browser disconnect (tab close, stop button, navigation)
    // to the upstream connection so the API cancels the turn task
    // (contracts.md → Streaming events → Client disconnect; SSE mapping →
    // Next.js proxy passthrough).
    signal: request.signal,
  } as RequestInit & { duplex?: "half" });

  const responseHeaders = copyHeaders(upstreamResponse.headers);
  // Forced independently of next.config.ts's `compress: false` (contracts.md
  // → Streaming events → SSE mapping → Next.js proxy passthrough): the two
  // layers are separate and both must hold for streamed bytes to reach the
  // browser as the API flushes them, unbuffered.
  responseHeaders.set("content-encoding", "identity");

  return new Response(upstreamResponse.body, {
    status: upstreamResponse.status,
    statusText: upstreamResponse.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
