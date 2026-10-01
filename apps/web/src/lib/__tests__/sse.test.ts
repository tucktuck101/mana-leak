import { describe, expect, it } from "vitest";

import { parseEventStream } from "../sse";

// Builds a ReadableStream<Uint8Array> from a fixed sequence of chunks, each
// chunk emitted as its own reader.read() call — the exact shape the proxy
// forwards bytes in (byte-for-byte, unbuffered), so these tests exercise
// real chunk boundaries rather than a single in-memory string.
function streamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) {
        controller.enqueue(encoder.encode(chunks[i]));
        i += 1;
      } else {
        controller.close();
      }
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>) {
  const events: { event: string; data: unknown }[] = [];
  for await (const raw of parseEventStream(stream)) {
    events.push({ event: raw.event, data: JSON.parse(raw.data) });
  }
  return events;
}

describe("parseEventStream", () => {
  it("reassembles an event whose data line is split across two read() chunks", async () => {
    // The `data:` line itself is cut mid-JSON across the chunk boundary —
    // the parser must not look for "event:"/"data:" prefixes per chunk, only
    // once a full `\n\n`-terminated event has accumulated in the buffer.
    const stream = streamFromChunks([
      'event: text_delta\ndata: {"type":"text_delta","de',
      'lta":"combat damage"}\n\n',
    ]);
    const events = await collect(stream);
    expect(events).toEqual([
      { event: "text_delta", data: { type: "text_delta", delta: "combat damage" } },
    ]);
  });

  it("flushes a final event that has no trailing blank line before the stream closes", async () => {
    // Reproduces the observed defect: the upstream connection closes right
    // after the last event's `data:` line, with no terminating `\n\n`. A
    // parser that only yields on an explicit "\n\n" boundary silently drops
    // this event instead of flushing it at EOF.
    const stream = streamFromChunks([
      'event: final\ndata: {"type":"final","text":"…21 combat damage with your commander."}',
    ]);
    const events = await collect(stream);
    expect(events).toEqual([
      {
        event: "final",
        data: { type: "final", text: "…21 combat damage with your commander." },
      },
    ]);
  });

  it("surfaces final.text verbatim, independent of preceding text_delta payloads", async () => {
    // The parser must not accumulate or merge event data itself — each
    // event's data is self-contained so the UI can treat `final.text` as the
    // authoritative replacement for whatever partial text the deltas built.
    const stream = streamFromChunks([
      'event: text_delta\ndata: {"type":"text_delta","delta":"Hello wor"}\n\n',
      'event: text_delta\ndata: {"type":"text_delta","delta":"ld"}\n\n',
      'event: final\ndata: {"type":"final","text":"Hello, world!"}\n\n',
    ]);
    const events = await collect(stream);
    expect(events).toEqual([
      { event: "text_delta", data: { type: "text_delta", delta: "Hello wor" } },
      { event: "text_delta", data: { type: "text_delta", delta: "ld" } },
      { event: "final", data: { type: "final", text: "Hello, world!" } },
    ]);
    const finalEvent = events[2].data as { text: string };
    expect(finalEvent.text).not.toBe("Hello world");
    expect(finalEvent.text).toBe("Hello, world!");
  });
});
