"""Dev-server launcher.

On Windows, uvicorn's default asyncio loop is ProactorEventLoop, which psycopg's
async pool (used by the LangGraph Postgres checkpointer in app.main's lifespan)
refuses to run under. This launcher pins the selector policy on win32 and drives
uvicorn programmatically; on Linux/macOS it behaves like plain uvicorn.

Usage: uv run python run_server.py [PORT]   (default 8000)
"""

import asyncio
import sys

import uvicorn


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    config = uvicorn.Config("app.main:app", host="127.0.0.1", port=port)
    server = uvicorn.Server(config)
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
