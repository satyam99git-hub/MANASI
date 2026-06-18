from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="The user's question")
    session_id: str = Field(default="default", description="Conversation session identifier")


class SourceChunk(BaseModel):
    source: str
    content: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceChunk]


class UnderstandResponse(BaseModel):
    intent: str
    topic: str
    search_query: str
    emotional_state: str
