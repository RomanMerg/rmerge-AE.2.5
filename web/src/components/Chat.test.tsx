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
