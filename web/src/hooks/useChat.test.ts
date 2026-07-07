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
