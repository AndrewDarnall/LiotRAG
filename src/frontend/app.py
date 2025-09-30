import os
import uuid
import httpx
import chainlit as cl
from dotenv import load_dotenv

# -------------------------------
# LLM Server URL (FastAPI /chat endpoint)
# -------------------------------
load_dotenv()
LLM_API_URL = "https://liotrag-aca.braveforest-781d5f46.westus2.azurecontainerapps.io"
if not LLM_API_URL:
    raise ValueError("AZURE_CONTAINER_APP_ENDPOINT must be set in environment variables")
LLM_API_URL = LLM_API_URL.rstrip("/")

# -------------------------------
# Chainlit Handlers
# -------------------------------
@cl.on_chat_start
async def start_chat():
    """Initialize a new chat session."""
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{LLM_API_URL}/get_auth")
        response.raise_for_status()
        data = response.json()
        cl.user_session.set("access_token", data["access_token"])
    # Create a unique session id for Redis conversation tracking (required by backend ChatRequest)
    cl.user_session.set("session_id", str(uuid.uuid4()))
    await cl.Message("Ciao! Sono qui per rispondere alle tue domande sul DMI. Come posso aiutarti oggi?").send()


@cl.on_message
async def handle_message(message: cl.Message):

    user_input = message.content

    assistant_message = cl.Message(content="")
    await assistant_message.send()

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            session_id = cl.user_session.get("session_id")
            if not session_id: # regenerate if missing
                session_id = str(uuid.uuid4())
                cl.user_session.set("session_id", session_id)
            response = await client.post(
                url=f"{LLM_API_URL}/chat",
                json={
                    "user_prompt": user_input,
                    "session_id": session_id
                },
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {cl.user_session.get('access_token')}",
                }
            )
            if response.status_code != 200:
                assistant_text = f"⚠️ Errore Server: {response.status_code} - {response.text}"
            data = response.json()
            assistant_text = data.get("response_text", "Nessun contenuto disponibile!")

    except Exception as e:
        assistant_text = f"⚠️ Errore: {e}"

    # Send as a single Chainlit message
    assistant_message.content = assistant_text
    await assistant_message.update()