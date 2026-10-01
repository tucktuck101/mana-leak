"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { ConversationDetail, ErrorResponse, MessageOut } from "@/lib/contracts";
import { parseEventStream } from "@/lib/sse";

interface ChatMessage {
  id: string;
  role: MessageOut["role"];
  content: string;
}

type LoadState = "loading" | "ready" | "not_found" | "error";

export function ChatView({ conversationId }: { conversationId: string }) {
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [title, setTitle] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamingMessageId, setStreamingMessageId] = useState<string | null>(null);
  const [turnError, setTurnError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Load persisted history on mount (and after a reload) so the browser
  // resumes the full conversation (FR-3, AC-2).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/conversations/${conversationId}`);
        if (res.status === 404) {
          if (!cancelled) setLoadState("not_found");
          return;
        }
        if (!res.ok) {
          if (!cancelled) setLoadState("error");
          return;
        }
        const detail = (await res.json()) as ConversationDetail;
        if (cancelled) return;
        setTitle(detail.title);
        // Ascending seq, matching persistence (WP6 checklist), independent
        // of the order the API happens to return.
        const ordered = [...detail.messages].sort((a, b) => a.seq - b.seq);
        setMessages(ordered.map((m) => ({ id: m.id, role: m.role, content: m.content })));
        setLoadState("ready");
      } catch {
        if (!cancelled) setLoadState("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  // Abort any in-flight turn if the user navigates away.
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

  async function sendMessage() {
    const content = input.trim();
    if (!content || streaming) return;

    const localUserId = `local-${crypto.randomUUID()}`;
    setMessages((prev) => [...prev, { id: localUserId, role: "user", content }]);
    setInput("");
    setTurnError(null);
    setStreaming(true);
    setStreamingMessageId(null);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch(`/api/conversations/${conversationId}/messages`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ content }),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        const body = (await res.json()) as ErrorResponse;
        if (body.error.code === "validation_error") {
          // Deterministic validation rejects before anything is persisted
          // (contracts.md → REST API → Conversation behaviour) — drop the
          // optimistic bubble so it doesn't survive a reload it never will.
          setMessages((prev) => prev.filter((m) => m.id !== localUserId));
        }
        setTurnError(body.error.message);
        setStreaming(false);
        return;
      }

      for await (const raw of parseEventStream(res.body)) {
        const event = JSON.parse(raw.data) as { type: string; [key: string]: unknown };
        switch (event.type) {
          case "message_start": {
            const messageId = event.message_id as string;
            setStreamingMessageId(messageId);
            appendOrUpdateMessage(messageId, "assistant", () => "");
            break;
          }
          case "text_delta": {
            const delta = event.delta as string;
            setStreamingMessageId((current) => {
              if (current) appendOrUpdateMessage(current, "assistant", (prev) => prev + delta);
              return current;
            });
            break;
          }
          case "final": {
            const text = event.text as string;
            const error = event.error as ErrorResponse["error"] | null;
            setStreamingMessageId((current) => {
              if (current) appendOrUpdateMessage(current, "assistant", () => text);
              return current;
            });
            if (error) setTurnError(error.message);
            break;
          }
          case "error": {
            const error = event.error as ErrorResponse["error"];
            setTurnError(error.message);
            break;
          }
          case "message_end":
            break;
          default:
            break;
        }
      }
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        setTurnError(err instanceof Error ? err.message : "The connection was lost.");
      }
    } finally {
      setStreaming(false);
      setStreamingMessageId(null);
      abortRef.current = null;
    }
  }

  function stopStreaming() {
    abortRef.current?.abort();
  }

  if (loadState === "loading") {
    return <main style={{ padding: "4rem" }}>Loading…</main>;
  }
  if (loadState === "not_found") {
    return (
      <main style={{ padding: "4rem", fontFamily: "system-ui, sans-serif" }}>
        <p>Conversation not found.</p>
        <Link href="/">Start a new chat</Link>
      </main>
    );
  }
  if (loadState === "error") {
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
      {turnError && <p style={{ color: "crimson" }}>{turnError}</p>}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void sendMessage();
        }}
        style={{ display: "flex", gap: "0.5rem" }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about a card, a combo, or a ruling…"
          disabled={streaming}
          style={{ flex: 1, padding: "0.5rem" }}
        />
        {streaming ? (
          <button type="button" onClick={stopStreaming}>
            Stop
          </button>
        ) : (
          <button type="submit" disabled={!input.trim()}>
            Send
          </button>
        )}
      </form>
    </main>
  );
}
