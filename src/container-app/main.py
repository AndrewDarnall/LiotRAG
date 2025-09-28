""" FastAPI + Azure AI Search Integration (Async RAG Orchestrator) """
import os
import json
import logging
from typing import List, Dict, Any

from fastapi import FastAPI, HTTPException
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
import redis.asyncio as redis
from jinja2 import Environment, FileSystemLoader, select_autoescape
from openai import AsyncAzureOpenAI

from models.models import ChatRequest, ChatResponse, SourceDocument

logger = logging.getLogger("liotrag")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")

app = FastAPI(title="Azure Container App + OpenAI + AI Search")

# -------------------------------
# Environment Variables & Key Vault
# -------------------------------
KEY_VAULT_URL = os.getenv("KEY_VAULT_URL")
REDIS_SECRET_NAME = os.getenv("AZURE_REDIS_CACHE_SECRET_NAME")
OPENAI_SECRET_NAME = os.getenv("AZURE_OPENAI_SECRET_NAME")
AI_SEARCH_SECRET_NAME = os.getenv("AZURE_AI_SEARCH_SECRET_NAME")
AI_SEARCH_URL = os.getenv("AZURE_AI_SEARCH_URL")
AI_SEARCH_INDEX_NAME = os.getenv("AZURE_AI_SEARCH_INDEX_NAME")

if not KEY_VAULT_URL or not REDIS_SECRET_NAME or not OPENAI_SECRET_NAME:
    raise ValueError("KEY_VAULT_URL, AZURE_REDIS_CACHE_SECRET_NAME, and AZURE_OPENAI_SECRET_NAME must be set.")

logger.info("Initializing credentials and secret client")

credential = DefaultAzureCredential()
secret_client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

# -------------------------------
# Redis setup
# -------------------------------
def parse_azure_redis_secret(raw: str) -> str:
    parts = {}
    for part in raw.split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            parts[key.strip()] = value.strip()
        else:
            parts["host_port"] = part.strip()
    host_port = parts.get("host_port")
    password = parts.get("password")
    if not host_port or not password:
        raise ValueError(f"Cannot parse Redis secret: {raw}")
    return f"rediss://:{password}@{host_port}"

raw_redis_secret = secret_client.get_secret(REDIS_SECRET_NAME).value
redis_conn_str = parse_azure_redis_secret(raw_redis_secret)
redis_client = redis.from_url(redis_conn_str, decode_responses=True)
REDIS_TTL_SECONDS = int(os.getenv("REDIS_SESSION_TTL", "3600"))  # default 1 hour
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "4"))
logger.info("Redis client configured with TTL=%s, max_history=%s", REDIS_TTL_SECONDS, MAX_HISTORY_TURNS)

# -------------------------------
# Azure OpenAI setup
# -------------------------------
openai_api_key = secret_client.get_secret(OPENAI_SECRET_NAME).value.strip()
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION")

openai_client = AsyncAzureOpenAI(
    api_key=openai_api_key,
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_version=AZURE_OPENAI_API_VERSION
)
AZURE_OPENAI_EMBED_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBED_DEPLOYMENT")  # if embeddings needed later
logger.info("Azure OpenAI async client initialized. Deployment=%s", os.getenv("AZURE_OPENAI_DEPLOYMENT"))

# -------------------------------
# Azure AI Search setup
# -------------------------------
ai_search_key = secret_client.get_secret(AI_SEARCH_SECRET_NAME).value
search_client = SearchClient(
    endpoint=AI_SEARCH_URL,
    index_name=AI_SEARCH_INDEX_NAME,
    credential=AzureKeyCredential(ai_search_key)
)
logger.info("Azure AI Search client ready for index '%s'", AI_SEARCH_INDEX_NAME)

# -------------------------------
# Jinja2 Environment for Prompts
# -------------------------------
prompts_path = os.path.join(os.path.dirname(__file__), "prompts")
jinja_env = Environment(
    loader=FileSystemLoader(prompts_path),
    autoescape=select_autoescape(disabled_extensions=(".j2",))
)
GEN_PROMPT_TEMPLATE = "gen_prompt.j2"
REWRITE_PROMPT_TEMPLATE = "rewrite_prompt.j2"

# -------------------------------
# Helper Functions
# -------------------------------
def _conv_redis_key(session_id: str) -> str:
    return f"{session_id}:conv"

async def load_history(session_id: str) -> List[Dict[str, str]]:
    key = _conv_redis_key(session_id)
    raw = await redis_client.get(key)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Corrupted history for session %s, resetting", session_id)
        return []

async def save_history(session_id: str, history: List[Dict[str, str]]):
    key = _conv_redis_key(session_id)
    await redis_client.setex(key, REDIS_TTL_SECONDS, json.dumps(history))

def trim_history(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    # Keep only last MAX_HISTORY_TURNS * 2 messages (user+assistant)
    max_messages = MAX_HISTORY_TURNS * 2
    return history[-max_messages:]

def format_history_for_prompt(history: List[Dict[str, str]]) -> str:
    lines = []
    for m in history:
        role = m.get("role")
        content = m.get("content")
        if role and content:
            lines.append(f"{role.upper()}: {content}")
    return "\n".join(lines)

async def query_rewrite_if_needed(original_question: str, history: List[Dict[str, str]]) -> str:
    if not history:
        return original_question  # first turn, no rewrite needed
    template = jinja_env.get_template(REWRITE_PROMPT_TEMPLATE)
    prompt = template.render(
        history=format_history_for_prompt(history),
        question=original_question
    )
    logger.debug("Rewrite prompt length=%d", len(prompt))
    completion = await openai_client.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        messages=[{"role": "system", "content": "Query Rewriting"}, {"role": "user", "content": prompt}],
        temperature=0.2,
        max_completion_tokens=64
    )
    rewritten = completion.choices[0].message.content.strip()
    logger.info("Query rewrite raw output: %s", rewritten)
    return rewritten

def search_documents(query: str, top_k: int = 5) -> List[SourceDocument]:
    try:
        logger.info("Searching AI Search with query='%s' top=%d", query, top_k)
        results = search_client.search(query, top=top_k)
        docs: List[SourceDocument] = []
        for doc in results:
            snippet = doc.get("chunk") or doc.get("content") or ""
            docs.append(SourceDocument(
                title=doc.get("title"),
                url=doc.get("url"),
                snippet=snippet
            ))
        return docs
    except Exception as e:
        logger.exception("Search failure: %s", e)
        return []

async def generate_answer(history: List[Dict[str, str]], user_question: str, rewritten_query: str, docs: List[SourceDocument]) -> str:
    template = jinja_env.get_template(GEN_PROMPT_TEMPLATE)
    context_block = "\n\n".join([f"[DOC {i+1}]\nTitle: {d.title}\nURL: {d.url}\nSnippet: {d.snippet}" for i, d in enumerate(docs)])
    answer_prompt = template.render(
        history=format_history_for_prompt(history),
        context=context_block,
        question=user_question,
        rewritten_query=rewritten_query
    )
    logger.debug("Generation prompt size=%d chars", len(answer_prompt))
    completion = await openai_client.chat.completions.create(
        model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        messages=[{"role": "system", "content": "You are a university RAG assistant."}, {"role": "user", "content": answer_prompt}],
        temperature=0.3,
        max_completion_tokens=500
    )
    return completion.choices[0].message.content.strip()

# -------------------------------
# FastAPI endpoints
# -------------------------------
@app.get("/")
def root():
    return {"message": "FastAPI is running!"}

@app.post("/chat", response_model=ChatResponse)
async def chat_with_openai(request: ChatRequest):
    """Primary chat endpoint implementing naive+iterative RAG with query rewriting and Redis memory."""
    session_id = request.session_id
    user_prompt = request.user_prompt.strip()
    if not user_prompt:
        raise HTTPException(status_code=400, detail="Empty prompt")

    try:
        history = await load_history(session_id)
        logger.info("Session %s history length=%d", session_id, len(history))

        # Query rewriting + OOD check (only if history exists)
        has_prior = len(history) > 0
        rewritten_query = await query_rewrite_if_needed(user_prompt, history)
        if has_prior and rewritten_query.upper() == "FUORI_DOMINIO":
            logger.info("Out-of-domain detected for session %s", session_id)
            # store user message + short rejection response
            answer_text = "Scusa, non posso aiutarti con questa domanda." 
            history.extend([
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": answer_text}
            ])
            history = trim_history(history)
            await save_history(session_id, history)
            return ChatResponse(
                response_text=answer_text,
                rewritten_query=None,
                sources=[]
            )
        search_query = rewritten_query if has_prior else user_prompt

        # Retrieve context docs
        docs = search_documents(search_query, top_k=5)

        # Build answer
        answer_text = await generate_answer(
            history, 
            user_prompt, 
            rewritten_query if has_prior else None, 
            docs
        )

        # Update history (append user + assistant)
        history.extend([
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": answer_text}
        ])
        history = trim_history(history)
        await save_history(session_id, history)
        logger.info("Updated history stored for session %s (messages=%d)", session_id, len(history))

        return ChatResponse(
            response_text=answer_text,
            rewritten_query=rewritten_query if has_prior else None,
            sources=docs
        )
    except Exception as e:
        logger.exception("Error in /chat: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
