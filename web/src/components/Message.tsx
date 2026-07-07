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
