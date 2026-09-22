/**
 * A minimal, from-scratch SSE parser for a streaming POST response.
 *
 * `EventSource` can't be used here -- it only issues GET requests, and
 * this endpoint is a POST (see docs/DESIGN.md, "Streaming"). Consuming
 * `response.body` with `fetch` instead means network chunk boundaries are
 * NOT the same thing as SSE record boundaries: one chunk can contain
 * several complete records, or end mid-record with the rest arriving in
 * the next chunk. This buffers across reads and only ever yields complete
 * records -- never assumes `chunk.split("\n\n")` lines up with reality.
 *
 * `TextDecoder`'s streaming mode (`{ stream: true }`) is what makes this
 * safe for multi-byte UTF-8 text too: a character split across a chunk
 * boundary is held back by the decoder itself until the rest arrives,
 * rather than corrupted into replacement characters.
 */

export interface SseRecord {
  event: string;
  data: string;
}

function parseRecord(raw: string): SseRecord | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
    // Other SSE fields (id:, retry:, comments starting with ":") aren't
    // used by this application-level protocol and are ignored, not errored on.
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}

export async function* parseSseStream(body: ReadableStream<Uint8Array>): AsyncGenerator<SseRecord> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (value) {
        buffer += decoder.decode(value, { stream: true });
      }

      // SSE records are separated by a blank line. A chunk can complete
      // zero, one, or several records -- drain every complete one found so
      // far, and leave whatever's left (possibly nothing, possibly a
      // dangling partial record) in the buffer for the next read.
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const record = parseRecord(raw);
        if (record) yield record;
      }

      if (done) {
        buffer += decoder.decode(); // flush any pending multi-byte sequence
        const record = parseRecord(buffer);
        if (record) yield record;
        return;
      }
    }
  } finally {
    reader.releaseLock();
  }
}
