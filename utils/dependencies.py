"""Process-singleton accessors for the agent / data services."""
from __future__ import annotations

from services.agent_service import AgentService
from services.data_service import DataService

DATA_SERVICE_INSTANCE: DataService = DataService()
AGENT_SERVICE_INSTANCE: AgentService = AgentService()


def get_agent_service() -> AgentService:
    return AGENT_SERVICE_INSTANCE


def invalidate_agent_service(bot_id: str) -> AgentService:
    """Drop cached state for ``bot_id`` so the next request rebuilds."""
    AGENT_SERVICE_INSTANCE.invalidate_bot(bot_id)
    return AGENT_SERVICE_INSTANCE
