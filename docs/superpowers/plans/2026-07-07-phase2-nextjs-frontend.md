# Phase 2 — Functional Next.js Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A functional Next.js chat frontend in `web/` that consumes the backend's `POST /chat/stream` SSE endpoint — streaming replies, session persistence, turn counter, source citations, token/cost display — replacing the Gradio demo as the user-facing UI.

**Architecture:** A small, layered client: a pure incremental SSE parser (`lib/sse.ts`), a fetch-based streaming API client (`lib/api.ts`) that maps SSE events to typed handler callbacks, a `useChat` React hook owning all chat state (messages, streaming text, turns, errors, sessionStorage session id), and thin presentational components. **Functional-first per design spec Section 9 / "Session 6"**: plain Tailwind utility styling, no design system — the visual design is a separate design session (spec Section 13 brief) applied later. The backend is not modified in any way by this plan.

**Tech Stack:** Next.js 15 (App Router, TypeScript, `src/` dir, Tailwind), React 19, Vitest + React Testing Library + jsdom for unit tests, npm.

## Global Constraints

- **Backend untouched.** No file outside `web/` and the root `README.md` changes in this plan. The Gradio demo (`frontend/app.py`) stays as-is.
- **npm only, and only inside `web/`** (the backend stays uv). Node 20+ required.
- Tests run as `npm test` (vitest run) from `web/`. Never install packages globally.
- **Exact backend contract** (shipped, live-verified — do not "improve" it):
  - `POST {API_URL}/chat/stream`, JSON body `{"message": string, "session_id"?: string}` (omit `session_id` for a new session).
  - SSE frames, each `event: <name>\ndata: <json>\n\n`:
    - `token` → `{"content": string}` (zero or more)
    - `done` → `{"session_id": string, "reply": string, "sources": [{"title": string, "similarity": number}], "turns_remaining": number, "tokens_used": number, "cost_usd": number}` (exactly one on success)
    - `error` → `{"detail": string}` (terminal on mid-stream failure)
  - Pre-stream rejections are plain HTTP errors with JSON `{"detail": string}`: 422 (message > 600 chars), 429 (session turn limit / IP rate limit — distinguished only by detail text).
- Limits mirrored client-side as constants: `MAX_INPUT_CHARS = 600`, `MAX_TURNS = 8` (display + input guard only — the backend remains the enforcer).
- Session id lives in `sessionStorage` under key `automate-this-session-id` (anonymous, no accounts — spec Section 9).
- `NEXT_PUBLIC_API_URL` env var, default `http://localhost:8000`. Backend CORS already allows `http://localhost:3000` (config default) — do not touch backend CORS.
- No additional UI libraries (no shadcn, no component kits) — the design session decides those later.
- TypeScript strict mode (create-next-app default) stays on; no `any` except where a test mock genuinely needs it.
- Commit style: `feat:` / `fix:` / `docs:` / `chore:` prefixes, imperative mood.
- Platform note: Windows dev box — commands below run in Git Bash or PowerShell; none use POSIX-only syntax except where marked.

---

### Task 1: Scaffold Next.js app + test tooling

**Files:**
- Create: `web/` via create-next-app (package.json, src/app/..., tsconfig.json, etc.)
- Create: `web/vitest.config.mts`, `web/vitest.setup.ts`
- Create: `web/.env.local.example`, `web/.env.local`
- Test: `web/src/app/page.test.tsx` (smoke test)

**Interfaces:**
- Produces: the `web/` workspace all later tasks live in; `@/` alias → `web/src/`; `npm test` runs vitest; jsdom + jest-dom matchers available in all `*.test.ts(x)` files.

- [ ] **Step 1: Scaffold**

From the repo root:

```bash
npx create-next-app@15 web --typescript --app --tailwind --eslint --src-dir --import-alias "@/*" --use-npm
```

Accept defaults for any remaining prompts (Turbopack: yes is fine for dev; tests use vitest, not the bundler). Verify `web/src/app/page.tsx` exists afterwards.

- [ ] **Step 2: Add test tooling**

```bash
cd web
npm install -D vitest @vitejs/plugin-react jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event
```

Create `web/vitest.config.mts`:

```ts
import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
});
```

Create `web/vitest.setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
```

In `web/tsconfig.json`, add to `compilerOptions`:

```json
"types": ["vitest/globals", "@testing-library/jest-dom"]
```

In `web/package.json` scripts, add:

```json
"test": "vitest run",
"test:watch": "vitest"
```

- [ ] **Step 3: Env files**

Create `web/.env.local.example`:

```bash
# Backend API base URL (no trailing slash).
# Backend default port is 8000; on this dev machine it often runs on 8001
# (8000 held by another local service) — adjust to match.
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Copy it to `web/.env.local` (create-next-app's .gitignore already excludes `.env*.local` — verify with `git status`, `.env.local` must NOT appear).

- [ ] **Step 4: Write the smoke test**

Replace the scaffold's default home page content first — `web/src/app/page.tsx`:

```tsx
export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col p-4">
      <h1 className="text-2xl font-bold">Automate This — SMB Automation Advisor</h1>
      <p className="text-sm text-gray-500">
        Functional demo — production design pending.
      </p>
    </main>
  );
}
```

Create `web/src/app/page.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import Home from "./page";

test("home page renders the app title", () => {
  render(<Home />);
  expect(
    screen.getByRole("heading", { name: /automate this/i }),
  ).toBeInTheDocument();
});
```

- [ ] **Step 5: Run the test**

Run: `npm test` (from `web/`)
Expected: 1 passed.

- [ ] **Step 6: Build + lint gate**

```bash
npm run build
npm run lint
```

Expected: build succeeds, no lint errors (delete any scaffold demo files that fail lint rather than suppressing rules).

- [ ] **Step 7: Commit**

From repo root:

```bash
git add web
git commit -m "feat: scaffold Next.js frontend in web/ with vitest tooling"
```

---

### Task 2: Incremental SSE parser

**Files:**
- Create: `web/src/lib/sse.ts`
- Test: `web/src/lib/sse.test.ts`

**Interfaces:**
- Produces: `class SSEParser { push(chunk: string): SSEEvent[] }` and `interface SSEEvent { event: string; data: string }`. Task 3 feeds decoded fetch chunks into `push` and consumes complete events.

- [ ] **Step 1: Write the failing tests**

Create `web/src/lib/sse.test.ts`:

```ts
import { SSEParser } from "./sse";

test("parses a single complete event", () => {
  const p = new SSEParser();
  const events = p.push('event: token\ndata: {"content": "Hi"}\n\n');
  expect(events).toEqual([{ event: "token", data: '{"content": "Hi"}' }]);
});

test("parses multiple events arriving in one chunk", () => {
  const p = new SSEParser();
  const events = p.push(
    'event: token\ndata: {"content": "A"}\n\nevent: token\ndata: {"content": "B"}\n\n',
  );
  expect(events.map((e) => e.data)).toEqual(['{"content": "A"}', '{"content": "B"}']);
});

test("buffers an event split across chunk boundaries", () => {
  const p = new SSEParser();
  expect(p.push("event: to")).toEqual([]);
  expect(p.push('ken\ndata: {"content": "Hi"}\n')).toEqual([]);
  const events = p.push("\n");
  expect(events).toEqual([{ event: "token", data: '{"content": "Hi"}' }]);
});

test("defaults event name to message when absent", () => {
  const p = new SSEParser();
  const events = p.push("data: x\n\n");
  expect(events).toEqual([{ event: "message", data: "x" }]);
});

test("ignores comment lines and blocks without data", () => {
  const p = new SSEParser();
  expect(p.push(": keepalive\n\n")).toEqual([]);
  expect(p.push("event: done\n\n")).toEqual([]);
});

test("joins multi-line data with newlines", () => {
  const p = new SSEParser();
  const events = p.push("data: line1\ndata: line2\n\n");
  expect(events).toEqual([{ event: "message", data: "line1\nline2" }]);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -- src/lib/sse.test.ts`
Expected: FAIL — cannot resolve `./sse`.

- [ ] **Step 3: Implement**

Create `web/src/lib/sse.ts`:

```ts
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test -- src/lib/sse.test.ts`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/sse.ts web/src/lib/sse.test.ts
git commit -m "feat: add incremental SSE parser for chat streaming"
```

---

### Task 3: Streaming API client

**Files:**
- Create: `web/src/lib/api.ts`
- Test: `web/src/lib/api.test.ts`

**Interfaces:**
- Consumes: `SSEParser`, `SSEEvent` from `@/lib/sse` (Task 2).
- Produces (Task 4 depends on these exact names):
  - `interface Source { title: string; similarity: number }`
  - `interface ChatDone { session_id: string; reply: string; sources: Source[]; turns_remaining: number; tokens_used: number; cost_usd: number }`
  - `interface StreamHandlers { onToken(content: string): void; onDone(done: ChatDone): void; onError(message: string): void }`
  - `const API_URL: string` (from `NEXT_PUBLIC_API_URL`, default `http://localhost:8000`)
  - `async function streamChat(message: string, sessionId: string | null, handlers: StreamHandlers, apiUrl?: string): Promise<void>` — resolves after the terminal event/error; exactly one of `onDone`/`onError` fires per call (possibly after several `onToken`).

- [ ] **Step 1: Write the failing tests**

Create `web/src/lib/api.test.ts`:

```ts
import { streamChat, type ChatDone } from "./api";

const DONE_PAYLOAD: ChatDone = {
  session_id: "s-1",
  reply: "Hello there",
  sources: [{ title: "Pattern A", similarity: 0.9 }],
  turns_remaining: 7,
  tokens_used: 150,
  cost_usd: 0.000123,
};

function sseResponse(frames: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function collectHandlers() {
  const calls = { tokens: [] as string[], done: [] as ChatDone[], errors: [] as string[] };
  return {
    calls,
    handlers: {
      onToken: (c: string) => calls.tokens.push(c),
      onDone: (d: ChatDone) => calls.done.push(d),
      onError: (m: string) => calls.errors.push(m),
    },
  };
}

afterEach(() => vi.restoreAllMocks());

test("streams tokens then done, posting message and session_id", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    sseResponse([
      'event: token\ndata: {"content": "Hel"}\n\n',
      'event: token\ndata: {"content": "lo"}\n\n',
      `event: done\ndata: ${JSON.stringify(DONE_PAYLOAD)}\n\n`,
    ]),
  );
  const { calls, handlers } = collectHandlers();

  await streamChat("hi", "s-1", handlers, "http://api.test");

  expect(fetchMock).toHaveBeenCalledWith("http://api.test/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: "hi", session_id: "s-1" }),
  });
  expect(calls.tokens).toEqual(["Hel", "lo"]);
  expect(calls.done).toEqual([DONE_PAYLOAD]);
  expect(calls.errors).toEqual([]);
});

test("omits session_id from the body for a new session", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    sseResponse([`event: done\ndata: ${JSON.stringify(DONE_PAYLOAD)}\n\n`]),
  );
  const { handlers } = collectHandlers();

  await streamChat("hi", null, handlers, "http://api.test");

  const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
  expect(body).toEqual({ message: "hi" });
});

test("SSE error event calls onError with the detail, not onDone", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    sseResponse(['event: error\ndata: {"detail": "The advisor hit a snag"}\n\n']),
  );
  const { calls, handlers } = collectHandlers();

  await streamChat("hi", "s-1", handlers, "http://api.test");

  expect(calls.errors).toEqual(["The advisor hit a snag"]);
  expect(calls.done).toEqual([]);
});

test("HTTP 429 surfaces the JSON detail via onError", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ detail: "Session turn limit reached" }), { status: 429 }),
  );
  const { calls, handlers } = collectHandlers();

  await streamChat("hi", "s-1", handlers, "http://api.test");

  expect(calls.errors).toEqual(["Session turn limit reached"]);
});

test("network failure calls onError", async () => {
  vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fetch failed"));
  const { calls, handlers } = collectHandlers();

  await streamChat("hi", null, handlers, "http://api.test");

  expect(calls.errors).toHaveLength(1);
  expect(calls.done).toEqual([]);
});

test("stream ending without a done event is reported as an error", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    sseResponse(['event: token\ndata: {"content": "partial"}\n\n']),
  );
  const { calls, handlers } = collectHandlers();

  await streamChat("hi", "s-1", handlers, "http://api.test");

  expect(calls.tokens).toEqual(["partial"]);
  expect(calls.errors).toHaveLength(1);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -- src/lib/api.test.ts`
Expected: FAIL — cannot resolve `./api`.

- [ ] **Step 3: Implement**

Create `web/src/lib/api.ts`:

```ts
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
        if (ev.event === "token") {
          handlers.onToken((JSON.parse(ev.data) as { content: string }).content);
        } else if (ev.event === "done") {
          terminal = true;
          handlers.onDone(JSON.parse(ev.data) as ChatDone);
        } else if (ev.event === "error") {
          terminal = true;
          handlers.onError((JSON.parse(ev.data) as { detail: string }).detail);
          return;
        }
      }
    }
    if (!terminal) {
      handlers.onError("The reply was cut off — please retry.");
    }
  } catch {
    handlers.onError("Connection lost mid-reply — please retry.");
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test -- src/lib/api.test.ts`
Expected: 6 passed.

- [ ] **Step 5: Full web test suite + commit**

Run: `npm test` — expected: all pass (1 + 6 + 6 = 13).

```bash
git add web/src/lib/api.ts web/src/lib/api.test.ts
git commit -m "feat: add streaming chat API client over /chat/stream"
```

---

### Task 4: useChat hook

**Files:**
- Create: `web/src/hooks/useChat.ts`
- Test: `web/src/hooks/useChat.test.ts`

**Interfaces:**
- Consumes: `streamChat`, `ChatDone`, `Source`, `StreamHandlers` from `@/lib/api` (Task 3) — the tests mock `@/lib/api`.
- Produces (Task 5 depends on these exact names):
  - `const MAX_TURNS = 8`, `const MAX_INPUT_CHARS = 600`, `const SESSION_KEY = "automate-this-session-id"`
  - `interface ChatMessage { role: "user" | "assistant"; content: string; sources?: Source[]; tokensUsed?: number; costUsd?: number }`
  - `function useChat(): { messages: ChatMessage[]; streamingReply: string | null; isStreaming: boolean; turnsRemaining: number; error: string | null; send(text: string): Promise<void>; newConversation(): void }`

- [ ] **Step 1: Write the failing tests**

Create `web/src/hooks/useChat.test.ts`:

```ts
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ChatDone, StreamHandlers } from "@/lib/api";
import { MAX_TURNS, SESSION_KEY, useChat } from "./useChat";

vi.mock("@/lib/api", () => ({
  streamChat: vi.fn(),
}));
import { streamChat } from "@/lib/api";
const streamChatMock = vi.mocked(streamChat);

const DONE: ChatDone = {
  session_id: "s-42",
  reply: "Full reply",
  sources: [{ title: "Pattern A", similarity: 0.9 }],
  turns_remaining: 6,
  tokens_used: 200,
  cost_usd: 0.0002,
};

function resolveWith(
  fn: (handlers: StreamHandlers) => void,
): typeof streamChatMock {
  return streamChatMock.mockImplementation(async (_m, _s, handlers) => {
    fn(handlers);
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
});

test("send appends the user message, streams tokens, then finalises the assistant message", async () => {
  resolveWith((h) => {
    h.onToken("Fu");
    h.onToken("ll reply");
    h.onDone(DONE);
  });
  const { result } = renderHook(() => useChat());

  await act(() => result.current.send("automate my invoicing"));

  expect(result.current.messages).toEqual([
    { role: "user", content: "automate my invoicing" },
    {
      role: "assistant",
      content: "Full reply",
      sources: DONE.sources,
      tokensUsed: 200,
      costUsd: 0.0002,
    },
  ]);
  expect(result.current.streamingReply).toBeNull();
  expect(result.current.turnsRemaining).toBe(6);
});

test("done persists the session id and the next send reuses it", async () => {
  resolveWith((h) => h.onDone(DONE));
  const { result } = renderHook(() => useChat());

  await act(() => result.current.send("first"));
  expect(sessionStorage.getItem(SESSION_KEY)).toBe("s-42");

  await act(() => result.current.send("second"));
  expect(streamChatMock).toHaveBeenLastCalledWith(
    "second",
    "s-42",
    expect.anything(),
  );
});

test("first send passes null session id", async () => {
  resolveWith((h) => h.onDone(DONE));
  const { result } = renderHook(() => useChat());

  await act(() => result.current.send("first"));

  expect(streamChatMock).toHaveBeenCalledWith("first", null, expect.anything());
});

test("onError sets error, clears the streaming bubble, keeps the user message", async () => {
  resolveWith((h) => {
    h.onToken("par");
    h.onError("Session turn limit reached");
  });
  const { result } = renderHook(() => useChat());

  await act(() => result.current.send("hi"));

  expect(result.current.error).toBe("Session turn limit reached");
  expect(result.current.streamingReply).toBeNull();
  expect(result.current.messages).toEqual([{ role: "user", content: "hi" }]);
});

test("send is a no-op while already streaming or for blank input", async () => {
  let capturedHandlers: StreamHandlers | undefined;
  streamChatMock.mockImplementation(async (_m, _s, handlers) => {
    capturedHandlers = handlers; // never terminates -> stays streaming
  });
  const { result } = renderHook(() => useChat());

  await act(() => result.current.send("first"));
  expect(result.current.isStreaming).toBe(true);
  await act(() => result.current.send("second while busy"));
  expect(streamChatMock).toHaveBeenCalledTimes(1);

  act(() => capturedHandlers!.onDone(DONE));
  await waitFor(() => expect(result.current.isStreaming).toBe(false));

  await act(() => result.current.send("   "));
  expect(streamChatMock).toHaveBeenCalledTimes(1);
});

test("newConversation clears messages, error, session id, and resets turns", async () => {
  resolveWith((h) => h.onDone({ ...DONE, turns_remaining: 0 }));
  const { result } = renderHook(() => useChat());
  await act(() => result.current.send("hi"));
  expect(result.current.turnsRemaining).toBe(0);

  act(() => result.current.newConversation());

  expect(result.current.messages).toEqual([]);
  expect(result.current.error).toBeNull();
  expect(result.current.turnsRemaining).toBe(MAX_TURNS);
  expect(sessionStorage.getItem(SESSION_KEY)).toBeNull();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -- src/hooks/useChat.test.ts`
Expected: FAIL — cannot resolve `./useChat`.

- [ ] **Step 3: Implement**

Create `web/src/hooks/useChat.ts`:

```ts
"use client";

import { useCallback, useState } from "react";
import { type Source, streamChat } from "@/lib/api";

export const MAX_TURNS = 8;
export const MAX_INPUT_CHARS = 600;
export const SESSION_KEY = "automate-this-session-id";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  tokensUsed?: number;
  costUsd?: number;
}

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streamingReply, setStreamingReply] = useState<string | null>(null);
  const [turnsRemaining, setTurnsRemaining] = useState(MAX_TURNS);
  const [error, setError] = useState<string | null>(null);

  const isStreaming = streamingReply !== null;

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || isStreaming) return;

      setError(null);
      setMessages((prev) => [...prev, { role: "user", content: trimmed }]);
      setStreamingReply("");

      const sessionId = sessionStorage.getItem(SESSION_KEY);
      await streamChat(trimmed, sessionId, {
        onToken: (content) => setStreamingReply((prev) => (prev ?? "") + content),
        onDone: (done) => {
          sessionStorage.setItem(SESSION_KEY, done.session_id);
          setMessages((prev) => [
            ...prev,
            {
              role: "assistant",
              content: done.reply,
              sources: done.sources,
              tokensUsed: done.tokens_used,
              costUsd: done.cost_usd,
            },
          ]);
          setTurnsRemaining(done.turns_remaining);
          setStreamingReply(null);
        },
        onError: (message) => {
          setError(message);
          setStreamingReply(null);
        },
      });
    },
    [isStreaming],
  );

  const newConversation = useCallback(() => {
    sessionStorage.removeItem(SESSION_KEY);
    setMessages([]);
    setError(null);
    setTurnsRemaining(MAX_TURNS);
    setStreamingReply(null);
  }, []);

  return { messages, streamingReply, isStreaming, turnsRemaining, error, send, newConversation };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test -- src/hooks/useChat.test.ts`
Expected: 6 passed.

- [ ] **Step 5: Full web suite + commit**

Run: `npm test` — expected 19 passed.

```bash
git add web/src/hooks/useChat.ts web/src/hooks/useChat.test.ts
git commit -m "feat: add useChat hook (streaming state, session persistence, turn tracking)"
```

---

### Task 5: Chat UI components + page

**Files:**
- Create: `web/src/components/Message.tsx`
- Create: `web/src/components/Chat.tsx`
- Modify: `web/src/app/page.tsx`
- Test: `web/src/components/Chat.test.tsx`

**Interfaces:**
- Consumes: `useChat`, `ChatMessage`, `MAX_TURNS`, `MAX_INPUT_CHARS` from `@/hooks/useChat` (Task 4); tests mock `@/hooks/useChat`.
- Produces: `<Chat />` (default export, client component) rendered by the home page. Test ids used (and relied on by the verification pass): `chat-input`, `send-button`, `turn-counter`, `error-banner`, `streaming-bubble`, `new-conversation`.

- [ ] **Step 1: Write the failing tests**

Create `web/src/components/Chat.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ChatMessage } from "@/hooks/useChat";
import Chat from "./Chat";

const baseState = {
  messages: [] as ChatMessage[],
  streamingReply: null as string | null,
  isStreaming: false,
  turnsRemaining: 8,
  error: null as string | null,
  send: vi.fn(),
  newConversation: vi.fn(),
};

vi.mock("@/hooks/useChat", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/hooks/useChat")>()),
  useChat: () => mockState,
}));
let mockState = { ...baseState };

beforeEach(() => {
  mockState = { ...baseState, send: vi.fn(), newConversation: vi.fn() };
});

test("submits the typed message and clears the input", async () => {
  render(<Chat />);
  const input = screen.getByTestId("chat-input");

  await userEvent.type(input, "automate my invoicing");
  await userEvent.click(screen.getByTestId("send-button"));

  expect(mockState.send).toHaveBeenCalledWith("automate my invoicing");
  expect(input).toHaveValue("");
});

test("renders history, sources, and token/cost line", () => {
  mockState.messages = [
    { role: "user", content: "help" },
    {
      role: "assistant",
      content: "Use n8n.",
      sources: [{ title: "Pattern A", similarity: 0.87 }],
      tokensUsed: 150,
      costUsd: 0.000123,
    },
  ];
  render(<Chat />);

  expect(screen.getByText("help")).toBeInTheDocument();
  expect(screen.getByText("Use n8n.")).toBeInTheDocument();
  expect(screen.getByText(/Pattern A/)).toBeInTheDocument();
  expect(screen.getByText(/87%/)).toBeInTheDocument();
  expect(screen.getByText(/150 tokens/)).toBeInTheDocument();
  expect(screen.getByText(/\$0\.000123/)).toBeInTheDocument();
});

test("shows the live streaming bubble while a reply streams", () => {
  mockState.streamingReply = "Typing so f";
  mockState.isStreaming = true;
  render(<Chat />);

  expect(screen.getByTestId("streaming-bubble")).toHaveTextContent("Typing so f");
  expect(screen.getByTestId("send-button")).toBeDisabled();
});

test("turn counter reflects used turns", () => {
  mockState.turnsRemaining = 5;
  render(<Chat />);

  expect(screen.getByTestId("turn-counter")).toHaveTextContent(
    "3 of 8 free questions used",
  );
});

test("locks input and shows the CTA when the turn limit is reached", () => {
  mockState.turnsRemaining = 0;
  render(<Chat />);

  expect(screen.getByTestId("chat-input")).toBeDisabled();
  expect(screen.getByText(/book a free 30-min call/i)).toBeInTheDocument();
});

test("shows the error banner and New conversation resets", async () => {
  mockState.error = "Session turn limit reached";
  render(<Chat />);

  expect(screen.getByTestId("error-banner")).toHaveTextContent(
    "Session turn limit reached",
  );
  await userEvent.click(screen.getByTestId("new-conversation"));
  expect(mockState.newConversation).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -- src/components/Chat.test.tsx`
Expected: FAIL — cannot resolve `./Chat`.

- [ ] **Step 3: Implement Message component**

Create `web/src/components/Message.tsx`:

```tsx
import type { ChatMessage } from "@/hooks/useChat";

export default function Message({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={isUser ? "flex justify-end" : "flex justify-start"}>
      <div
        className={
          isUser
            ? "max-w-[80%] rounded-lg bg-blue-600 px-3 py-2 text-white"
            : "max-w-[80%] rounded-lg bg-gray-100 px-3 py-2 text-gray-900"
        }
      >
        <p className="whitespace-pre-wrap">{message.content}</p>
        {message.sources && message.sources.length > 0 && (
          <ul className="mt-2 border-t border-gray-300 pt-1 text-xs text-gray-600">
            {message.sources.map((s) => (
              <li key={s.title}>
                📚 {s.title} ({Math.round(s.similarity * 100)}%)
              </li>
            ))}
          </ul>
        )}
        {message.tokensUsed !== undefined && (
          <p className="mt-1 text-xs text-gray-500">
            {message.tokensUsed} tokens · ${message.costUsd?.toFixed(6)}
          </p>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Implement Chat component**

Create `web/src/components/Chat.tsx`:

```tsx
"use client";

import { type FormEvent, useState } from "react";
import { MAX_INPUT_CHARS, MAX_TURNS, useChat } from "@/hooks/useChat";
import Message from "./Message";

export default function Chat() {
  const {
    messages,
    streamingReply,
    isStreaming,
    turnsRemaining,
    error,
    send,
    newConversation,
  } = useChat();
  const [draft, setDraft] = useState("");

  const limitReached = turnsRemaining <= 0;
  const turnsUsed = MAX_TURNS - turnsRemaining;

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!draft.trim() || isStreaming || limitReached) return;
    void send(draft);
    setDraft("");
  }

  return (
    <section className="flex flex-1 flex-col gap-3">
      <div className="flex items-center justify-between text-sm text-gray-600">
        <span data-testid="turn-counter">
          {turnsUsed} of {MAX_TURNS} free questions used
        </span>
        <button
          type="button"
          data-testid="new-conversation"
          onClick={newConversation}
          className="rounded border px-2 py-1 hover:bg-gray-50"
        >
          New conversation
        </button>
      </div>

      <div className="flex flex-1 flex-col gap-2 overflow-y-auto rounded border p-3">
        {messages.length === 0 && !isStreaming && (
          <p className="text-sm text-gray-400">
            Describe a repetitive task in your business — e.g. “I spend 4 hours
            a week manually creating invoices.”
          </p>
        )}
        {messages.map((m, i) => (
          <Message key={i} message={m} />
        ))}
        {isStreaming && (
          <div className="flex justify-start">
            <div
              data-testid="streaming-bubble"
              className="max-w-[80%] rounded-lg bg-gray-100 px-3 py-2 text-gray-900"
            >
              <p className="whitespace-pre-wrap">{streamingReply || "…"}</p>
            </div>
          </div>
        )}
      </div>

      {error && (
        <p data-testid="error-banner" className="rounded bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}

      {limitReached ? (
        <p className="rounded bg-amber-50 px-3 py-2 text-sm text-amber-800">
          You’ve used all {MAX_TURNS} free questions. Want a full custom
          automation plan? Book a free 30-min call — booking link coming with
          the production site.
        </p>
      ) : (
        <form onSubmit={handleSubmit} className="flex gap-2">
          <input
            data-testid="chat-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            maxLength={MAX_INPUT_CHARS}
            disabled={isStreaming || limitReached}
            placeholder="What would you like to automate?"
            className="flex-1 rounded border px-3 py-2 disabled:bg-gray-100"
          />
          <button
            type="submit"
            data-testid="send-button"
            disabled={isStreaming || limitReached || !draft.trim()}
            className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50"
          >
            Send
          </button>
        </form>
      )}
    </section>
  );
}
```

- [ ] **Step 5: Wire into the page**

Replace `web/src/app/page.tsx`:

```tsx
import Chat from "@/components/Chat";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col gap-4 p-4">
      <header>
        <h1 className="text-2xl font-bold">
          Automate This — SMB Automation Advisor
        </h1>
        <p className="text-sm text-gray-500">
          Functional demo — production design pending.
        </p>
      </header>
      <Chat />
    </main>
  );
}
```

(The Task 1 smoke test still passes — the heading is unchanged.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `npm test`
Expected: 25 passed (1 + 6 + 6 + 6 + 6).

- [ ] **Step 7: Build + lint + commit**

```bash
npm run build
npm run lint
git add web/src
git commit -m "feat: add streaming chat UI (messages, sources, turn counter, limit CTA)"
```

---

### Task 6: Docs + handoff for live verification

**Files:**
- Modify: `README.md` (repo root)
- Create: `web/README.md` (replace scaffold boilerplate)

**Interfaces:** none — documentation task. Live browser verification is performed by the controller session afterwards (it has browser preview tooling); this task only prepares and documents.

- [ ] **Step 1: Replace `web/README.md`**

```markdown
# Automate This — Web Frontend

Functional Next.js chat UI for the FastAPI backend in `../backend`. Streams
replies from `POST /chat/stream` (SSE over fetch), keeps the anonymous session
id in `sessionStorage`, and shows sources, token/cost, and the 8-turn limit.

Production visual design is intentionally not applied yet — see the design
brief in `../docs/superpowers/specs/2026-06-05-automate-this-design.md`
(Section 13).

## Run

```bash
cp .env.local.example .env.local   # point NEXT_PUBLIC_API_URL at the backend
npm install
npm run dev                        # http://localhost:3000
```

The backend must be running (see repo root README — `uv run python
run_server.py` from `backend/`). Backend CORS allows localhost:3000 by
default.

## Test

```bash
npm test
```
```

- [ ] **Step 2: Update the root README**

- Infrastructure table: add row `| Next.js frontend | localhost | 3000 |`.
- "What It Does" item 5 (demo UI): note the Gradio demo is now superseded for interactive use by the Next.js app in `web/` (Gradio kept as a minimal harness).
- Project Structure tree: add `web/` with a one-line description.
- Roadmap / Known Gaps: replace the "Production frontend (Next.js) — deliberately deferred" bullet with the current truth: functional Next.js frontend built in `web/`; visual design pass (spec Section 13 brief) still pending.

- [ ] **Step 3: Commit**

```bash
git add README.md web/README.md
git commit -m "docs: document the Next.js frontend and supersede the Gradio demo"
```

- [ ] **Step 4: Handoff note for the controller (do not perform yourself)**

Live verification checklist the controller runs with browser tooling after this task: backend up via `run_server.py`; `npm run dev`; send a real message and watch tokens stream into the bubble; confirm sources + token/cost line render; confirm turn counter increments; confirm New conversation resets; confirm a >600-char paste is blocked client-side and the 429 detail renders in the error banner when the turn limit is hit.

---

## Self-review notes (done at plan time)

- **Spec coverage** (design spec Section 9 functional requirements + Sprint 3 UI requirement): chat with streaming → Tasks 2-5; turn counter → Tasks 4-5; session in sessionStorage → Task 4; sources/tool results shown → Task 5 (sources + token/cost; per-tool badges deferred — the `done` payload exposes `sources` but not `tools_called`, and adding it is a backend change this plan forbids; noted for the design/Phase-3 pass); post-limit CTA → Task 5 (copy only, Calendly link deferred to design pass per spec); mobile responsive → deferred to design pass (functional layout is single-column and usable, not tuned); ROI sidebar widget → deferred to design pass (spec Section 9 lists it, Section 11's "Session 6" functional scope does not).
- **Placeholder scan:** clean — every code step contains complete code.
- **Type consistency:** `ChatDone`/`Source`/`StreamHandlers` (Task 3) match the hook's usage (Task 4) and the component props (Task 5); `SESSION_KEY`, `MAX_TURNS`, `MAX_INPUT_CHARS` defined once in `useChat.ts` and imported elsewhere; test ids in Task 5's tests match the component code.
