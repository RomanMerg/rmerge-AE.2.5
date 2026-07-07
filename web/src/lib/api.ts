import { SSEParser } from "@/lib/sse";

export interface Source {
  title: string;
  similarity: number;
}

export interface ChatDone {
  session_id: string;
  reply: string;
  sources: Source[];
  turns_remaining: number;
  tokens_used: number;
  cost_usd: number;
}

export interface StreamHandlers {
  onToken(content: string): void;
  onDone(done: ChatDone): void;
  onError(message: string): void;
}

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * POST /chat/stream and dispatch its SSE events to handlers. Exactly one of
 * onDone/onError fires per call. EventSource can't POST, hence fetch + reader.
 */
export async function streamChat(
  message: string,
  sessionId: string | null,
  handlers: StreamHandlers,
  apiUrl: string = API_URL,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${apiUrl}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        sessionId ? { message, session_id: sessionId } : { message },
      ),
    });
  } catch {
    handlers.onError("Could not reach the advisor — is the backend running?");
    return;
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // non-JSON error body — keep the generic detail
    }
    handlers.onError(detail);
    return;
  }

  const reader = response.body?.getReader();
  if (!reader) {
    handlers.onError("Streaming is not supported in this browser.");
    return;
  }

  const decoder = new TextDecoder();
  const parser = new SSEParser();
  let terminal = false;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const ev of parser.push(decoder.decode(value, { stream: true }))) {
        if (terminal) break;
        if (ev.event === "token") {
          handlers.onToken((JSON.parse(ev.data) as { content: string }).content);
        } else if (ev.event === "done") {
          const payload = JSON.parse(ev.data) as ChatDone;
          terminal = true;
          handlers.onDone(payload);
        } else if (ev.event === "error") {
          const detail = (JSON.parse(ev.data) as { detail: string }).detail;
          terminal = true;
          handlers.onError(detail);
        }
      }
      if (terminal) break;
    }
    if (!terminal) {
      handlers.onError("The reply was cut off — please retry.");
    }
  } catch {
    if (!terminal) handlers.onError("Connection lost mid-reply — please retry.");
  } finally {
    if (terminal) void reader.cancel().catch(() => {});
  }
}
