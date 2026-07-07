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
