# from contextlib import asynccontextmanager

# from fastapi import FastAPI, HTTPException
# from langchain_core.messages import AIMessage, HumanMessage

# from app.config import settings
# from app.models import (
#     AnswerResponse,
#     ChatRequest,
#     ChatResponse,
#     CTAResponse,
#     HumanizeResponse,
#     KnowledgeResponse,
#     SafetyResponse,
#     SourceChunk,
#     UnderstandResponse,
# )
# from app.nodes.cta_node import build_cta_graph, cta_node
# from app.nodes.empathy_node import build_empathy_graph
# from app.nodes.knowledge_node import build_knowledge_graph
# from app.nodes.response_node import build_response_graph
# from app.nodes.safety_node import build_safety_graph
# from app.nodes.understanding_node import build_understanding_graph
# from app.rag.chain import build_chain

# MAX_HISTORY_TURNS = 6

# chat_chain = None
# understanding_graph = None
# knowledge_graph = None
# response_graph = None
# empathy_graph = None
# safety_graph = None
# cta_graph = None
# session_histories: dict[str, list] = {}


# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     global chat_chain, understanding_graph, knowledge_graph, response_graph, empathy_graph, safety_graph, cta_graph
#     settings.validate()
#     chat_chain = build_chain()
#     understanding_graph = build_understanding_graph()
#     knowledge_graph = build_knowledge_graph()
#     response_graph = build_response_graph()
#     empathy_graph = build_empathy_graph()
#     safety_graph = build_safety_graph()
#     cta_graph = build_cta_graph()
#     yield


# app = FastAPI(title="Manasi - ManaScience RAG Chatbot", lifespan=lifespan)


# @app.get("/health")
# def health():
#     return {"status": "ok"}


# @app.post("/chat", response_model=ChatResponse)
# def chat(request: ChatRequest):
#     if chat_chain is None:
#         raise HTTPException(status_code=503, detail="Chatbot is still starting up")

#     history = session_histories.setdefault(request.session_id, [])

#     result = chat_chain.invoke({"input": request.message, "chat_history": history})

#     history.append(HumanMessage(content=request.message))
#     history.append(AIMessage(content=result["answer"]))
#     session_histories[request.session_id] = history[-(MAX_HISTORY_TURNS * 2) :]

#     sources = [
#         SourceChunk(source=doc.metadata.get("source", "unknown"), content=doc.page_content)
#         for doc in result.get("context", [])
#     ]
#     cta_result = cta_node(
#         {"user_message": request.message, "understanding": None, "safety": {"safe_response": result["answer"]}}
#     )["cta"]
#     return ChatResponse(answer=result["answer"], sources=sources, cta=CTAResponse(**cta_result))


# @app.delete("/chat/{session_id}")
# def reset_session(session_id: str):
#     session_histories.pop(session_id, None)
#     return {"status": "reset", "session_id": session_id}


# def _history_to_chat_turns(history: list) -> list[dict]:
#     """Convert LangChain HumanMessage/AIMessage objects into the Understanding Node's ChatTurn shape."""
#     return [
#         {"role": "user" if isinstance(msg, HumanMessage) else "assistant", "content": msg.content}
#         for msg in history
#     ]


# @app.post("/understand", response_model=UnderstandResponse)
# def understand(request: ChatRequest):
#     if understanding_graph is None:
#         raise HTTPException(status_code=503, detail="Understanding node is still starting up")

#     history = session_histories.get(request.session_id, [])
#     result = understanding_graph.invoke(
#         {
#             "user_message": request.message,
#             "chat_history": _history_to_chat_turns(history),
#             "understanding": None,
#         }
#     )
#     return UnderstandResponse(**result["understanding"])


# @app.post("/knowledge", response_model=KnowledgeResponse)
# def knowledge(request: ChatRequest):
#     if knowledge_graph is None:
#         raise HTTPException(status_code=503, detail="Knowledge node is still starting up")

#     history = session_histories.get(request.session_id, [])
#     result = knowledge_graph.invoke(
#         {
#             "user_message": request.message,
#             "chat_history": _history_to_chat_turns(history),
#             "understanding": None,
#             "knowledge": None,
#         }
#     )
#     return KnowledgeResponse(**result["knowledge"])


# @app.post("/respond", response_model=AnswerResponse)
# def respond(request: ChatRequest):
#     if response_graph is None:
#         raise HTTPException(status_code=503, detail="Response node is still starting up")

#     history = session_histories.get(request.session_id, [])
#     result = response_graph.invoke(
#         {
#             "user_message": request.message,
#             "chat_history": _history_to_chat_turns(history),
#             "understanding": None,
#             "knowledge": None,
#             "response": None,
#         }
#     )
#     return AnswerResponse(**result["response"])


# @app.post("/humanize", response_model=HumanizeResponse)
# def humanize(request: ChatRequest):
#     if empathy_graph is None:
#         raise HTTPException(status_code=503, detail="Empathy node is still starting up")

#     history = session_histories.get(request.session_id, [])
#     result = empathy_graph.invoke(
#         {
#             "user_message": request.message,
#             "chat_history": _history_to_chat_turns(history),
#             "understanding": None,
#             "knowledge": None,
#             "response": None,
#             "empathy": None,
#         }
#     )
#     return HumanizeResponse(**result["empathy"])


# @app.post("/safety", response_model=SafetyResponse)
# def safety(request: ChatRequest):
#     if safety_graph is None:
#         raise HTTPException(status_code=503, detail="Safety node is still starting up")

#     history = session_histories.get(request.session_id, [])
#     result = safety_graph.invoke(
#         {
#             "user_message": request.message,
#             "chat_history": _history_to_chat_turns(history),
#             "understanding": None,
#             "knowledge": None,
#             "response": None,
#             "empathy": None,
#             "safety": None,
#         }
#     )
#     return SafetyResponse(**result["safety"])


# @app.post("/cta", response_model=CTAResponse)
# def cta(request: ChatRequest):
#     if cta_graph is None:
#         raise HTTPException(status_code=503, detail="CTA node is still starting up")

#     history = session_histories.get(request.session_id, [])
#     result = cta_graph.invoke(
#         {
#             "user_message": request.message,
#             "chat_history": _history_to_chat_turns(history),
#             "understanding": None,
#             "knowledge": None,
#             "response": None,
#             "empathy": None,
#             "safety": None,
#             "cta": None,
#         }
#     )
#     return CTAResponse(**result["cta"])


# if __name__ == "__main__":
#     import uvicorn

#     uvicorn.run(app, host="0.0.0.0", port=8000)
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
# 1. ADD THIS IMPORT HERE 🌐
from fastapi.middleware.cors import CORSMiddleware 
from langchain_core.messages import AIMessage, HumanMessage

from app.config import settings
from app.models import (
    AnswerResponse,
    ChatRequest,
    ChatResponse,
    CTAResponse,
    HumanizeResponse,
    KnowledgeResponse,
    SafetyResponse,
    SourceChunk,
    UnderstandResponse,
)
from app.nodes.cta_node import build_cta_graph, cta_node
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
response_graph = None
empathy_graph = None
safety_graph = None
cta_graph = None
session_histories: dict[str, list] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global chat_chain, understanding_graph, knowledge_graph, response_graph, empathy_graph, safety_graph, cta_graph
    settings.validate()
    chat_chain = build_chain()
    understanding_graph = build_understanding_graph()
    knowledge_graph = build_knowledge_graph()
    response_graph = build_response_graph()
    empathy_graph = build_empathy_graph()
    safety_graph = build_safety_graph()
    cta_graph = build_cta_graph()
    yield


app = FastAPI(title="Manasi - ManaScience RAG Chatbot", lifespan=lifespan)


origins = [
    "http://localhost:5173", 
    "http://127.0.0.1:5173",
    "http://192.168.29.34:5173",
    "http://manascience.in",
    "https://manascience.webflow.io"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    cta_result = cta_node(
        {"user_message": request.message, "understanding": None, "safety": {"safe_response": result["answer"]}}
    )["cta"]
    return ChatResponse(answer=result["answer"], sources=sources, cta=CTAResponse(**cta_result))


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
            "empathy": None,
            "safety": None,
        }
    )
    return SafetyResponse(**result["safety"])


@app.post("/cta", response_model=CTAResponse)
def cta(request: ChatRequest):
    if cta_graph is None:
        raise HTTPException(status_code=503, detail="CTA node is still starting up")

    history = session_histories.get(request.session_id, [])
    result = cta_graph.invoke(
        {
            "user_message": request.message,
            "chat_history": _history_to_chat_turns(history),
            "understanding": None,
            "knowledge": None,
            "response": None,
            "empathy": None,
            "safety": None,
            "cta": None,
        }
    )
    return CTAResponse(**result["cta"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
