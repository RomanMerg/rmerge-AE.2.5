"""
Gradio chat frontend for Automate This.
Run with: uv run python frontend/app.py
"""
import uuid
import httpx
import gradio as gr

API_URL = "http://localhost:8000/chat"
session_id = str(uuid.uuid4())


def chat(message: str, history: list) -> tuple[str, list]:
    global session_id
    try:
        resp = httpx.post(
            API_URL,
            json={"session_id": session_id, "message": message},
            timeout=30.0,
        )
        data = resp.json()
        if resp.status_code == 429:
            detail = data.get("detail", "Rate limit reached")
            return f"{detail} Please try again later or start a new conversation.", history
        if resp.status_code != 200:
            return f"Error {resp.status_code}: {data.get('detail', 'Unknown error')}", history

        reply = data["reply"]
        turns_left = data.get("turns_remaining", "?")
        sources = data.get("sources", [])
        tokens_used = data.get("tokens_used", 0)
        cost_usd = data.get("cost_usd", 0.0)

        if sources:
            citations = "\n\n**Sources used:**\n" + "\n".join(
                f"- {s['title']} (similarity: {s['similarity']:.2f})" for s in sources
            )
            reply += citations

        reply += f"\n\n*{turns_left} turns remaining · {tokens_used} tokens · ${cost_usd:.6f} this turn.*"
        history.append((message, reply))
        return "", history
    except Exception as e:
        return f"Connection error: {e}", history


def new_conversation():
    global session_id
    session_id = str(uuid.uuid4())
    return []


with gr.Blocks(title="Automate This — SMB Advisor") as demo:
    gr.Markdown("# Automate This\nDescribe a repetitive task your business does manually. I'll tell you how to automate it.")
    chatbot = gr.Chatbot(height=500)
    msg = gr.Textbox(placeholder="e.g. I spend 3 hours a week chasing unpaid invoices by email...", label="Your message")
    clear = gr.Button("New conversation")

    msg.submit(chat, [msg, chatbot], [msg, chatbot])
    clear.click(new_conversation, outputs=[chatbot])

if __name__ == "__main__":
    demo.launch(server_port=7860)
