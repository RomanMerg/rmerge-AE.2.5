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
