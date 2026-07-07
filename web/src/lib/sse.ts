export interface SSEEvent {
  event: string;
  data: string;
}

/**
 * Incremental parser for text/event-stream frames ("event: X\ndata: ...\n\n").
 * Feed decoded chunks in any split via push(); complete events are returned,
 * partial frames stay buffered until their terminating blank line arrives.
 */
export class SSEParser {
  private buffer = "";

  push(chunk: string): SSEEvent[] {
    this.buffer += chunk;
    const events: SSEEvent[] = [];
    let sep = this.buffer.indexOf("\n\n");
    while (sep !== -1) {
      const block = this.buffer.slice(0, sep);
      this.buffer = this.buffer.slice(sep + 2);
      const event = parseBlock(block);
      if (event) events.push(event);
      sep = this.buffer.indexOf("\n\n");
    }
    return events;
  }
}

function parseBlock(block: string): SSEEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice("event: ".length).trim();
    else if (line.startsWith("data: ")) dataLines.push(line.slice("data: ".length));
    // comment lines (":...") and anything else are ignored per the SSE spec
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}
