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


class GraphState(TypedDict):
    user_message: str
    chat_history: list[ChatTurn]
    understanding: Optional[Understanding]
