from pydantic import BaseModel, ConfigDict
from typing import Optional, Any, List, Dict


class Message(BaseModel):
    role: str
    content: str


class AgentRequest(BaseModel):
    """
    Request model for agent requests
    """
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


class FileModel(BaseModel):
    file_name: str
    extension: str


class FileDeleteRequest(BaseModel):
    files: List[FileModel]
