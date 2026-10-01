"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { ConversationDetail, ErrorResponse, MessageOut } from "@/lib/contracts";
import { parseEventStream } from "@/lib/sse";
import { useHydrated } from "@/lib/hydrated";

interface ChatMessage {
  id: string;
  role: MessageOut["role"];
  content: string;
  // An optimistic bubble shown before the server echoes the persisted
  // message back with its own id (the stream announces the assistant
  // message id only — contracts.md → Streaming events → `MessageStart`).
  pending?: boolean;
}

type HistoryState = "loading" | "ready" | "not_found" | "error";
type Notice = { kind: "error" | "info"; text: string };

export function ChatView({ conversationId }: { conversationId: string }) {
  const [historyState, setHistoryState] = useState<HistoryState>("loading");
  const [title, setTitle] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamingMessageId, setStreamingMessageId] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  // The composer is inert until React runs: before that, Send is a plain
  // submit button in a real <form>, so a click (or Enter) navigates the
  // browser to `/c/<id>?`, which throws away the typed message and drops an
  // in-flight turn. Disabled controls cannot be typed into, clicked, or
  // implicitly submitted, so that window is closed instead of being lost
  // work. See `useHydrated`.
  const hydrated = useHydrated();

  // Refs, not state, for everything the turn loop reads while it runs: a send
  // spans many awaits, and React state read from the enclosing render's
  // closure would be stale by the time an event arrives.
  const abortRef = useRef<AbortController | null>(null);
  const streamingRef = useRef(false);
  const streamingMessageIdRef = useRef<string | null>(null);
  const turnStartedRef = useRef(false);
  const mountedRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Reconciles a history snapshot with what this mount already shows. The
  // snapshot describes the conversation as of the moment its GET was issued,
  // so it can be older than a turn that is already streaming: it must never
  // remove the optimistic user bubble, truncate the answer being streamed, or
  // show the user's message twice once the server's copy appears in it.
  const mergeHistory = useCallback((serverMessages: MessageOut[]) => {
    // Ascending seq, matching persistence (WP6 checklist), independent of the
    // order the API happens to return.
    const ordered = [...serverMessages].sort((a, b) => a.seq - b.seq);
    setMessages((local) => {
      const streamingId = streamingMessageIdRef.current;
      const unmatchedPending = local.filter((m) => m.pending);
      const merged: ChatMessage[] = [];
      for (const server of ordered) {
        if (server.id === streamingId) {
          // The locally streamed text is newer than any snapshot of it.
          const current = local.find((m) => m.id === streamingId);
          merged.push(current ?? { id: server.id, role: server.role, content: server.content });
          continue;
        }
        const pendingIndex = unmatchedPending.findIndex(
          (p) => p.role === server.role && p.content === server.content,
        );
        if (pendingIndex !== -1) unmatchedPending.splice(pendingIndex, 1);
        merged.push({ id: server.id, role: server.role, content: server.content });
      }
      const mergedIds = new Set(merged.map((m) => m.id));
      for (const m of local) {
        if (mergedIds.has(m.id)) continue;
        // Dropped only when the snapshot already contains this exact message
        // under its persisted id; anything else this turn produced is kept.
        if (m.pending && !unmatchedPending.includes(m)) continue;
        merged.push(m);
      }
      return merged;
    });
  }, []);

  // Load persisted history on mount (and after a reload) so the browser
  // resumes the full conversation (FR-3, AC-2). The composer is usable while
  // this is in flight — a conversation created a moment ago has no history to
  // wait for — so this can resolve mid-turn; it never aborts the turn and
  // never replaces the streaming view with a load-failure screen.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/conversations/${conversationId}`);
        if (cancelled) return;
        if (!res.ok) {
          setHistoryState(
            turnStartedRef.current ? "ready" : res.status === 404 ? "not_found" : "error",
          );
          return;
        }
        const detail = (await res.json()) as ConversationDetail;
        if (cancelled) return;
        setTitle(detail.title);
        mergeHistory(detail.messages);
        setHistoryState("ready");
      } catch {
        if (!cancelled) setHistoryState(turnStartedRef.current ? "ready" : "error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [conversationId, mergeHistory]);

  // Abort an in-flight turn when the view really goes away. This is the only
  // automatic abort: a re-render, a resolved history load, or a rejected send
  // must never reach it, because the server treats a dropped connection as a
  // client disconnect and truncates the persisted answer (contracts.md →
  // Streaming events → Client disconnect).
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  function appendOrUpdateMessage(id: string, role: ChatMessage["role"], update: (prev: string) => string) {
    setMessages((prev) => {
      const index = prev.findIndex((m) => m.id === id);
      if (index === -1) {
        return [...prev, { id, role, content: update("") }];
      }
      const next = [...prev];
      next[index] = { ...next[index], content: update(next[index].content) };
      return next;
    });
  }

  // The server sets `conversation.title` from the first user message
  // (data-model.md → conversation) but the heading is rendered from local
  // state set on mount; refresh it from the authoritative source once the
  // turn settles instead of duplicating the server's title-derivation logic
  // client-side (D2). Runs however the turn ended — a stopped or failed first
  // turn still titles the conversation, so the heading must not wait for a
  // reload.
  const refreshTitle = useCallback(async () => {
    try {
      const res = await fetch(`/api/conversations/${conversationId}`);
      if (!res.ok) return;
      const detail = (await res.json()) as ConversationDetail;
      if (mountedRef.current) setTitle(detail.title);
    } catch {
      // Best effort — the next turn or reload will still show the right title.
    }
  }, [conversationId]);

  async function readErrorMessage(res: Response): Promise<string> {
    try {
      const body = (await res.json()) as ErrorResponse;
      if (body?.error?.message) return body.error.message;
    } catch {
      // Not an ErrorResponse (e.g. a proxy-level failure) — fall through.
    }
    return `The server rejected this message (HTTP ${res.status}).`;
  }

  async function sendMessage() {
    const content = input.trim();
    // Synchronous guard: two clicks in the same frame both see `streaming`
    // from the render that produced them, so the ref decides. A send that
    // cannot proceed says why and leaves the text where the user typed it —
    // no message is ever silently dropped.
    if (streamingRef.current) {
      setNotice({
        kind: "info",
        text: "Mana Leak is still answering. Your message is still in the box — send it when this answer finishes, or press Stop first.",
      });
      return;
    }
    if (!content) {
      setNotice({ kind: "info", text: "Type a question before sending." });
      return;
    }

    const localUserId = `local-${crypto.randomUUID()}`;
    setMessages((prev) => [...prev, { id: localUserId, role: "user", content, pending: true }]);
    setInput("");
    setNotice(null);
    streamingRef.current = true;
    turnStartedRef.current = true;
    setStreaming(true);
    streamingMessageIdRef.current = null;
    setStreamingMessageId(null);

    const controller = new AbortController();
    abortRef.current = controller;
    let stopped = false;

    try {
      const res = await fetch(`/api/conversations/${conversationId}/messages`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ content }),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        // A request rejected before the stream starts persists nothing
        // (contracts.md → REST API → Conversation behaviour: validation, then
        // the user message, then the turn), so the optimistic bubble goes and
        // the text is handed back for a retry instead of vanishing.
        const message = await readErrorMessage(res);
        setMessages((prev) => prev.filter((m) => m.id !== localUserId));
        setInput((current) => (current === "" ? content : current));
        setNotice({ kind: "error", text: message });
        return;
      }

      for await (const raw of parseEventStream(res.body)) {
        const event = JSON.parse(raw.data) as { type: string; [key: string]: unknown };
        switch (event.type) {
          case "message_start": {
            const messageId = event.message_id as string;
            streamingMessageIdRef.current = messageId;
            setStreamingMessageId(messageId);
            appendOrUpdateMessage(messageId, "assistant", () => "");
            break;
          }
          case "text_delta": {
            const id = streamingMessageIdRef.current;
            if (id) appendOrUpdateMessage(id, "assistant", (prev) => prev + (event.delta as string));
            break;
          }
          case "final": {
            const id = streamingMessageIdRef.current;
            const text = event.text as string;
            const error = event.error as ErrorResponse["error"] | null;
            if (id) appendOrUpdateMessage(id, "assistant", () => text);
            if (error) setNotice({ kind: "error", text: error.message });
            break;
          }
          case "error": {
            const error = event.error as ErrorResponse["error"];
            setNotice({ kind: "error", text: error.message });
            break;
          }
          default:
            // `message_end` closes the stream; the turn settles below.
            break;
        }
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        stopped = true;
      } else {
        setNotice({
          kind: "error",
          text: err instanceof Error ? err.message : "The connection was lost.",
        });
      }
    } finally {
      streamingRef.current = false;
      abortRef.current = null;
      streamingMessageIdRef.current = null;
      setStreaming(false);
      setStreamingMessageId(null);
      if (stopped && mountedRef.current) {
        setNotice({
          kind: "info",
          text: "Answer stopped. What is shown above is what the server recorded.",
        });
      }
      if (mountedRef.current) void refreshTitle();
    }
  }

  function stopStreaming() {
    abortRef.current?.abort();
  }

  if (historyState === "not_found") {
    return (
      <main style={{ padding: "4rem", fontFamily: "system-ui, sans-serif" }}>
        <p>Conversation not found.</p>
        <Link href="/">Start a new chat</Link>
      </main>
    );
  }
  if (historyState === "error") {
    return (
      <main style={{ padding: "4rem", fontFamily: "system-ui, sans-serif" }}>
        <p>Could not load this conversation.</p>
        <Link href="/">Start a new chat</Link>
      </main>
    );
  }

  return (
    <main style={{ padding: "2rem", fontFamily: "system-ui, sans-serif", maxWidth: 720 }}>
      <p>
        <Link href="/">← New chat</Link>
      </p>
      <h1>{title ?? "Untitled conversation"}</h1>
      {historyState === "loading" && <p>Loading earlier messages…</p>}
      <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem", margin: "1.5rem 0" }}>
        {messages.map((message) => (
          <div key={message.id}>
            <strong>{message.role === "user" ? "You" : "Mana Leak"}:</strong>{" "}
            <span style={{ whiteSpace: "pre-wrap" }}>
              {message.content}
              {streaming && message.id === streamingMessageId ? " ▋" : ""}
            </span>
          </div>
        ))}
        {streaming && streamingMessageId === null && <p>Mana Leak is thinking…</p>}
      </div>
      {notice && (
        <p role="status" style={{ color: notice.kind === "error" ? "crimson" : "#555" }}>
          {notice.text}
        </p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void sendMessage();
        }}
        style={{ display: "flex", gap: "0.5rem" }}
      >
        {/* The composer never changes shape while a turn runs: the input stays
            editable and Send keeps its place, so the next question can be
            typed during an answer and a click where Send was can never land on
            Stop and kill the turn. Stop is a separate control that exists only
            while there is something to stop. The only time the composer is
            disabled is before hydration, where it cannot work at all. */}
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={hydrated ? "Ask about a card, a combo, or a ruling…" : "Starting up…"}
          aria-label="Message"
          disabled={!hydrated}
          style={{ flex: 1, padding: "0.5rem" }}
        />
        <button type="submit" disabled={!hydrated}>
          Send
        </button>
        {streaming && (
          <button type="button" onClick={stopStreaming}>
            Stop
          </button>
        )}
      </form>
    </main>
  );
}
