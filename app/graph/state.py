from typing import Literal, Optional, TypedDict


class ChatTurn(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class Understanding(TypedDict):
    intent: Literal[
        "concept_explanation",
        "therapy_information",
        "course_information",
        "research_information",
        "website_information",
        "personal_concern",
        "emotional_support",
        "general_chat",
    ]
    topic: str
    search_query: str
    emotional_state: Literal[
        "neutral", "curious", "confused", "worried", "overwhelmed", "frustrated"
    ]


class RetrievedDocument(TypedDict):
    chunk_id: str
    content: str
    content_type: Literal[
        "course",
        "blog",
        "research_article",
        "faq",
        "practitioner_info",
        "therapy_info",
        "website_content",
        "neuroplasticity_content",
        "pdf_document",
    ]
    source_title: str
    source_url: Optional[str]
    similarity_score: float
    metadata: dict


class Knowledge(TypedDict):
    source: Literal["rag", "llm"]
    retrieved_docs: list[RetrievedDocument]
    confidence: float
    query_used: str
    intent: str
    retrieval_skipped: bool
    content_types_searched: list[str]
    retrieval_time_ms: float
    error: Optional[str]


class GraphState(TypedDict):
    user_message: str
    chat_history: list[ChatTurn]
    understanding: Optional[Understanding]
    knowledge: Optional[Knowledge]
