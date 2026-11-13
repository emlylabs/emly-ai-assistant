from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class Message(BaseModel):
    role: str
    content: str


class AgentRequest(BaseModel):
    user_id: str
    session_id: str
    timestamp: int
    messages: List[Message]
    options: Dict[str, Any] = {}
    stream: bool = True
    keep_alive: int = 1
    page_id: Optional[str] = None
    bot_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
