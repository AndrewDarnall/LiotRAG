""" FastAPI + Azure AI Search Integration """
from fastapi import FastAPI, HTTPException
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
import redis
import os
from pydantic import BaseModel
from openai import AzureOpenAI

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

# -------------------------------
# Azure OpenAI setup
# -------------------------------
openai_api_key = secret_client.get_secret(OPENAI_SECRET_NAME).value.strip()
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION")

openai_client = AzureOpenAI(
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

# -------------------------------
# FastAPI models
# -------------------------------
class ChatRequest(BaseModel):
    user_prompt: str

class ChatResponse(BaseModel):
    response_text: str

class SearchRequest(BaseModel):
    query: str
    top: int = 3  # number of top results to return

class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str

# -------------------------------
# FastAPI endpoints
# -------------------------------
@app.get("/")
def root():
    return {"message": "FastAPI is running!"}

@app.post("/chat", response_model=ChatResponse)
def chat_with_openai(request: ChatRequest):
    try:
        response = openai_client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": request.user_prompt}
            ],
            max_completion_tokens=500,
            temperature=0.7,
            model=AZURE_OPENAI_DEPLOYMENT
        )
        text_output = response.choices[0].message.content
        return ChatResponse(response_text=text_output)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/search", response_model=list[SearchResult])
def search_azure_ai(request: SearchRequest):
    """Query Azure AI Search index for a given keyword."""
    try:
        results = search_client.search(request.query, top=request.top)
        response_list = []
        for doc in results:
            snippet = doc["chunk"]
            response_list.append(SearchResult(
                title=doc["title"],
                url=doc["url"],
                snippet=snippet
            ))
        return response_list
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/redis/write/{key}")
def redis_write(key: str, value: str):
    try:
        redis_client.set(key, value)
        return {"status": "written", "key": key, "value": value}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/redis/read/{key}")
def redis_read(key: str):
    try:
        value = redis_client.get(key)
        if value is None:
            raise HTTPException(status_code=404, detail="Key not found")
        return {"key": key, "value": value}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))