"""POST /chat - the member-facing endpoint.

Identity: member_id and the thread namespace come from the verified JWT.
The client only chooses a session_id, and it is scoped under the member's
own id, so one member can never read or resume another's conversation.
"""

import re
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi import Depends
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.auth import Principal
from app.rate_limit import enforce_rate_limit

router = APIRouter(tags=["chat"])

_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    awaiting_confirmation: bool = False


def _pending_question(state) -> str | None:
    for task in state.tasks:
        for it in task.interrupts:
            value = it.value
            if isinstance(value, dict):
                return value.get("question")
    return None


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request, principal: Principal = Depends(enforce_rate_limit)):
    session_id = body.session_id or uuid.uuid4().hex
    if not _SESSION_RE.match(session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id")

    graph = request.app.state.tribe_graph
    config = {"configurable": {
        "thread_id": f"tribe-app:{principal.member_id}:{session_id}",
        "member_id": principal.member_id,
    }}

    current = await graph.aget_state(config)
    if current.next:
        # A booking confirmation is pending: this message IS the answer.
        await graph.ainvoke(Command(resume=body.message), config)
    else:
        await graph.ainvoke({"messages": [HumanMessage(body.message)]}, config)

    state = await graph.aget_state(config)
    question = _pending_question(state)
    if question:
        return ChatResponse(reply=question, session_id=session_id, awaiting_confirmation=True)

    reply = ""
    for m in reversed(state.values.get("messages", [])):
        if isinstance(m, AIMessage) and m.content and not m.tool_calls:
            reply = m.content if isinstance(m.content, str) else "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in m.content)
            break
    return ChatResponse(reply=reply.strip(), session_id=session_id)
