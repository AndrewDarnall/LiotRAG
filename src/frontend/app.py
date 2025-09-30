""" Minimal Chainlit Frontend: Streaming LLM Only """
import uuid
import os
import json

import chainlit as cl
import httpx

# -------------------------------
# LLM Server URL (FastAPI /chat endpoint)
# -------------------------------
LLM_API_URL = os.getenv("AZURE_CONTAINER_APP_ENDPOINT")
if not LLM_API_URL:
    raise ValueError("AZURE_CONTAINER_APP_ENDPOINT must be set in environment variables")

# -------------------------------
# Chainlit Handlers
# -------------------------------
@cl.on_chat_start
async def start_chat():
    """Initialize a new chat session."""
    session_id = str(uuid.uuid4())
    cl.user_session.set("session_id", session_id)
    cl.user_session.set("message_history", [])


@cl.on_message
async def handle_message(message: cl.Message):
    user_input = message.content
    message_history = cl.user_session.get("message_history", [])

    # Append user message to history
    message_history.append({"role": "user", "content": user_input})
    cl.user_session.set("message_history", message_history)

    assistant_text = ""

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(
                os.getenv("AZURE_CONTAINER_APP_ENDPOINT"),
                json={"user_prompt": user_input},
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()
            assistant_text = data.get("response_text", "")

    except Exception as e:
        assistant_text = f"⚠️ LLM server error: {e}"

    # Append assistant response to history
    message_history.append({"role": "assistant", "content": assistant_text})
    cl.user_session.set("message_history", message_history)

    # Send as a single Chainlit message
    await cl.Message(content=assistant_text).send()

