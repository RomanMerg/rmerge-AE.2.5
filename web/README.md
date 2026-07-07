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
