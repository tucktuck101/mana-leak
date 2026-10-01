// @vitest-environment jsdom
//
// Regression tests for the defect observed in a real browser against the
// running stack: the first turn of a new chat was cut off ~2 s in, the server
// recorded `payload.error = {"code":"timeout","message":"client
// disconnected"}`, the follow-up message never reached the server, and the
// heading stayed "Untitled conversation" until a reload.
//
// Root cause: the composer swapped Send for Stop in the same slot and
// disabled the input while a turn streamed, so typing the next question did
// nothing and the click meant for Send hit Stop — aborting the turn. With the
// turn aborted, `message_end` never arrived and the title refresh (which hung
// off that event) never ran.
//
// These tests drive the component through its real fetch/SSE path; only the
// network boundary is faked.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";

import { ChatView } from "../ChatView";

const CONVERSATION_ID = "11111111-1111-4111-8111-111111111111";
const ASSISTANT_ID = "22222222-2222-4222-8222-222222222222";

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function conversationDetail(
  title: string | null,
  messages: Array<{ id: string; seq: number; role: "user" | "assistant"; content: string }> = [],
) {
  return {
    id: CONVERSATION_ID,
    title,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    messages: messages.map((m) => ({ ...m, conversation_id: CONVERSATION_ID, payload: null })),
  };
}

/**
 * A controllable SSE response, written event by event like the API does and
 * — like a real `fetch` body — errored with an `AbortError` when the request
 * signal aborts, so the component's abort path is exercised for real.
 */
class SseResponse {
  private controller!: ReadableStreamDefaultController<Uint8Array>;
  readonly response: Response;
  readonly signal: AbortSignal;

  constructor(signal: AbortSignal) {
    this.signal = signal;
    const stream = new ReadableStream<Uint8Array>({
      start: (controller) => {
        this.controller = controller;
        signal.addEventListener("abort", () => {
          controller.error(new DOMException("The operation was aborted.", "AbortError"));
        });
      },
    });
    this.response = new Response(stream, {
      status: 200,
      headers: { "content-type": "text/event-stream; charset=utf-8" },
    });
  }

  async emit(type: string, payload: Record<string, unknown> = {}): Promise<void> {
    const data = JSON.stringify({
      type,
      conversation_id: CONVERSATION_ID,
      turn_id: "33333333-3333-4333-8333-333333333333",
      ...payload,
    });
    await act(async () => {
      this.controller.enqueue(new TextEncoder().encode(`event: ${type}\ndata: ${data}\n\n`));
      await Promise.resolve();
    });
  }

  async close(): Promise<void> {
    await act(async () => {
      this.controller.close();
      await Promise.resolve();
    });
  }
}

interface FetchCall {
  url: string;
  method: string;
  body: string | null;
  signal: AbortSignal | null;
}

let calls: FetchCall[];
let historyResponses: Array<Promise<Response> | Response>;
let turnHandler: (signal: AbortSignal) => Promise<Response>;
let openStreams: SseResponse[];

beforeEach(() => {
  calls = [];
  openStreams = [];
  historyResponses = [];
  turnHandler = async (signal) => {
    const stream = new SseResponse(signal);
    openStreams.push(stream);
    return stream.response;
  };

  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({
      url,
      method,
      body: typeof init?.body === "string" ? init.body : null,
      signal: init?.signal ?? null,
    });
    if (method === "POST") {
      return turnHandler(init?.signal as AbortSignal);
    }
    const next = historyResponses.shift();
    if (next === undefined) return Promise.resolve(jsonResponse(conversationDetail(null)));
    return Promise.resolve(next);
  });

  if (typeof globalThis.crypto?.randomUUID !== "function") {
    vi.stubGlobal("crypto", { ...globalThis.crypto, randomUUID: () => "test-uuid" });
  }
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function historyCalls(): FetchCall[] {
  return calls.filter((c) => c.method === "GET");
}

function turnCalls(): FetchCall[] {
  return calls.filter((c) => c.method === "POST");
}

async function send(text: string): Promise<void> {
  fireEvent.change(screen.getByPlaceholderText(/Ask about a card/), { target: { value: text } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await Promise.resolve();
  });
}

/** Mounts, lets the history GET settle, sends `text`, and returns the stream. */
async function startTurn(text: string, detail = conversationDetail(null)): Promise<SseResponse> {
  historyResponses.push(jsonResponse(detail));
  render(<ChatView conversationId={CONVERSATION_ID} />);
  await screen.findByRole("button", { name: "Send" });
  await act(async () => {
    await Promise.resolve();
  });
  await send(text);
  await waitFor(() => expect(openStreams).toHaveLength(1));
  return openStreams[0];
}

describe("ChatView", () => {
  it("keeps the streaming turn when the history snapshot arrives mid-turn", async () => {
    // The history GET is issued at mount but resolves only after the turn has
    // started, and its snapshot already contains the persisted user message.
    const history = deferred<Response>();
    historyResponses.push(history.promise);
    render(<ChatView conversationId={CONVERSATION_ID} />);
    await send("Why does this combo work?");
    await waitFor(() => expect(openStreams).toHaveLength(1));
    const stream = openStreams[0];

    await stream.emit("message_start", { message_id: ASSISTANT_ID });
    await stream.emit("text_delta", { delta: "Because " });

    await act(async () => {
      history.resolve(
        jsonResponse(
          conversationDetail("Why does this combo work?", [
            { id: "user-1", seq: 1, role: "user", content: "Why does this combo work?" },
          ]),
        ),
      );
      await history.promise;
    });

    // The in-flight stream survives: not aborted, text intact, still growing.
    expect(stream.signal.aborted).toBe(false);
    await stream.emit("text_delta", { delta: "the loop never ends." });
    await waitFor(() =>
      expect(screen.getByText(/Because the loop never ends\./)).toBeTruthy(),
    );
    // The user's message is shown exactly once, not duplicated by the
    // snapshot's persisted copy of it (the heading carries the same text).
    expect(screen.getAllByText(/Why does this combo work\?/, { selector: "span" })).toHaveLength(1);

    await stream.emit("final", { route: "other", text: "Because the loop never ends.", result: null, error: null });
    await stream.emit("message_end");
    await stream.close();
    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeTruthy());
  });

  it("does not abort an in-flight stream on re-render or on a send attempt", async () => {
    const stream = await startTurn("First question");
    await stream.emit("message_start", { message_id: ASSISTANT_ID });
    await stream.emit("text_delta", { delta: "Working on it" });

    // Typing the next question (a re-render, and the exact thing the user does
    // while waiting) must not disturb the turn, and the input must accept it.
    fireEvent.change(screen.getByPlaceholderText(/Ask about a card/), { target: { value: "Second question" } });
    expect((screen.getByPlaceholderText(/Ask about a card/) as HTMLInputElement).value).toBe("Second question");
    expect(stream.signal.aborted).toBe(false);

    // Clicking Send while the answer streams: the defect was that this click
    // landed on Stop. It must not abort, must not drop the message silently,
    // and must say why it cannot go yet.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Send" }));
      await Promise.resolve();
    });
    expect(stream.signal.aborted).toBe(false);
    expect(turnCalls()).toHaveLength(1);
    expect((screen.getByPlaceholderText(/Ask about a card/) as HTMLInputElement).value).toBe("Second question");
    expect(screen.getByRole("status").textContent).toMatch(/still answering/i);
  });

  it("keeps the turn alive when the user clicks where Send was while it streams", async () => {
    // The reported defect, stated without naming the implementation: during a
    // turn, the first button of the composer used to be Stop, so the click
    // meant for Send killed the answer the server was still writing.
    const stream = await startTurn("First question");
    await stream.emit("message_start", { message_id: ASSISTANT_ID });
    await stream.emit("text_delta", { delta: "Working on it" });

    const firstComposerButton = document.querySelector("form button") as HTMLButtonElement;
    await act(async () => {
      fireEvent.click(firstComposerButton);
      await Promise.resolve();
    });

    expect(stream.signal.aborted).toBe(false);
    await stream.emit("text_delta", { delta: ", still." });
    await waitFor(() => expect(screen.getByText(/Working on it, still\./)).toBeTruthy());
  });

  it("aborts only on the Stop button", async () => {
    const stream = await startTurn("First question");
    await stream.emit("message_start", { message_id: ASSISTANT_ID });
    expect(stream.signal.aborted).toBe(false);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Stop" }));
      await Promise.resolve();
    });
    expect(stream.signal.aborted).toBe(true);
    await waitFor(() => expect(screen.getByRole("status").textContent).toMatch(/stopped/i));
  });

  it("aborts an in-flight stream when the view unmounts", async () => {
    const stream = await startTurn("First question");
    await stream.emit("message_start", { message_id: ASSISTANT_ID });

    await act(async () => {
      cleanup();
      await Promise.resolve();
    });
    expect(stream.signal.aborted).toBe(true);
  });

  it("sends the next message after the first turn ends", async () => {
    const stream = await startTurn("First question");
    await stream.emit("message_start", { message_id: ASSISTANT_ID });
    await stream.emit("text_delta", { delta: "An answer." });
    await stream.emit("final", { route: "other", text: "An answer.", result: null, error: null });
    await stream.emit("message_end");
    await stream.close();

    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
    await send("Second question");

    await waitFor(() => expect(turnCalls()).toHaveLength(2));
    expect(turnCalls()[1].url).toBe(`/api/conversations/${CONVERSATION_ID}/messages`);
    expect(JSON.parse(turnCalls()[1].body as string)).toEqual({ content: "Second question" });
  });

  it("updates the heading from the server after the first turn, without a reload", async () => {
    const stream = await startTurn("What does trample do?");
    expect(screen.getByRole("heading").textContent).toBe("Untitled conversation");

    historyResponses.push(jsonResponse(conversationDetail("What does trample do?")));
    await stream.emit("message_start", { message_id: ASSISTANT_ID });
    await stream.emit("final", { route: "other", text: "It tramples.", result: null, error: null });
    await stream.emit("message_end");
    await stream.close();

    await waitFor(() =>
      expect(screen.getByRole("heading").textContent).toBe("What does trample do?"),
    );
    expect(historyCalls()).toHaveLength(2);
  });

  it("updates the heading even when the turn was stopped", async () => {
    const stream = await startTurn("What does trample do?");
    await stream.emit("message_start", { message_id: ASSISTANT_ID });
    historyResponses.push(jsonResponse(conversationDetail("What does trample do?")));

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Stop" }));
      await Promise.resolve();
    });

    await waitFor(() =>
      expect(screen.getByRole("heading").textContent).toBe("What does trample do?"),
    );
  });

  it("hands the text back and explains when the server rejects the send", async () => {
    historyResponses.push(jsonResponse(conversationDetail(null)));
    render(<ChatView conversationId={CONVERSATION_ID} />);
    await screen.findByRole("button", { name: "Send" });
    turnHandler = async () =>
      jsonResponse(
        { error: { code: "conflict", message: "A turn is already running for this conversation.", retryable: true, details: {} } },
        409,
      );

    await send("Second question too fast");

    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toBe(
        "A turn is already running for this conversation.",
      ),
    );
    // Nothing was persisted, so the optimistic bubble goes and the text comes
    // back to the composer for a retry.
    expect(screen.queryByText(/Second question too fast/)).toBeNull();
    expect((screen.getByPlaceholderText(/Ask about a card/) as HTMLInputElement).value).toBe(
      "Second question too fast",
    );
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });

  it("explains an empty send instead of ignoring it", async () => {
    historyResponses.push(jsonResponse(conversationDetail(null)));
    render(<ChatView conversationId={CONVERSATION_ID} />);
    await screen.findByRole("button", { name: "Send" });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Send" }));
      await Promise.resolve();
    });

    expect(turnCalls()).toHaveLength(0);
    expect(screen.getByRole("status").textContent).toMatch(/Type a question/i);
  });

  it("lets the first message be sent while history is still loading", async () => {
    const history = deferred<Response>();
    historyResponses.push(history.promise);
    render(<ChatView conversationId={CONVERSATION_ID} />);

    expect(screen.getByText("Loading earlier messages…")).toBeTruthy();
    await send("Immediate first question");

    await waitFor(() => expect(turnCalls()).toHaveLength(1));
    await act(async () => {
      history.resolve(jsonResponse(conversationDetail(null)));
      await history.promise;
    });
  });

  it("keeps a streaming turn visible when the history load fails", async () => {
    const history = deferred<Response>();
    historyResponses.push(history.promise);
    render(<ChatView conversationId={CONVERSATION_ID} />);
    await send("Immediate first question");
    await waitFor(() => expect(openStreams).toHaveLength(1));
    const stream = openStreams[0];
    await stream.emit("message_start", { message_id: ASSISTANT_ID });
    await stream.emit("text_delta", { delta: "Still answering" });

    await act(async () => {
      history.resolve(jsonResponse({ error: { code: "internal_error", message: "boom" } }, 500));
      await history.promise;
    });

    expect(screen.queryByText("Could not load this conversation.")).toBeNull();
    expect(screen.getByText(/Still answering/)).toBeTruthy();
    expect(stream.signal.aborted).toBe(false);
  });

  it("server-renders a composer that cannot submit before React takes over", () => {
    // The browser shows this HTML, and acts on it, before the client bundle
    // runs. If the composer were live there, a click on Send would be a plain
    // form submission: the browser would navigate to `/c/<id>?`, dropping the
    // typed message and any in-flight turn (the server then records the
    // answer as `payload.error = {"code":"timeout","message":"client
    // disconnected"}`). Verified end to end in
    // `apps/web/e2e/first-turn.spec.ts`.
    const html = renderToStaticMarkup(<ChatView conversationId={CONVERSATION_ID} />);

    const input = /<input[^>]*>/.exec(html)?.[0] ?? "";
    expect(input).toMatch(/\bdisabled\b/);
    const send = /<button[^>]*>Send<\/button>/.exec(html)?.[0] ?? "";
    expect(send).toMatch(/\bdisabled\b/);
  });
});
