from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from langchain_core.messages import AIMessage, HumanMessage

from app.config import settings
from app.models import (
    AnswerResponse,
    ChatRequest,
    ChatResponse,
    ContentOptimizationResponse,
    CTAResponse,
    HumanizeResponse,
    KnowledgeResponse,
    SafetyResponse,
    SourceChunk,
    UnderstandResponse,
)
from app.nodes.content_optimization_node import build_content_optimization_graph
from app.nodes.cta_node import build_cta_graph
from app.nodes.empathy_node import build_empathy_graph
from app.nodes.knowledge_node import build_knowledge_graph
from app.nodes.response_node import build_response_graph
from app.nodes.safety_node import build_safety_graph
from app.nodes.understanding_node import build_understanding_graph
from app.rag.chain import build_chain

MAX_HISTORY_TURNS = 6

chat_chain = None
understanding_graph = None
knowledge_graph = None
cta_graph = None
response_graph = None
content_optimization_graph = None
empathy_graph = None
safety_graph = None
session_histories: dict[str, list] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global chat_chain, understanding_graph, knowledge_graph, cta_graph, response_graph, content_optimization_graph, empathy_graph, safety_graph
    settings.validate()
    chat_chain = build_chain()
    understanding_graph = build_understanding_graph()
    knowledge_graph = build_knowledge_graph()
    cta_graph = build_cta_graph()
    response_graph = build_response_graph()
    content_optimization_graph = build_content_optimization_graph()
    empathy_graph = build_empathy_graph()
    safety_graph = build_safety_graph()
    yield


app = FastAPI(title="Manasi - ManaScience RAG Chatbot", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    if chat_chain is None:
        raise HTTPException(status_code=503, detail="Chatbot is still starting up")

    history = session_histories.setdefault(request.session_id, [])

    result = chat_chain.invoke({"input": request.message, "chat_history": history})

    history.append(HumanMessage(content=request.message))
    history.append(AIMessage(content=result["answer"]))
    session_histories[request.session_id] = history[-(MAX_HISTORY_TURNS * 2) :]

    sources = [
        SourceChunk(source=doc.metadata.get("source", "unknown"), content=doc.page_content)
        for doc in result.get("context", [])
    ]
    return ChatResponse(answer=result["answer"], sources=sources)


@app.delete("/chat/{session_id}")
def reset_session(session_id: str):
    session_histories.pop(session_id, None)
    return {"status": "reset", "session_id": session_id}


def _history_to_chat_turns(history: list) -> list[dict]:
    """Convert LangChain HumanMessage/AIMessage objects into the Understanding Node's ChatTurn shape."""
    return [
        {"role": "user" if isinstance(msg, HumanMessage) else "assistant", "content": msg.content}
        for msg in history
    ]


@app.post("/understand", response_model=UnderstandResponse)
def understand(request: ChatRequest):
    if understanding_graph is None:
        raise HTTPException(status_code=503, detail="Understanding node is still starting up")

    history = session_histories.get(request.session_id, [])
    result = understanding_graph.invoke(
        {
            "user_message": request.message,
            "chat_history": _history_to_chat_turns(history),
            "understanding": None,
        }
    )
    return UnderstandResponse(**result["understanding"])


@app.post("/knowledge", response_model=KnowledgeResponse)
def knowledge(request: ChatRequest):
    if knowledge_graph is None:
        raise HTTPException(status_code=503, detail="Knowledge node is still starting up")

    history = session_histories.get(request.session_id, [])
    result = knowledge_graph.invoke(
        {
            "user_message": request.message,
            "chat_history": _history_to_chat_turns(history),
            "understanding": None,
            "knowledge": None,
        }
    )
    return KnowledgeResponse(**result["knowledge"])


@app.post("/cta", response_model=CTAResponse)
def cta_endpoint(request: ChatRequest):
    if cta_graph is None:
        raise HTTPException(status_code=503, detail="CTA node is still starting up")

    history = session_histories.get(request.session_id, [])
    result = cta_graph.invoke(
        {
            "user_message": request.message,
            "chat_history": _history_to_chat_turns(history),
            "understanding": None,
            "knowledge": None,
            "cta": None,
        }
    )
    return CTAResponse(**result["cta"])


@app.post("/respond", response_model=AnswerResponse)
def respond(request: ChatRequest):
    if response_graph is None:
        raise HTTPException(status_code=503, detail="Response node is still starting up")

    history = session_histories.get(request.session_id, [])
    result = response_graph.invoke(
        {
            "user_message": request.message,
            "chat_history": _history_to_chat_turns(history),
            "understanding": None,
            "knowledge": None,
            "response": None,
        }
    )
    return AnswerResponse(**result["response"])


@app.post("/optimize-content", response_model=ContentOptimizationResponse)
def optimize_content_endpoint(request: ChatRequest):
    if content_optimization_graph is None:
        raise HTTPException(status_code=503, detail="Content optimization node is still starting up")

    history = session_histories.get(request.session_id, [])
    result = content_optimization_graph.invoke(
        {
            "user_message": request.message,
            "chat_history": _history_to_chat_turns(history),
            "understanding": None,
            "knowledge": None,
            "response": None,
            "content_optimization": None,
        }
    )
    return ContentOptimizationResponse(**result["content_optimization"])


@app.post("/humanize", response_model=HumanizeResponse)
def humanize(request: ChatRequest):
    if empathy_graph is None:
        raise HTTPException(status_code=503, detail="Empathy node is still starting up")

    history = session_histories.get(request.session_id, [])
    result = empathy_graph.invoke(
        {
            "user_message": request.message,
            "chat_history": _history_to_chat_turns(history),
            "understanding": None,
            "knowledge": None,
            "response": None,
            "content_optimization": None,
            "empathy": None,
        }
    )
    return HumanizeResponse(**result["empathy"])


@app.post("/safety", response_model=SafetyResponse)
def safety(request: ChatRequest):
    if safety_graph is None:
        raise HTTPException(status_code=503, detail="Safety node is still starting up")

    history = session_histories.get(request.session_id, [])
    result = safety_graph.invoke(
        {
            "user_message": request.message,
            "chat_history": _history_to_chat_turns(history),
            "understanding": None,
            "knowledge": None,
            "response": None,
            "content_optimization": None,
            "empathy": None,
            "safety": None,
        }
    )
    return SafetyResponse(**result["safety"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
