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

      {limitReached && (
        <p className="rounded bg-amber-50 px-3 py-2 text-sm text-amber-800">
          You’ve used all {MAX_TURNS} free questions. Want a full custom
          automation plan? Book a free 30-min call — booking link coming with
          the production site.
        </p>
      )}
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
    </section>
  );
}
