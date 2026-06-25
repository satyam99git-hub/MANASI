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


class RetrievedDocumentModel(BaseModel):
    chunk_id: str
    content: str
    content_type: str
    source_title: str
    source_url: str | None
    similarity_score: float
    metadata: dict


class KnowledgeResponse(BaseModel):
    source: str
    retrieved_docs: list[RetrievedDocumentModel]
    confidence: float
    query_used: str
    intent: str
    retrieval_skipped: bool
    content_types_searched: list[str]
    retrieval_time_ms: float
    error: str | None


class AnswerResponse(BaseModel):
    answer: str
    source: str
    answer_type: str
    topic: str
    intent: str
    confidence: float
    grounded_chunk_ids: list[str]
    generation_time_ms: float
    error: str | None


class HumanizeResponse(BaseModel):
    final_answer: str
    emotional_state: str
    source: str
    answer_type: str
    topic: str
    intent: str
    confidence: float
    grounded_chunk_ids: list[str]
    humanization_time_ms: float
    error: str | None


class SafetyResponse(BaseModel):
    safe_response: str
    safety_status: str
    violations_detected: list[str]
    escalation_level: str
    disclaimer_added: bool
    original_final_answer: str
    emotional_state: str
    source: str
    answer_type: str
    topic: str
    intent: str
    confidence: float
    grounded_chunk_ids: list[str]
    validation_time_ms: float
    error: str | None


class ContentOptimizationResponse(BaseModel):
    title: str | None
    summary: str
    description: str
    key_points: list[str]
    content_type: str
    source_type: str
    confidence_score: float
    source: str
    answer_type: str
    topic: str
    intent: str
    confidence: float
    grounded_chunk_ids: list[str]
    original_answer: str
    optimization_time_ms: float
    error: str | None
