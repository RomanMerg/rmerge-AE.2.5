# Design Spec: Token/Cost Tracking + Per-IP Rate Limiting

**Date:** 2026-07-02
**Status:** Approved — ready for implementation planning
**Context:** Closes the second Sprint 2 Medium-optional gap ("Calculate and display token usage and costs") and closes a real anti-abuse gap in `/chat`: `max_turns_per_session` only limits turns *within* a session, but `session_id` is client-supplied (or freely regenerated), so a script can bypass the entire per-session limit by starting a fresh session on every call.

---

## 1. Goals

1. Track and return real token usage and estimated USD cost per `/chat` turn.
2. Close the session-cycling bypass with a per-IP rate limit, independent of `session_id`.
3. Keep scope tight — no new cost-based cap (the existing turn limit × `max_output_tokens` already bounds worst-case per-session cost), no multi-provider pricing table, no in-memory state (this app's state is Postgres-backed everywhere else — `turn_count`, `history` — and this should match).

## 2. Non-Goals

- No prompt-injection / intent classifier guard (deliberately deferred to its own design round — different mechanism, different cost/latency tradeoffs).
- No session-level running cost/token total exposed in the API response (only per-turn) — nothing currently consumes a running total, so persisting one would be premature.
- No modification to `rag/ingest.py`'s `embed_text()` signature to expose real embedding token usage — embedding cost is estimated instead (see §4).

## 3. Per-IP Rate Limiting

**Storage:** new Postgres table, matching the existing pattern of persisting all rate-limit state in the DB (not in-memory, which would reset on restart and not work across multiple workers):

```sql
CREATE TABLE IF NOT EXISTS chat_requests (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ip_address TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chat_requests_ip_created_idx
    ON chat_requests (ip_address, created_at);
```

**Client IP resolution:** `X-Forwarded-For` header (first IP in the list) takes precedence, since Render sits behind a reverse proxy; falls back to the raw socket address (`request.client.host`).

**Check + record flow**, run first in `/chat` (before any DB conversation lookup or LLM call — cheapest reject-fast guard):
1. Count rows in `chat_requests` for this IP where `created_at > NOW() - INTERVAL '1 hour'`.
2. If count `>= max_requests_per_ip_per_hour` (new setting, default `30`), raise `429` with a message distinct from the turn-limit 429 (so client code / a human debugging can tell them apart).
3. Otherwise, insert a new row for this request, and opportunistically delete rows older than 24 hours (any IP) to bound table growth — cheap given the indexed `created_at` column, no separate cron job needed for a low-traffic app.

**Default threshold rationale:** 30/hour is roughly 3-4 full 8-turn sessions — generous for a human testing multiple scenarios, tight enough to block a script cycling `session_id`s to bypass the per-session limit.

## 4. Token/Cost Tracking

**Chat completion tokens — real, not estimated.** `langchain_openai.ChatOpenAI` populates `AIMessage.usage_metadata` (`{"input_tokens": int, "output_tokens": int, "total_tokens": int}`) from the actual OpenRouter response. `/chat` makes 1-2 `ainvoke()` calls per turn (2 if a tool was called); sum `usage_metadata` across whichever calls actually happened. Implementer must verify `usage_metadata` is actually populated (not `None`) via OpenRouter early, and fall back to a `0`/`None`-safe default if it's ever missing rather than raising.

**Embedding tokens — estimated, not real.** Only relevant when `search_automation_patterns` fires (it calls `embed_text()` internally). Rather than modifying `rag/ingest.py`'s `embed_text()` to expose real usage (touching an already-tested, already-reviewed module for a cost line that's 10-30x cheaper than chat completion — not worth the risk), estimate via `len(text) // 4` (same rough heuristic the reference `rmerge-AE.1.5` project uses).

**Pricing** — a small hardcoded table, only for the models this project actually uses (not a multi-provider table like the reference project):

```python
MODEL_PRICING = {
    # USD per 1M tokens. Verify against https://openrouter.ai/models periodically — these drift.
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "openai/text-embedding-3-small": {"input": 0.02, "output": 0.0},
}
```

Unknown models default to `{"input": 0.0, "output": 0.0}` (cost `0.0`) rather than raising — matches the reference project's `.get(model)` fallback behavior.

**Response shape.** `ChatResponse` gains two fields, matching what the *original* project design spec already anticipated but never implemented:

```python
class ChatResponse(BaseModel):
    session_id: str
    reply: str
    sources: list[dict]
    turns_remaining: int
    tokens_used: int      # NEW — this turn only
    cost_usd: float        # NEW — this turn only
```

## 5. Files Touched

| File | Change |
|---|---|
| `init.sql` | New `chat_requests` table + index |
| `backend/app/config.py` | New `max_requests_per_ip_per_hour: int = 30` |
| `backend/app/rate_limit.py` (new) | `get_client_ip(request)`, `check_ip_rate_limit(ip_address)` — raises `429` |
| `backend/app/cost_tracker.py` (new) | `MODEL_PRICING`, `calculate_cost()`, `estimate_embedding_tokens()` |
| `backend/app/main.py` | Inject `Request`, call IP check first, compute tokens/cost after LLM call(s), add fields to `ChatResponse` |
| `frontend/app.py` | Gradio footer shows tokens/cost per turn |
| `backend/tests/test_cost_tracker.py` (new) | Pure function tests |
| `backend/tests/test_rate_limit.py` (new) | Mocked-DB tests for IP check/record |
| `backend/tests/test_chat.py` | New response fields, 429-on-IP-limit test, `X-Forwarded-For` precedence test |
| `README.md` | Flip the token/cost coverage row to ✅ |

## 6. Error Handling

- IP rate limit exceeded: `429`, distinct detail message from the turn-limit `429`.
- `usage_metadata` missing/`None` from `ChatOpenAI`: fall back to `0` tokens / `$0.0` cost for that call rather than raising — cost tracking should never break the chat flow itself.
- Unknown model in `MODEL_PRICING`: `$0.0` cost, not an error.

## 7. Testing

- `cost_tracker.py`: pure function unit tests — known input/output pairs for `calculate_cost()` (both configured models + an unknown model → 0), `estimate_embedding_tokens()`.
- `rate_limit.py`: mocked `AsyncSessionLocal` tests — under threshold passes, at/over threshold raises `429`, `X-Forwarded-For` takes precedence over `request.client.host`.
- `test_chat.py`: `ChatResponse` includes `tokens_used`/`cost_usd` reflecting a mocked `usage_metadata`; a dedicated test drives the IP limit to `429` via a mocked count.
- No live smoke test strictly required for this feature (no new external service dependency), but a manual pass confirming the Gradio UI actually shows the new fields is worthwhile before closing out.
