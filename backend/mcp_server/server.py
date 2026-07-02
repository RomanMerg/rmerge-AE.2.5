"""
FastMCP server exposing the capture_lead tool.
Wraps Twenty CRM REST API to create a Person record.

Run with:
    uv run python -m mcp_server.server

Or as MCP stdio server (for Gradio / Claude Desktop):
    uv run fastmcp run mcp_server/server.py
"""
import os
import httpx
from fastmcp import FastMCP

mcp = FastMCP("automate-this-lead-capture")

TWENTY_BASE_URL = os.getenv("TWENTY_BASE_URL", "http://localhost:3000")
TWENTY_API_KEY = os.getenv("TWENTY_API_KEY", "")


@mcp.tool()
async def capture_lead(
    name: str,
    email: str,
    company: str,
    pain_point: str,
) -> dict:
    """
    Save a prospective lead into Twenty CRM.

    Args:
        name: Full name of the contact (e.g. "Jane Smith")
        email: Business email address
        company: Company or trading name
        pain_point: The automation problem they described in the chat

    Returns:
        {"status": "created", "person_id": "<uuid>"} on success
        {"status": "error", "detail": "<msg>"} on failure
    """
    headers = {
        "Authorization": f"Bearer {TWENTY_API_KEY}",
        "Content-Type": "application/json",
    }
    first, *rest = name.strip().split(" ", 1)
    last = rest[0] if rest else ""

    payload = {
        "name": {"firstName": first, "lastName": last},
        "emails": {"primaryEmail": email},
        "company": {"name": company},
        "jobTitle": "SMB Owner",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                f"{TWENTY_BASE_URL}/api/object/people",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            person_id = data.get("data", {}).get("createPerson", {}).get("id", "unknown")
        except httpx.HTTPStatusError as e:
            return {"status": "error", "detail": str(e)}
        except Exception as e:
            return {"status": "error", "detail": str(e)}

        try:
            # Best-effort: a failed note shouldn't erase a successful Person creation.
            await client.post(
                f"{TWENTY_BASE_URL}/api/object/notes",
                json={
                    "title": f"Automation pain point — {company}",
                    "body": pain_point,
                    "noteTargets": [{"personId": person_id}] if person_id != "unknown" else [],
                },
                headers=headers,
            )
        except Exception:
            pass

        return {"status": "created", "person_id": person_id}


if __name__ == "__main__":
    mcp.run()
