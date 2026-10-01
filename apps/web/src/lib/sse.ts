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
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const rawEvent = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);

        let eventType = "message";
        const dataLines: string[] = [];
        for (const line of rawEvent.split("\n")) {
          if (line.startsWith(":")) continue; // comment (e.g. `: ping`)
          if (line.startsWith("event:")) eventType = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
        }
        if (dataLines.length > 0) {
          yield { event: eventType, data: dataLines.join("\n") };
        }

        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}
