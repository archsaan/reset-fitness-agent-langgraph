"""Riley (tribe-app) as a LangGraph graph.

    START -> prepare -> agent -+-> END                (plain reply)
                 ^             |
                 |             +-> tools -> agent     (read-only tools)
                 |             |
                 |             +-> confirm_booking -> END
                 |
    prepare: injection log, KB retrieval, rolling summary
    confirm_booking: interrupt() asks the member; their NEXT message is the
        resume value. yes/no is a deterministic keyword check, never an LLM
        call. Only a "yes" reaches execute_booking, with the member_id from
        the verified JWT (config["configurable"]["member_id"]).

State is persisted by the Postgres checkpointer, so a pending confirmation
survives restarts and can be resumed by any instance.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Any, Callable

from langchain_core.messages import (
    AIMessage, AnyMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt
from typing_extensions import TypedDict

from app import agent_config, kb
from app.config import (
    ROLLING_SUMMARY_RECENT_MESSAGES, ROLLING_SUMMARY_TRIGGER_MESSAGES, TRIBE_APP_MODEL,
)
from app.prompts import RULES, SUMMARY_PROMPT, TRIBE_APP_PERSONA
from app.safety import log_if_injection_attempt
from app.tools.booking import book_class, execute_booking
from app.tools.schedule import check_availability, get_schedule

logger = logging.getLogger("reset_fitness.tribe")

AGENT_SLUG = "tribe-app"
READ_TOOLS = [get_schedule, check_availability]
ALL_TOOLS = READ_TOOLS + [book_class]

# Deny-by-default, same wordlists as the original: an ambiguous reply must
# never resolve to "go ahead" on a real booking.
_YES_PHRASES = {
    "yes", "y", "yeah", "yep", "yup", "confirm", "confirmed", "sure", "ok", "okay",
    "correct", "that's right", "sounds good", "go ahead", "book it", "please book it",
    "confirm it", "yes please", "yes book it",
}
_YES_FIRST_WORDS = {"yes", "y", "yeah", "yep", "yup", "confirm", "confirmed", "sure", "ok", "okay", "correct"}


def is_affirmative(reply: str) -> bool:
    normalized = (reply or "").strip().lower().rstrip(".!")
    if not normalized:
        return False
    return normalized in _YES_PHRASES or normalized.split()[0] in _YES_FIRST_WORDS


def format_booking_result(result: dict) -> str:
    if result.get("success"):
        b = result["booking"]
        return (f"You're booked! {b['class_name']} on {b['date']} at {b['start_time']} "
                f"for {b['lead_name']}. See you there! \U0001F389")
    if result.get("declined"):
        return "No worries! Let me know if you'd like to book something else. \U0001F60A"
    return f"I couldn't complete that booking: {result.get('reason', 'unknown error')}"


class State(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    kb_context: str
    summary: str


def _text(msg) -> str:
    c = msg.content
    if isinstance(c, str):
        return c
    return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)


@dataclass
class Deps:
    """Everything the graph needs from the outside world. Tests swap these."""
    get_model: Callable[[str], Any]
    get_kb_context: Callable = kb.get_kb_context
    get_persona: Callable = agent_config.get_system_prompt
    get_model_name: Callable = agent_config.get_agent_model
    log_usage: Callable = kb.log_usage
    execute_booking: Callable = execute_booking
    today: Callable[[], date] = date.today


def default_model_factory(model_name: str):
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(model=model_name.removeprefix("gemini/"), temperature=0.3)


def _safe_cut(messages: list, recent: int) -> int:
    """Index to cut at so the kept tail starts on a HumanMessage - never
    between an AI tool call and its ToolMessage."""
    i = len(messages) - recent
    while i < len(messages) and not isinstance(messages[i], HumanMessage):
        i += 1
    return i if i < len(messages) else 0


def build_graph(checkpointer, deps: Deps | None = None):
    deps = deps or Deps(get_model=default_model_factory)

    async def prepare(state: State, config: RunnableConfig) -> dict:
        msgs = state["messages"]
        last_human = next((m for m in reversed(msgs) if isinstance(m, HumanMessage)), None)
        question = _text(last_human) if last_human else ""
        thread_id = config["configurable"].get("thread_id", "unknown")
        log_if_injection_attempt(AGENT_SLUG, thread_id, question)

        update: dict = {"kb_context": await deps.get_kb_context(AGENT_SLUG, question)}

        if len(msgs) > ROLLING_SUMMARY_TRIGGER_MESSAGES:
            cut = _safe_cut(msgs, ROLLING_SUMMARY_RECENT_MESSAGES)
            if cut > 0:
                old = msgs[:cut]
                try:
                    update["summary"] = await _summarize(state.get("summary", ""), old)
                    update["messages"] = [RemoveMessage(id=m.id) for m in old if m.id]
                except Exception:
                    logger.exception("Summarization failed; keeping full history this turn")
        return update

    async def _summarize(previous: str, old: list) -> str:
        transcript = "\n".join(
            f"{'Member' if isinstance(m, HumanMessage) else 'Assistant'}: {_text(m)}"
            for m in old if isinstance(m, (HumanMessage, AIMessage)) and _text(m))
        prev = f"Earlier summary: {previous}\n\n" if previous else ""
        model = deps.get_model(TRIBE_APP_MODEL)
        resp = await model.ainvoke([HumanMessage(SUMMARY_PROMPT.format(previous=prev, transcript=transcript))])
        return _text(resp).strip()

    async def agent(state: State, config: RunnableConfig) -> dict:
        persona = await deps.get_persona(AGENT_SLUG, TRIBE_APP_PERSONA)
        system = (
            f"{persona}\n\nToday's date is {deps.today().isoformat()}."
            f"\n\n--- KNOWLEDGE BASE ---\n{state.get('kb_context', '')}"
            + (f"\n\n--- EARLIER IN THIS CONVERSATION ---\n{state['summary']}" if state.get("summary") else "")
            + f"\n\n--- RULES ---\n{RULES}"
        )
        model_name = await deps.get_model_name(AGENT_SLUG, TRIBE_APP_MODEL)
        model = deps.get_model(model_name).bind_tools(ALL_TOOLS)
        resp = await model.ainvoke([SystemMessage(system)] + state["messages"])

        usage = getattr(resp, "usage_metadata", None) or {}
        await deps.log_usage(
            AGENT_SLUG, config["configurable"].get("thread_id", "unknown"), model_name,
            usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        return {"messages": [resp]}

    def route_after_agent(state: State) -> str:
        last = state["messages"][-1]
        calls = getattr(last, "tool_calls", None) or []
        if not calls:
            return END
        if any(c["name"] == "book_class" for c in calls):
            return "confirm_booking"
        return "tools"

    async def confirm_booking(state: State, config: RunnableConfig) -> dict:
        last = state["messages"][-1]
        calls = last.tool_calls
        book = next(c for c in calls if c["name"] == "book_class")
        args = book["args"]

        # Nothing with side effects happens before interrupt(): on resume
        # LangGraph re-runs this node from the top.
        reply = interrupt({
            "type": "confirm_booking",
            "question": (f"Please confirm: book {args.get('class_name')} on {args.get('target_date')} "
                         f"at {args.get('start_time')} for {args.get('lead_name')}? (yes/no)"),
            "booking": args,
        })

        if is_affirmative(reply if isinstance(reply, str) else ""):
            member_id = config["configurable"]["member_id"]  # from the verified JWT
            try:
                result = await deps.execute_booking(member_id=member_id, **args)
            except Exception:
                logger.exception("execute_booking raised member=%s args=%s", member_id, args)
                result = {"success": False, "reason": "Something went wrong reaching the booking system. Please try again in a moment."}
        else:
            result = {"success": False, "declined": True}

        # Every tool call needs a ToolMessage or the history becomes invalid.
        tool_msgs = [
            ToolMessage(
                content=json.dumps(result) if c["id"] == book["id"]
                else "Skipped - book_class must be called on its own.",
                tool_call_id=c["id"], name=c["name"])
            for c in calls
        ]
        return {"messages": tool_msgs + [AIMessage(content=format_booking_result(result))]}

    g = StateGraph(State)
    g.add_node("prepare", prepare)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode(READ_TOOLS))
    g.add_node("confirm_booking", confirm_booking)
    g.add_edge(START, "prepare")
    g.add_edge("prepare", "agent")
    g.add_conditional_edges("agent", route_after_agent, ["tools", "confirm_booking", END])
    g.add_edge("tools", "agent")
    g.add_edge("confirm_booking", END)
    return g.compile(checkpointer=checkpointer)
