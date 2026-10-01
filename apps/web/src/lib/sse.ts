// Parses the SSE wire format the proxy forwards byte-for-byte
// (docs/contracts.md → Streaming events → SSE mapping):
//
//   event: <type>\ndata: <json>\n\n
//
// Mana Leak's SSE format is its own (not the AI SDK `useChat` protocol), so
// it is parsed here rather than with a library. `: ping` comment lines are
// ignored; they exist only to keep the connection alive while the server
// waits on the model.

export interface RawSseEvent {
  event: string;
  data: string;
}

export async function* parseEventStream(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<RawSseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  function* drainComplete(): Generator<RawSseEvent> {
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const rawEvent = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const parsed = parseRawEvent(rawEvent);
      if (parsed) yield parsed;
      boundary = buffer.indexOf("\n\n");
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: true });
      if (done) {
        // Flush any bytes TextDecoder is still holding (e.g. a multi-byte
        // UTF-8 sequence split across the last two chunks).
        buffer += decoder.decode();
        // Normalised across the *whole* buffer, not per read() chunk, so a
        // `\r`/`\n` pair split across two chunks still collapses correctly
        // (D1: events split across read chunks).
        buffer = buffer.replace(/\r\n/g, "\n");
        yield* drainComplete();
        // The upstream connection can close right after the last event's
        // `data:` line, before the trailing blank line that normally
        // terminates it (observed: a `final` event dropped at EOF, D1).
        // Treat a non-empty remainder as one last event instead of
        // discarding it.
        if (buffer.trim().length > 0) {
          const parsed = parseRawEvent(buffer);
          if (parsed) yield parsed;
        }
        break;
      }
      buffer = buffer.replace(/\r\n/g, "\n");
      yield* drainComplete();
    }
  } finally {
    reader.releaseLock();
  }
}

function parseRawEvent(rawEvent: string): RawSseEvent | null {
  let eventType = "message";
  const dataLines: string[] = [];
  for (const line of rawEvent.split("\n")) {
    if (line.startsWith(":")) continue; // comment (e.g. `: ping`)
    if (line.startsWith("event:")) eventType = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  return { event: eventType, data: dataLines.join("\n") };
}
