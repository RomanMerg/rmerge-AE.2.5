"""
FastMCP server exposing the capture_lead tool.
Wraps Twenty CRM's REST API (/rest/...) to create a Person record and link a note to it.

Run with:
    uv run python -m mcp_server.server

Or as MCP stdio server (for Gradio / Claude Desktop):
    uv run fastmcp run mcp_server/server.py
"""
import httpx
from fastmcp import FastMCP

from app.config import get_settings

mcp = FastMCP("automate-this-lead-capture")


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
    settings = get_settings()
    headers = {
        "Authorization": f"Bearer {settings.twenty_api_key}",
        "Content-Type": "application/json",
    }
    first, *rest = name.strip().split(" ", 1)
    last = rest[0] if rest else ""

    payload = {
        "name": {"firstName": first, "lastName": last},
        "emails": {"primaryEmail": email},
        "jobTitle": "SMB Owner",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                f"{settings.twenty_base_url}/rest/people",
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
            # Best-effort: a failed note (or link) shouldn't erase a successful Person creation.
            note_resp = await client.post(
                f"{settings.twenty_base_url}/rest/notes",
                json={
                    "title": f"Automation pain point — {company}",
                    "bodyV2": {"markdown": pain_point},
                },
                headers=headers,
            )
            note_resp.raise_for_status()
            note_id = note_resp.json().get("data", {}).get("createNote", {}).get("id")

            if note_id and person_id != "unknown":
                await client.post(
                    f"{settings.twenty_base_url}/rest/noteTargets",
                    json={"noteId": note_id, "targetPersonId": person_id},
                    headers=headers,
                )
        except Exception:
            pass

        return {"status": "created", "person_id": person_id}


if __name__ == "__main__":
    mcp.run()
