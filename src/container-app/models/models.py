from pydantic import BaseModel, Field
from typing import List, Optional

# -------------------------------
# FastAPI models
# -------------------------------

class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Session identifier coming from the frontend (used as Redis key prefix)")
    user_prompt: str

class SourceDocument(BaseModel):
    title: Optional[str] = None
    url: Optional[str] = None
    snippet: Optional[str] = None

class ChatResponse(BaseModel):
    response_text: str
    #sources: List[SourceDocument] = Field(default_factory=list)

class SearchRequest(BaseModel):
    query: str
    top: int = 3  # number of top results to return

class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str