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
