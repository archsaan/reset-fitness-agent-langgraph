"""The booking gate, tested end to end through the real graph with a
scripted model and in-memory checkpointer - no database, no network, no
API keys."""

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agents.tribe import Deps, build_graph, is_affirmative

BOOK_ARGS = {"calendar_schedule_id": 42, "class_name": "Yoga", "target_date": "2026-11-12",
             "start_time": "18:00", "lead_name": "Sam"}


class ScriptedModel(BaseChatModel):
    script: list
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _book_call():
    return AIMessage(content="", tool_calls=[{"name": "book_class", "args": BOOK_ARGS, "id": "c1"}])


def _make(script, bookings):
    async def kb(slug, q): return "kb"
    async def persona(slug, default): return default
    async def model_name(slug, default): return default
    async def usage(*a, **k): return None
    async def execute(**kwargs):
        bookings.append(kwargs)
        return {"success": True, "booking": {k: BOOK_ARGS[k] for k in ("class_name", "start_time", "lead_name")}
                | {"date": BOOK_ARGS["target_date"]}}

    deps = Deps(get_model=lambda name: ScriptedModel(script=script), get_kb_context=kb, get_persona=persona,
                get_model_name=model_name, log_usage=usage, execute_booking=execute)
    return build_graph(InMemorySaver(), deps)


def _cfg(member_id=7, thread="t1"):
    return {"configurable": {"thread_id": thread, "member_id": member_id}}


async def _propose(graph, cfg):
    await graph.ainvoke({"messages": [HumanMessage("book yoga tomorrow, I'm Sam")]}, cfg)
    state = await graph.aget_state(cfg)
    assert state.next == ("confirm_booking",)
    return state


async def test_plain_reply_has_no_pending_confirmation():
    graph = _make([AIMessage(content="Hi, I'm Riley!")], [])
    await graph.ainvoke({"messages": [HumanMessage("hello")]}, _cfg())
    assert (await graph.aget_state(_cfg())).next == ()


async def test_nothing_is_booked_before_the_member_confirms():
    bookings = []
    graph = _make([_book_call()], bookings)
    state = await _propose(graph, _cfg())
    assert bookings == []
    assert "Please confirm" in state.tasks[0].interrupts[0].value["question"]


async def test_yes_books_with_the_member_id_from_config():
    bookings = []
    graph = _make([_book_call()], bookings)
    cfg = _cfg(member_id=123)
    await _propose(graph, cfg)
    await graph.ainvoke(Command(resume="yes please"), cfg)
    assert len(bookings) == 1
    assert bookings[0]["member_id"] == 123
    assert bookings[0]["calendar_schedule_id"] == 42
    final = (await graph.aget_state(cfg)).values["messages"][-1]
    assert "You're booked" in final.content


@pytest.mark.parametrize("reply", ["no", "maybe later", "", "what time is it?", "not yet, yes I might"])
async def test_anything_but_a_clear_yes_declines(reply):
    bookings = []
    graph = _make([_book_call()], bookings)
    cfg = _cfg()
    await _propose(graph, cfg)
    await graph.ainvoke(Command(resume=reply), cfg)
    assert bookings == []
    assert "No worries" in (await graph.aget_state(cfg)).values["messages"][-1].content


async def test_history_stays_valid_after_a_gated_turn():
    """The tool call must get a ToolMessage, or the next model call would
    be rejected by the provider."""
    graph = _make([_book_call(), AIMessage(content="Anything else?")], [])
    cfg = _cfg()
    await _propose(graph, cfg)
    await graph.ainvoke(Command(resume="yes"), cfg)
    await graph.ainvoke({"messages": [HumanMessage("thanks")]}, cfg)
    msgs = (await graph.aget_state(cfg)).values["messages"]
    tool_msgs = [m for m in msgs if m.type == "tool"]
    assert [m.tool_call_id for m in tool_msgs] == ["c1"]


async def test_threads_are_isolated():
    bookings = []
    graph = _make([_book_call()], bookings)
    await _propose(graph, _cfg(member_id=1, thread="a"))
    other = await graph.aget_state(_cfg(member_id=2, thread="b"))
    assert other.next == ()


def test_is_affirmative_is_deny_by_default():
    assert is_affirmative("Yes!") and is_affirmative("book it") and is_affirmative("ok sure")
    assert not is_affirmative("") and not is_affirmative("no") and not is_affirmative("nope")
    assert not is_affirmative("not now")
