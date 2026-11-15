"""G1a: the router sees the same last-N window as the answer prompt.

The router prompt used to be built from the whole in-memory buffer, which
grew with every turn of a session, so a long conversation sent far more
history to the LLM than ``LATEST_N_MESSAGES`` suggests.
"""
from __future__ import annotations

from types import SimpleNamespace

from agents.conversation_agent import AgenticConversationHandler, ConversationManager

WINDOW = 3


def _handler():
    """A handler with a fake workflow that records each initial state."""
    seen = []

    class FakeWorkflow:
        def invoke(self, state):
            seen.append(state)
            return {"response": f"answer {len(seen)}"}

    h = AgenticConversationHandler.__new__(AgenticConversationHandler)
    h.conversation_manager = ConversationManager("b", "u", "s", {}, WINDOW)
    h.slot_manager = SimpleNamespace(get_filled_slots=lambda topic: {})
    h.workflow = FakeWorkflow()
    return h, seen


def test_router_history_is_capped_at_the_window():
    h, seen = _handler()
    for i in range(1, 21):
        h.process_user_input(f"question {i}")

    history = seen[-1]["global_history"].splitlines()
    assert len(history) == WINDOW * 2
    assert history[0] == "User: question 17"
    assert history[-1] == "Assistant: answer 19"


def test_buffers_stay_bounded():
    h, _ = _handler()
    for i in range(20):
        h.process_user_input(f"question {i}")
    assert len(h.conversation_manager.global_memory.chat_memory.messages) == WINDOW * 2


def test_remember_turn_with_a_zero_window_keeps_nothing():
    m = ConversationManager("b", "u", "s", {}, 0)
    ConversationManager.remember_turn(m.global_memory, "q", "a")
    assert m.global_memory.chat_memory.messages == []
    assert m.global_history_text() == ""
