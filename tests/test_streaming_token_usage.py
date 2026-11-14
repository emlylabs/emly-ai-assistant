"""N2: streamed answers record token usage.

The widget streams every answer. The streaming chain ended in
StrOutputParser, which drops ``usage_metadata``, so every streamed
assistant row was stored with no token counts and the dashboard showed
zero spend and zero tokens for widget traffic.
"""
from __future__ import annotations

import uuid
from typing import Any, Iterator, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from agents.conversation_agent import (
    AgenticConversationHandler,
    PromptManager,
    TopicConfig,
    _add_stream_usage,
)


class StreamingFakeLLM(BaseChatModel):
    """Streams fixed tokens, then a content-free chunk carrying usage,
    the way OpenAI does with ``stream_options.include_usage``."""

    tokens: List[str] = ["Hello", " there"]
    model_name: str = "fake-model"

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, **kw: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="".join(self.tokens)))])

    def _stream(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, **kw: Any) -> Iterator[ChatGenerationChunk]:
        for t in self.tokens:
            yield ChatGenerationChunk(message=AIMessageChunk(content=t))
        yield ChatGenerationChunk(message=AIMessageChunk(
            content="", usage_metadata={"input_tokens": 42, "output_tokens": 7, "total_tokens": 49}))


class NoRAG:
    def should_use_rag(self, topic):
        return False


def test_streamed_answer_stores_token_counts(isolated_db):
    from models.bots import Bots
    from models.emly_messages import EMLYMessages

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"st-{uuid.uuid4().hex[:6]}", name="S", config_json={})
    topics = {"general": TopicConfig(name="general", description="chat", skip_slot_filling=True)}
    handler = AgenticConversationHandler(
        config={}, user_id="v1", session_id="s1", llm=StreamingFakeLLM(), embeddings=None, topics=topics,
        prompt_manager=PromptManager({"llm_response": "Answer: {user_input}"}, topics),
        intent_router=None, rag_manager=NoRAG(), bot_id=bot.id,
    )

    events = list(handler.process_user_input("hi", stream=True))

    assert "".join(e["data"] for e in events if e["type"] == "token") == "Hello there"
    (row,) = [m for m in EMLYMessages.list_all(bot.id, session_id="s1") if m.role == "assistant"]
    assert (row.prompt_tokens, row.completion_tokens) == (42, 7)
    assert row.model_used == "fake-model"


def test_usage_split_across_chunks_is_summed():
    tele = {"prompt_tokens": None, "completion_tokens": None}
    _add_stream_usage(tele, None)
    _add_stream_usage(tele, {"input_tokens": 10, "output_tokens": 0})
    _add_stream_usage(tele, {"input_tokens": 0, "output_tokens": 5})
    assert tele == {"prompt_tokens": 10, "completion_tokens": 5}
