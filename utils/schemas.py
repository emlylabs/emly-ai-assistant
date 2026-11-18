from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

# S2: one visitor message. Long enough for a pasted document excerpt; a
# multi-megabyte prompt would only burn the bot's token cap.
MAX_MESSAGE_CHARS = 8000


class Message(BaseModel):
    role: str = Field(max_length=32)
    content: str = Field(max_length=MAX_MESSAGE_CHARS)


class AgentRequest(BaseModel):
    # The chat route fills these from the X-Emly-* headers when the body
    # leaves them out, and mints a session id if neither has one.
    user_id: Optional[str] = Field(default=None, max_length=128)
    session_id: Optional[str] = Field(default=None, max_length=128)
    timestamp: Optional[int] = None
    messages: List[Message] = Field(max_length=50)
    options: Dict[str, Any] = {}
    stream: bool = True
    keep_alive: int = 1
    page_id: Optional[str] = None
    bot_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
