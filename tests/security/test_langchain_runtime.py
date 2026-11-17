"""F4: LangChain/LangGraph upgrade guards.

The product promise is that conversation data only goes to the customer's
model endpoint. LangSmith tracing would send prompts and replies to
LangChain's cloud, so it must stay off unless an operator turns it on.
"""

from __future__ import annotations

import pytest

TRACING_VARS = ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2",
                "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY", "LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT")


@pytest.fixture
def clean_env(monkeypatch):
    for var in TRACING_VARS:
        monkeypatch.delenv(var, raising=False)
    from langsmith import utils

    utils.get_env_var.cache_clear()
    yield
    utils.get_env_var.cache_clear()


def test_langsmith_tracing_off_by_default(clean_env):
    from langsmith import utils

    assert utils.tracing_is_enabled() is False


def test_nothing_in_the_app_turns_tracing_on(clean_env):
    import os

    import agents.conversation_agent  # noqa: F401  (imports LangChain/LangGraph)
    import main  # noqa: F401

    assert not any(os.environ.get(v) for v in TRACING_VARS)
    from langsmith import utils

    assert utils.tracing_is_enabled() is False
