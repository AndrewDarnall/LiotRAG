""" FastAPI + Azure AI Search Integration (Async RAG Orchestrator) """
import os
import json
import logging
from typing import List, Dict, Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.search.documents.aio import SearchClient
from azure.core.credentials import AzureKeyCredential
logging.getLogger("azure").setLevel(logging.WARNING)

import jwt
from jwt.algorithms import RSAAlgorithm
import redis.asyncio as redis

from jinja2 import Environment, FileSystemLoader, select_autoescape

from openai import AsyncAzureOpenAI

from dotenv import load_dotenv

import httpx
from models.models import ChatRequest, ChatResponse, SourceDocument
import logging

logger = logging.getLogger("liotrag")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")

app = FastAPI(title="Azure Container App + OpenAI + AI Search")

# -------------------------------
# Environment Variables & Key Vault
# -------------------------------
load_dotenv()

KEY_VAULT_URL = os.getenv("KEY_VAULT_URL")

REDIS_SECRET_NAME = os.getenv("AZURE_REDIS_CACHE_SECRET_NAME")

OPENAI_SECRET_NAME = os.getenv("AZURE_OPENAI_SECRET_NAME")

AI_SEARCH_SECRET_NAME = os.getenv("AZURE_AI_SEARCH_SECRET_NAME")
AI_SEARCH_URL = os.getenv("AZURE_AI_SEARCH_URL")
AI_SEARCH_INDEX_NAME = os.getenv("AZURE_AI_SEARCH_INDEX_NAME")

CLIENT_ID = os.getenv("AZURE_ENTRAID_CLIENT_ID")
TENANT_ID = os.getenv("AZURE_TENANT_ID")
CLIENT_SECRET_NAME = os.getenv("AZURE_CLIENT_SECRET_NAME")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
TOKEN_ENDPOINT = f"{AUTHORITY}/oauth2/v2.0/token"
JWKS_URI = f"{AUTHORITY}/discovery/v2.0/keys"

AUDIENCE = CLIENT_ID  # primary audience (we'll also accept api://<client_id>)
ISSUER = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"  # v2 issuer form
# Some tokens (esp. acquired via legacy /oauth2/token or conditional access flows) may use the v1 issuer form (sts.windows.net)
ALLOWED_ISSUERS = {
    ISSUER,
    f"https://sts.windows.net/{TENANT_ID}/",  # v1 issuer format ends with a trailing slash
}
ACCEPTED_AUDIENCES = {CLIENT_ID, f"api://{CLIENT_ID}"}

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
# Authentication Flow
# -------------------------------
CLIENT_SECRET = secret_client.get_secret(CLIENT_SECRET_NAME).value
security = HTTPBearer()

async def get_signing_key(token: str, jwks_uri: str):
    """Find the correct signing key from the JWKS"""
    # Get header to find which kid was used
    header = jwt.get_unverified_header(token)
    kid = header["kid"]

    async with httpx.AsyncClient() as client:
        resp = await client.get(jwks_uri)
        if resp.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to fetch JWKS")   
        jwks = resp.json()

    for key in jwks["keys"]:
        if key["kid"] == kid:
            return key
    raise HTTPException(status_code=401, detail="No matching JWK found.")


async def verify_jwt(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Validate bearer token against Entra ID JWKS.

    Converts the matched JWK into an RSA public key (PyJWT expects a key object / PEM),
    then decodes and validates issuer + (primary) audience. If your token uses the
    App ID URI form (api://<client_id>) you may wish to extend audience handling.
    """
    token = credentials.credentials
    key_dict = await get_signing_key(token, JWKS_URI)

    try:
        # Convert JWK (dict) -> RSA public key instance accepted by PyJWT
        public_key = RSAAlgorithm.from_jwk(json.dumps(key_dict))

        # Decode without enforcing iss/aud so we can allow multiple acceptable values
        decoded = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={
                "verify_aud": False,  # we'll check manually
                "verify_iss": False,  # we'll check manually
            },
        )

        token_iss = decoded.get("iss")
        if token_iss not in ALLOWED_ISSUERS:
            raise HTTPException(status_code=401, detail=f"Invalid issuer: {token_iss}")

        token_aud = decoded.get("aud")
        # aud can be a string or list (AAD usually string)
        if isinstance(token_aud, str):
            aud_values = {token_aud}
        else:
            aud_values = set(token_aud or [])
        if aud_values.isdisjoint(ACCEPTED_AUDIENCES):
            raise HTTPException(status_code=401, detail=f"Invalid audience: {token_aud}")

        return decoded
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")

@app.get("/get_auth")
async def get_auth():
    """Request an access token from Entra ID using client credentials flow"""
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": f"api://{CLIENT_ID}/.default",
        "grant_type": "client_credentials",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(TOKEN_ENDPOINT, data=data)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()  # contains access_token, expires_in, etc.

@app.get("/test_auth")
async def test_auth(decoded: dict = Depends(verify_jwt)):
    """Test authentication. Dependency already validated JWT and returns decoded claims."""
    return {"message": "Authenticated", "user": decoded}

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

async def search_documents(query: str, top_k: int = 5) -> List[SourceDocument]:
    try:
        logger.info("Searching AI Search with query='%s' top=%d", query, top_k)
        async with search_client:
            results = await search_client.search(query, top=top_k)
            docs: List[SourceDocument] = []
            async for doc in results:
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
@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.post("/chat", response_model=ChatResponse)
async def chat_with_openai(request: ChatRequest, _: None = Depends(verify_jwt)):
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
            )
        search_query = rewritten_query if has_prior else user_prompt

        # Retrieve context docs
        docs = await search_documents(search_query, top_k=5)

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
        )
    except Exception as e:
        logger.exception("Error in /chat: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
