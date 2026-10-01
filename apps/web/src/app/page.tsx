"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useHydrated } from "@/lib/hydrated";
import type { ConversationSummary, ErrorResponse } from "@/lib/contracts";

export default function Home() {
  const router = useRouter();
  const [conversations, setConversations] = useState<ConversationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  // A click on the server-rendered button before React attaches its handler
  // is simply lost, with nothing on screen to say so (observed in headless
  // Chromium with a slow client bundle: "New chat" did nothing and no
  // conversation was created). Disabled until the handler exists, so the
  // click either works or visibly cannot be made yet.
  const hydrated = useHydrated();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/conversations");
        if (!res.ok) {
          const body = (await res.json()) as ErrorResponse;
          throw new Error(body.error.message);
        }
        const data = (await res.json()) as ConversationSummary[];
        if (!cancelled) setConversations(data);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load conversations.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function startConversation() {
    setCreating(true);
    setError(null);
    try {
      const res = await fetch("/api/conversations", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        const body = (await res.json()) as ErrorResponse;
        throw new Error(body.error.message);
      }
      const conversation = (await res.json()) as ConversationSummary;
      router.push(`/c/${conversation.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start a conversation.");
      setCreating(false);
    }
  }

  return (
    <main style={{ padding: "4rem", fontFamily: "system-ui, sans-serif", maxWidth: 640 }}>
      <h1>Mana Leak</h1>
      <button type="button" onClick={startConversation} disabled={creating || !hydrated}>
        {creating ? "Starting…" : "New chat"}
      </button>
      {error && <p style={{ color: "crimson" }}>{error}</p>}
      <h2 style={{ marginTop: "2rem" }}>Conversations</h2>
      {conversations === null && <p>Loading…</p>}
      {conversations !== null && conversations.length === 0 && <p>No conversations yet.</p>}
      {conversations !== null && conversations.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0 }}>
          {conversations.map((conversation) => (
            <li key={conversation.id} style={{ marginBottom: "0.5rem" }}>
              <Link href={`/c/${conversation.id}`}>{conversation.title ?? conversation.id}</Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
